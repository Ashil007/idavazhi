"""Search for places: every named place in the app's own Kerala map.

The online place search (Nominatim) finds what OpenStreetMap has an address
for, which leaves out many everyday destinations. The Kerala map tiles
(tools/build_map.py) already hold every named thing in OpenStreetMap there -
shops and malls, schools, temples, churches, mosques, hospitals, offices, bus
stops and stations, streets, villages and neighbourhoods. This module reads
those names out of the tiles once into a small SQLite full-text index
(.cache/places/places.db) and searches it as the user types.

Each place records the nearest town or village, so "Lulu Mall" is shown as
"Shopping mall · Edappally, Kochi" rather than as a bare name.

Built in the background at server start when the map tiles are newer than
the index; `python -m transitnav.places` builds it by hand.
"""

from __future__ import annotations

import gzip
import math
import os
import re
import sqlite3
import threading
import time

from . import basemap
from .geo import haversine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, ".cache", "places", "places.db")
ZOOM = 14                        # the most detailed tiles hold every named object

# What a place is, by its tile layer and class: (label shown, search weight).
# Weight favours what people travel to: towns and stations over street names.
PLACE_KINDS = {
    "city": ("City", 9), "town": ("Town", 8), "village": ("Village", 6),
    "suburb": ("Area", 6), "neighbourhood": ("Area", 5), "quarter": ("Area", 5),
    "hamlet": ("Place", 4), "isolated_dwelling": ("Place", 2), "locality": ("Place", 3),
}
POI_LABELS = {
    "railway": "Station", "bus": "Bus stop", "shop": "Shop", "grocery": "Shop",
    "clothing_store": "Shop", "fast_food": "Restaurant", "restaurant": "Restaurant",
    "cafe": "Cafe", "hospital": "Hospital", "pharmacy": "Pharmacy", "school": "School",
    "college": "College", "town_hall": "Office", "place_of_worship": "Place of worship",
    "bank": "Bank", "fuel": "Petrol pump", "lodging": "Hotel", "attraction": "Attraction",
    "park": "Park", "stadium": "Stadium", "cinema": "Cinema", "library": "Library",
    "post": "Post office", "police": "Police", "airport": "Airport", "harbor": "Harbour",
}
POI_WEIGHT = {"railway": 8, "bus": 4, "hospital": 6, "college": 6, "school": 4,
              "shop": 5, "attraction": 6, "airport": 9, "stadium": 6, "town_hall": 5}
SUBCLASS_LABELS = {"mall": "Shopping mall", "bus_station": "Bus stand", "station": "Station",
                   "supermarket": "Supermarket", "university": "University"}
SUBCLASS_WEIGHT = {"mall": 8, "bus_station": 8, "university": 7}

_lock = threading.Lock()
_building = {"state": "idle"}


# ------------------------------------------------------------ vector tiles
# A Mapbox vector tile is a small protobuf; reading the few fields needed
# takes a page of code instead of a dependency.

def _varint(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, i
        shift += 7


def _fields(buf):
    """(field number, wire type, value) for each field in a protobuf message."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _varint(buf, i)
        elif wire == 2:
            ln, i = _varint(buf, i)
            val = buf[i:i + ln]
            i += ln
        elif wire == 1:
            val = buf[i:i + 8]
            i += 8
        elif wire == 5:
            val = buf[i:i + 4]
            i += 4
        else:
            raise ValueError("unsupported wire type %d" % wire)
        yield field, wire, val


def _packed(buf):
    out, i = [], 0
    while i < len(buf):
        v, i = _varint(buf, i)
        out.append(v)
    return out


def _value(buf):
    import struct
    for field, wire, val in _fields(buf):
        if field == 1:
            return val.decode("utf-8", "replace")
        if field == 2:
            return struct.unpack("<f", val)[0]
        if field == 3:
            return struct.unpack("<d", val)[0]
        if field in (4, 5):
            return val
        if field == 6:
            return (val >> 1) ^ -(val & 1)
        if field == 7:
            return bool(val)
    return None


def _first_point(geometry):
    """A representative point of a feature, in tile units: the first vertex
    of a point, or the middle vertex of a line or ring."""
    pts, x, y, i = [], 0, 0, 0
    while i < len(geometry):
        cmd = geometry[i]
        i += 1
        op, count = cmd & 7, cmd >> 3
        if op in (1, 2):
            for _ in range(count):
                dx, dy = geometry[i], geometry[i + 1]
                i += 2
                x += (dx >> 1) ^ -(dx & 1)
                y += (dy >> 1) ^ -(dy & 1)
                pts.append((x, y))
        if len(pts) > 64:
            break
    return pts[len(pts) // 2] if pts else None


def _layers(tile_bytes):
    """{layer name: [(properties dict, (x, y)), ...], "_extent": {name: extent}}."""
    if tile_bytes[:2] == b"\x1f\x8b":
        tile_bytes = gzip.decompress(tile_bytes)
    out = {}
    for field, _, layer in _fields(tile_bytes):
        if field != 3:
            continue
        name, keys, values, feats, extent = None, [], [], [], 4096
        for f, _, v in _fields(layer):
            if f == 1:
                name = v.decode("utf-8", "replace")
            elif f == 2:
                feats.append(v)
            elif f == 3:
                keys.append(v.decode("utf-8", "replace"))
            elif f == 4:
                values.append(_value(v))
            elif f == 5:
                extent = v
        rows = []
        for feat in feats:
            tags, geom = [], []
            for f, wire, v in _fields(feat):
                if f == 2:
                    tags = _packed(v) if wire == 2 else [v]
                elif f == 4:
                    geom = _packed(v) if wire == 2 else [v]
            props = {keys[tags[k]]: values[tags[k + 1]] for k in range(0, len(tags) - 1, 2)
                     if tags[k] < len(keys) and tags[k + 1] < len(values)}
            pt = _first_point(geom)
            if pt is not None:
                rows.append((props, pt, extent))
        out[name] = rows
    return out


def _lat_lon(z, tx, ty, px, py, extent):
    n = 1 << z
    lon = (tx + px / extent) / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + py / extent) / n))))
    return lat, lon


# ------------------------------------------------------------------ build

def _names(props):
    """Main name (English where the main one is in Malayalam), and every
    other spelling worth matching."""
    names = [props.get(k) for k in ("name_en", "name:latin", "name", "name_int", "name:ml")]
    names = [str(n).strip() for n in names if n and str(n).strip()]
    if not names:
        return None, ""
    latin = [n for n in names if re.search(r"[A-Za-z]", n)]
    main = latin[0] if latin else names[0]
    others = " ".join(sorted(set(names) - {main}))
    return main, others


def build(log=print):
    """Read every named feature out of the newest map tiles into the index."""
    path = basemap.newest_build()
    if path is None:
        log("  place search: no map tiles yet (run tools/build_map.py)")
        return False
    started = time.time()
    with _lock:
        _building["state"] = "running"
    src = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    seen, places = set(), []
    count = 0
    for col, row, data in src.execute(
            "SELECT tile_column, tile_row, tile_data FROM tiles WHERE zoom_level=?", (ZOOM,)):
        ty = (1 << ZOOM) - 1 - row
        count += 1
        try:
            layers = _layers(bytes(data))
        except Exception:  # noqa: BLE001 - one bad tile must not stop the index
            continue
        for layer, rows in layers.items():
            if layer not in ("place", "poi", "transportation_name", "aerodrome_label", "water_name"):
                continue
            for props, (px, py), extent in rows:
                main, others = _names(props)
                if not main:
                    continue
                cls = str(props.get("class") or "")
                sub = str(props.get("subclass") or "")
                if layer == "place":
                    label, weight = PLACE_KINDS.get(cls, ("Place", 3))
                elif layer == "poi":
                    label = SUBCLASS_LABELS.get(sub) or POI_LABELS.get(cls) or \
                        (sub or cls or "Place").replace("_", " ").capitalize()
                    weight = SUBCLASS_WEIGHT.get(sub) or POI_WEIGHT.get(cls, 4)
                elif layer == "aerodrome_label":
                    label, weight = "Airport", 9
                elif layer == "water_name":
                    label, weight = "Water", 2
                else:
                    label, weight = "Street", 2
                lat, lon = _lat_lon(ZOOM, col, ty, px, py, extent)
                # Streets and big features repeat in every tile they cross:
                # keep one per name, kind and ~1 km square.
                key = (main.lower(), label, round(lat, 2), round(lon, 2))
                if key in seen:
                    continue
                seen.add(key)
                places.append([main, others, label, layer, weight, lat, lon, ""])
    src.close()

    # The nearest town or village, for the second line of each suggestion.
    towns = [p for p in places if p[3] == "place" and p[2] in ("City", "Town", "Village", "Area")]
    grid = {}
    for t in towns:
        grid.setdefault((int(t[5] * 20), int(t[6] * 20)), []).append(t)
    for p in places:
        gy, gx = int(p[5] * 20), int(p[6] * 20)
        best, best_d = None, 15000.0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for t in grid.get((gy + dy, gx + dx), ()):
                    if t is p:
                        continue
                    d = haversine(p[5], p[6], t[5], t[6])
                    if d < best_d:
                        best, best_d = t, d
        if best is not None:
            p[7] = best[0]

    os.makedirs(os.path.dirname(DB), exist_ok=True)
    tmp = DB + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("CREATE TABLE places (id INTEGER PRIMARY KEY, name TEXT, alt TEXT, kind TEXT, "
               "layer TEXT, weight INTEGER, lat REAL, lon REAL, near TEXT)")
    db.execute("CREATE VIRTUAL TABLE search USING fts5(name, alt, near, content='places', "
               "content_rowid='id', tokenize='unicode61 remove_diacritics 2', prefix='1 2 3')")
    db.executemany("INSERT INTO places (name, alt, kind, layer, weight, lat, lon, near) "
                   "VALUES (?,?,?,?,?,?,?,?)", places)
    db.execute("INSERT INTO search (rowid, name, alt, near) SELECT id, name, alt, near FROM places")
    db.execute("INSERT INTO meta VALUES ('source', ?), ('built', ?)",
               (os.path.basename(path), str(time.time())))
    db.commit()
    db.close()
    os.replace(tmp, DB)
    with _lock:
        _building["state"] = "done"
    log("  place search: indexed %s named places from %s map tiles in %.0f s"
        % (format(len(places), ","), format(count, ","), time.time() - started))
    return True


def is_stale():
    """True when there is no index, or the map tiles are newer than it."""
    tiles = basemap.newest_build()
    if tiles is None:
        return False
    try:
        return os.path.getmtime(DB) < os.path.getmtime(tiles)
    except OSError:
        return True


# ----------------------------------------------------------------- search

def _query(text):
    """'lulu mal' -> 'lulu* AND mal*' (every word, as a prefix)."""
    words = re.findall(r"\w+", text.lower())
    return " AND ".join('"%s"*' % w for w in words) if words else None


def search(text, near=None, limit=8):
    """Best matches for what the user has typed so far, nearest and most
    useful first. `near` (lat, lon) is where the user is or is looking."""
    q = _query(text)
    if not q or not os.path.exists(DB):
        return []
    db = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    try:
        rows = db.execute(
            "SELECT p.name, p.kind, p.weight, p.lat, p.lon, p.near, bm25(search, 10.0, 2.0, 1.0) "
            "FROM search JOIN places p ON p.id = search.rowid "
            "WHERE search MATCH ? ORDER BY bm25(search, 10.0, 2.0, 1.0) LIMIT 400", (q,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    typed = " ".join(re.findall(r"\w+", text.lower()))
    scored = []
    for name, kind, weight, lat, lon, place_near, rank in rows:
        low = name.lower()
        score = weight * 2.0 - rank                      # bm25: lower is better
        if low == typed:
            score += 20
        elif low.startswith(typed):
            score += 14
        elif typed in low:
            score += 6
        if near:
            # Nearby wins: the Lulu Mall in your city before the one 170 km away.
            km = haversine(near[0], near[1], lat, lon) / 1000.0
            score -= 9.0 * math.log1p(km / 3.0)
        scored.append((score, name, kind, lat, lon, place_near))
    scored.sort(key=lambda s: -s[0])
    out, shown = [], set()
    for score, name, kind, lat, lon, place_near in scored:
        key = (name.lower(), kind, place_near)
        if key in shown:
            continue
        shown.add(key)
        detail = kind + (" · near " + place_near if place_near and place_near != name else "")
        out.append({"name": name, "detail": detail, "lat": lat, "lon": lon, "source": "local"})
        if len(out) >= limit:
            break
    return out


def status():
    with _lock:
        return dict(_building)


def build_in_background(log=print):
    if not is_stale():
        return None
    def work():
        try:
            build(log)
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _building["state"] = "failed"
            log("  place search: build failed (%s)" % exc)
    th = threading.Thread(target=work, name="places", daemon=True)
    th.start()
    return th


if __name__ == "__main__":
    build()
