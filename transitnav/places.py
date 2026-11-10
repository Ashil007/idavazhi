"""Search for places: every named place in Kerala's OpenStreetMap data.

The online place search (Nominatim) finds what OpenStreetMap has an address
for, which leaves out many everyday destinations. The Kerala map tiles
(tools/build_map.py) already hold every named thing in OpenStreetMap there -
shops and malls, schools, temples, churches, mosques, hospitals, offices, bus
stops and stations, streets, villages and neighbourhoods. This module reads
those names once into a small SQLite full-text index
(.cache/places/places.db) and searches it as the user types.

Where the names come from: the full Kerala OpenStreetMap file
(.cache/osm/kerala-latest.osm.pbf, read with pyosmium) when it is there -
every named node and way, including the small ones the map tiles leave out
(a village's post office, a junction's bus stop). Without it, the map tiles.

Spelling: Malayalam names are written in English many ways - Kuthiathode,
Kuthiyathode, Kuthiyathodu; Kozhikode, Kozhikkode. Every name also gets a
sound-alike key (_sound) that is the same for all of these, and the search
looks in both: exact spellings first, sound-alikes after.

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
PBF = os.path.join(ROOT, ".cache", "osm", "kerala-latest.osm.pbf")
INDEX_VERSION = "2"             # bump to rebuild old indexes (2: OSM file + sound keys)
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


# ------------------------------------------------------- spelling

_SOUNDS = [("zh", "l"), ("th", "t"), ("dh", "d"), ("kh", "k"), ("gh", "g"), ("bh", "b"),
           ("ph", "f"), ("sh", "s"), ("ch", "c"), ("c", "k"), ("q", "k"), ("w", "v"),
           ("ee", "i"), ("oo", "u"), ("iy", "i"), ("ey", "e"), ("ay", "a"), ("z", "s")]


def _sound(word):
    """One word as it sounds, so English spellings of the same Malayalam name
    agree: kuthiathode, kuthiyathodu -> kutiatod; kozhikkode -> kolikod."""
    w = re.sub(r"[^a-z]", "", word.lower())
    for a, b in _SOUNDS:
        w = w.replace(a, b)
    w = re.sub(r"(.)\1+", r"\1", w)             # kk -> k, aa -> a
    if len(w) > 3 and w[-1] in "aeiou":           # -odu / -ode / -oda alike
        w = w[:-1]
    return w


def _sound_key(*texts):
    words = []
    for t in texts:
        words += re.findall(r"[A-Za-z]+", t or "")
    keys = []
    for w in words:
        k = _sound(w)
        if k and k not in keys:
            keys.append(k)
    return " ".join(keys)


# ------------------------------------------------------ OSM file
# What a named OSM object is: (label shown, search weight), from its tags.

AMENITY = {
    "hospital": ("Hospital", 6), "clinic": ("Clinic", 4), "doctors": ("Clinic", 3),
    "pharmacy": ("Pharmacy", 4), "school": ("School", 4), "college": ("College", 6),
    "university": ("University", 7), "place_of_worship": ("Place of worship", 4),
    "bank": ("Bank", 4), "atm": ("ATM", 2), "fuel": ("Petrol pump", 3), "police": ("Police", 4),
    "post_office": ("Post office", 4), "townhall": ("Office", 5), "library": ("Library", 3),
    "cinema": ("Cinema", 5), "restaurant": ("Restaurant", 4), "cafe": ("Cafe", 3),
    "fast_food": ("Restaurant", 3), "marketplace": ("Market", 5), "community_centre": ("Hall", 3),
    "courthouse": ("Court", 4), "ferry_terminal": ("Boat jetty", 6), "bus_station": ("Bus stand", 8),
    "kindergarten": ("School", 2), "childcare": ("Anganwadi", 2), "veterinary": ("Vet", 3),
    "taxi": ("Taxi stand", 3), "auditorium": ("Hall", 4), "conference_centre": ("Hall", 4),
    "parking": ("Parking", 1), "toilets": ("Toilet", 1), "parking_entrance": ("Parking", 1),
    "waste_basket": None, "bench": None, "drinking_water": ("Drinking water", 1),
}
TOURISM = {"hotel": ("Hotel", 4), "guest_house": ("Hotel", 3), "resort": ("Resort", 4),
           "attraction": ("Attraction", 6), "viewpoint": ("Viewpoint", 5),
           "museum": ("Museum", 6), "zoo": ("Zoo", 6), "theme_park": ("Theme park", 6)}
LEISURE = {"park": ("Park", 4), "stadium": ("Stadium", 6), "sports_centre": ("Sports ground", 3),
           "pitch": ("Sports ground", 2), "beach_resort": ("Beach", 5), "playground": ("Park", 2)}
ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
         "service", "living_street", "road", "track", "pedestrian", "footway", "path"}
NAME_TAGS = ("name:en", "name", "name:latin", "int_name", "name:ml", "alt_name", "old_name",
             "loc_name", "official_name", "short_name", "alt_name:en", "name:ml-Latn")


def _osm_kind(t):
    g = t.get
    if g("place"):
        return PLACE_KINDS.get(g("place"), ("Place", 3))
    if g("aeroway") == "aerodrome":
        return "Airport", 9
    if g("railway") in ("station", "halt") or (g("public_transport") == "station" and g("train") == "yes"):
        return ("Metro station", 8) if g("station") in ("subway", "light_rail") else ("Station", 8)
    if g("amenity") == "bus_station":
        return "Bus stand", 8
    if g("highway") == "bus_stop" or (g("public_transport") == "platform" and g("bus") == "yes"):
        return "Bus stop", 4
    if g("amenity"):
        return AMENITY.get(g("amenity"), (g("amenity").replace("_", " ").capitalize(), 3))
    if g("shop"):
        return {"mall": ("Shopping mall", 8), "supermarket": ("Supermarket", 5)}.get(g("shop"), ("Shop", 4))
    if g("tourism"):
        return TOURISM.get(g("tourism"), ("Place", 3))
    if g("leisure"):
        return LEISURE.get(g("leisure"), ("Place", 3))
    if g("office") or g("government"):
        return "Office", 4
    if g("historic"):
        return "Attraction", 4
    if g("natural") == "beach":
        return "Beach", 5
    if g("natural") in ("water", "bay") or g("waterway") in ("river", "canal") or g("water"):
        return "Water", 2
    if g("highway") in ROADS or g("man_made") == "bridge" or g("bridge"):
        return "Street", 2
    if g("landuse") in ("residential", "retail", "commercial"):
        return "Area", 3
    if g("building") in ("house", "residential", "detached", "hut"):
        return None                               # people's homes: not destinations
    if g("boundary") or g("route") or g("type") in ("route", "multipolygon", "boundary"):
        return None
    return "Place", 2


def _osm_names(t):
    names = []
    for k in NAME_TAGS:
        for v in (t.get(k) or "").split(";"):
            v = v.strip()
            if v and v not in names:
                names.append(v)
    if not names:
        return None, ""
    latin = [n for n in names if re.search(r"[A-Za-z]", n)]
    main = latin[0] if latin else names[0]
    return main, " ".join(n for n in names if n != main)


def _from_pbf(log):
    """Every named node and way in the Kerala OSM file."""
    import osmium                                  # optional: pip install osmium
    seen, places = set(), []
    fp = osmium.FileProcessor(PBF, osmium.osm.NODE | osmium.osm.WAY).with_locations()
    for o in fp:
        tags = o.tags
        if "name" not in tags and "name:en" not in tags and "name:ml" not in tags:
            continue
        t = {k: v for k, v in tags}
        kind = _osm_kind(t)
        if kind is None:
            continue
        main, others = _osm_names(t)
        if not main:
            continue
        try:
            if o.is_node():
                lat, lon = o.location.lat, o.location.lon
            else:
                nodes = o.nodes
                mid = nodes[len(nodes) // 2]
                if not mid.location.valid():
                    continue
                lat, lon = mid.location.lat, mid.location.lon
        except Exception:  # noqa: BLE001 - a way cut at the file's edge
            continue
        label, weight = kind
        key = (main.lower(), label, round(lat, 2), round(lon, 2))
        if key in seen:
            continue
        seen.add(key)
        layer = "place" if t.get("place") else "osm"
        places.append([main, others, label, layer, weight, lat, lon, ""])
    return places, "kerala-latest.osm.pbf"


def build(log=print):
    """Read every named place into the index: from the OSM file when there is
    one (and pyosmium is installed), else from the newest map tiles."""
    started = time.time()
    with _lock:
        _building["state"] = "running"
    places = None
    if os.path.exists(PBF):
        try:
            places, source = _from_pbf(log)
        except ImportError:
            log("  place search: pyosmium not installed (pip install osmium) - "
                "using the map tiles, which hold fewer names")
    if places is None:
        places, source = _from_tiles(log)
        if places is None:
            with _lock:
                _building["state"] = "idle"
            return False
    elif basemap.newest_build():
        # The tiles also name big areas the file maps as relations (campuses,
        # temple grounds): add what the file did not already give.
        tiles, tile_source = _from_tiles(log)
        have = set((p[0].lower(), round(p[5], 2), round(p[6], 2)) for p in places)
        extra = [p for p in tiles or []
                 if (p[0].lower(), round(p[5], 2), round(p[6], 2)) not in have]
        places += extra
        source += " + %s more from %s" % (format(len(extra), ","), tile_source)
    return _write(places, source, started, log)


def _from_tiles(log):
    """Every named feature in the newest map tiles."""
    path = basemap.newest_build()
    if path is None:
        log("  place search: no map tiles yet (run tools/build_map.py)")
        return None, None
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
    log("  place search: read %s map tiles" % format(count, ","))
    return places, os.path.basename(path)


def _write(places, source, started, log):
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
    # Sound-alike keys, for other spellings of the same name.
    db.execute("CREATE VIRTUAL TABLE sound USING fts5(key, tokenize='unicode61', prefix='2 3 4')")
    db.executemany("INSERT INTO sound (rowid, key) VALUES (?, ?)",
                   ((i, _sound_key(n, a)) for i, n, a in
                    db.execute("SELECT id, name, alt FROM places").fetchall()))
    db.execute("INSERT INTO meta VALUES ('source', ?), ('built', ?), ('version', ?)",
               (source, str(time.time()), INDEX_VERSION))
    db.commit()
    db.close()
    os.replace(tmp, DB)
    with _lock:
        _building["state"] = "done"
    log("  place search: indexed %s named places from %s in %.0f s"
        % (format(len(places), ","), source, time.time() - started))
    return True


def _version():
    try:
        db = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
        try:
            row = db.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        finally:
            db.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def is_stale():
    """True when there is no index, it is of an older kind, or its source
    (the OSM file or the map tiles) is newer than it."""
    sources = [p for p in (PBF, basemap.newest_build()) if p and os.path.exists(p)]
    if not sources:
        return False
    if not os.path.exists(DB) or _version() != INDEX_VERSION:
        return True
    return os.path.getmtime(DB) < max(os.path.getmtime(p) for p in sources)


# ----------------------------------------------------------------- search

def _query(text, join=" AND "):
    """'lulu mal' -> 'lulu* AND mal*' (every word, as a prefix)."""
    words = re.findall(r"\w+", text.lower())
    return join.join('"%s"*' % w for w in words) if words else None


def _sound_query(text, join=" AND "):
    """'kuthiyathodu' -> '"kutiatod"*': the typed words as they sound."""
    keys = [_sound(w) for w in re.findall(r"[A-Za-z]+", text)]
    keys = [k for k in keys if len(k) >= 2]
    return join.join('"%s"*' % k for k in keys) if keys else None


# What people add after a place name to say what sort of place they want:
# "kuthiathode bus stop", "vyttila hub bust stop", "edappally jn". These words
# are rarely part of the name in the map, so they are not required to match;
# they lift places of that sort instead. Common typos are included.
_BUS = r"(?:bus|buss|bust|bas|bs)"
_STOP = r"(?:stop|stops|stopp|stp|sotp|stoop)"
_HINTS = [
    (re.compile(r"\b(?:%s\s*%s|%s|busstp|bustop|bustopp)\b" % (_BUS, _STOP, "busstop"), re.I),
     ("Bus stop", "Bus stand")),
    (re.compile(r"\b(?:%s\s*(?:stand|stnd|station)|busstand|ksrtc(?:\s*stand)?)\b" % _BUS, re.I),
     ("Bus stand", "Bus stop")),
    (re.compile(r"\b%s\b" % _STOP, re.I), ("Bus stop", "Bus stand")),
    (re.compile(r"\b(?:junction|jn|jnc|jct|jun)\b\.?", re.I), ("junction",)),
]


def clean_query(text):
    """Split what was typed into the place name and the sort of place wanted:
    'north kuthiathode bust stop' -> ('north kuthiathode', {'Bus stop', ...})."""
    core, kinds = text, set()
    for pattern, wanted in _HINTS:
        if pattern.search(core):
            core = pattern.sub(" ", core)
            kinds.update(wanted)
    core = " ".join(core.split())
    if not re.search(r"\w", core):
        return text.strip(), set()                    # only "bus stop": search it as typed
    return core, kinds


def _covered(words, name, alt):
    """Letters of the typed words found in the place's names, by spelling or
    by sound, as a share of all typed letters (long words count most)."""
    have = re.findall(r"[a-z0-9]+", (name + " " + (alt or "")).lower())
    sounds = [_sound(h) for h in have]
    got = 0
    for w in words:
        sw = _sound(w)
        if any(h.startswith(w) for h in have) or (len(sw) >= 2 and any(s.startswith(sw) for s in sounds)):
            got += len(w)
    return got / max(1, sum(len(w) for w in words))


def search(text, near=None, limit=8):
    """Best matches for what the user has typed so far, nearest and most
    useful first. `near` (lat, lon) is where the user is or is looking.
    Exact spellings come first; other spellings of the same name follow.
    Words like "bus stop" or "jn" lift places of that sort, and when no
    place has every typed word, the places with most of them are shown."""
    text, want = clean_query(text)
    q = _query(text)
    if not q or not os.path.exists(DB):
        return []
    db = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    rows = {}
    cols = "p.id, p.name, p.kind, p.weight, p.lat, p.lon, p.near, p.alt, "
    words = re.findall(r"\w+", text.lower())

    def run(sql, query, exact):
        for row in db.execute(sql, (query,)):
            rows.setdefault(row[0], row[1:] + (exact,))

    def ask(join, exact_flag):
        run("SELECT " + cols + "bm25(search, 10.0, 2.0, 1.0) "
            "FROM search JOIN places p ON p.id = search.rowid "
            "WHERE search MATCH ? ORDER BY bm25(search, 10.0, 2.0, 1.0) LIMIT 400",
            _query(text, join), exact_flag)
        sq = _sound_query(text, join)
        if sq:
            try:
                run("SELECT " + cols + "bm25(sound) "
                    "FROM sound JOIN places p ON p.id = sound.rowid "
                    "WHERE sound MATCH ? ORDER BY bm25(sound) LIMIT 400", sq, False)
            except sqlite3.OperationalError:
                pass                                    # an index from before sound keys

    try:
        ask(" AND ", True)
        if len(words) > 1 and len(rows) < limit:
            ask(" OR ", False)                          # most of the words, if not all
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    typed = " ".join(words)
    styped = _sound_key(text)
    scored = []
    for name, kind, weight, lat, lon, place_near, alt, rank, exact in rows.values():
        low = name.lower()
        score = weight * 2.0 - rank                      # bm25: lower is better
        if len(words) > 1:
            share = _covered(words, name, alt)
            if share < 0.5:
                continue                                # matched only a small word
            score -= 14 * (1 - share)                   # every word beats most words
        if kind in want:
            score += 10                                 # "bus stop" typed: bus stops first
        elif "junction" in want and re.search(r"\b(?:junction|jn|jnc|jct)\b", low):
            score += 10
        if low == typed:
            score += 20
        elif low.startswith(typed):
            score += 14
        elif typed in low:
            score += 6
        elif styped:
            sname = _sound_key(name)
            if sname == styped:
                score += 16                              # same name, other spelling
            elif sname.startswith(styped):
                score += 11
            elif styped in sname:
                score += 4
        if exact:
            score += 3
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
