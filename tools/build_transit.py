"""Every bus stop, bus stand, railway station and metro station in Kerala.

Reads the OpenStreetMap extract that build_map.py downloads and writes
web/map/transit.geojson, which the app shows on the map when the rider turns
on "All stops". The tags match the ones trip planning searches
(transitnav/config.py), so the map shows the same stops a trip can use.

    python tools/build_transit.py

Each point has `k` (bus, stand, rail or metro) and `n` (its name, if any).

OpenStreetMap's bus stands (amenity=bus_station) are cleaned up first,
because mappers often give that tag to a roadside shelter, or to a place
that has nothing to do with buses:
  - no name at all: left out (nothing to tell the rider);
  - not a bus place (a workshop, a club, a railway gate...): left out;
  - named like a stop or shelter ("Pallikkare Bus Stop"): shown as a bus stop;
  - named like a stand (stand, station, terminal, depot, KSRTC, in English
    or Malayalam): a bus stand;
  - just a place name ("Kattakada"): a bus stand if OpenStreetMap also marks
    it as a station, gives KSRTC as the operator or draws its outline;
    otherwise a bus stop;
  - one stand mapped twice (a point and an outline, close together and
    with matching names): kept once.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys

import osmium

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PBF = os.path.join(ROOT, ".cache", "osm", "kerala-latest.osm.pbf")
OUT = os.path.join(ROOT, "web", "map", "transit.geojson")

# Signal cabins are sometimes tagged as stations; nobody boards there.
CABIN = re.compile(r"\bcabin\b", re.I)

# What a real bus stand is called, in English and Malayalam
# (സ്റ്റാൻഡ് stand, സ്റ്റേഷൻ station, ഡിപ്പോ depot, കെ.എസ്.ആർ.ടി.സി KSRTC).
STAND_WORDS = re.compile(
    r"stand(?!ard)|station|termi?nal|terminus|\bdepot\b|ksrtc|k\s*\.\s*s\s*\.\s*r\s*\.\s*t\s*\.\s*c|"
    r"transport\s+co|"
    "സ്റ്റാൻഡ|സ്റ്റാന്റ|"
    "സ്റ്റേഷൻ|ഡിപ്പോ|"
    "കെ\\.?\\s*എസ്", re.I)
# A roadside stop or shelter, not a stand.
STOP_WORDS = re.compile(r"\bstop\b|shelter|waiting|\bshed\b|\bjunction\b|\bjn\b", re.I)
# Places that are not bus stands, whatever else the name says.
NOT_BUS = re.compile(
    r"work\s*shop|\bclub\b|\boffice\b|garage|\bgate\b|\bshop\b|hotel|\bbank\b|auto\s*stand|taxi|"
    r"rickshaw|parking|lorry|loary|lorri|truck|goods|carr?iage|carrage|bus\s*shed", re.I)
# ...unless it says bus stand outright ("Edoor Bus Stand & Goods Carrier Stand"),
# and is not a garage, workshop, office or car park that happens to say so.
SAYS_BUS_STAND = re.compile(r"bus\s*(stand|station|termi)", re.I)
NEVER_A_STAND = re.compile(r"parking|garage|work\s*shop|\boffice\b", re.I)
# Named after a school, temple or hospital: a stand only if the name says so
# ("Medical College Bus Terminal" is one; "School Stop" is not).
NAMED_AFTER = re.compile(r"school|college|temple|church|mosque|masjid|hospital", re.I)

MERGE_M = 300          # a stand mapped twice is usually within a few dozen metres


def station_kind(tags) -> str | None:
    """rail / metro for a railway station or halt, else None."""
    rail = tags.get("railway")
    if rail not in ("station", "halt"):
        return None
    station = tags.get("station")
    if rail == "station" and (station in ("subway", "light_rail")
                              or tags.get("subway") == "yes"
                              or tags.get("light_rail") == "yes"):
        return "metro"
    if station == "monorail" or CABIN.search(tags.get("name", "")):
        return None
    return "rail"


def is_bus_stop(tags) -> bool:
    return tags.get("highway") == "bus_stop" or (
        tags.get("public_transport") == "platform" and tags.get("bus") == "yes")


def stand_kind(tags, is_area: bool) -> str | None:
    """stand, bus (really a stop) or None (leave it out) for amenity=bus_station."""
    names = " ".join(tags.get(k, "") for k in ("name", "name:en", "name:ml", "short_name"))
    if not names.strip():
        return None
    if NOT_BUS.search(names) and (NEVER_A_STAND.search(names) or not SAYS_BUS_STAND.search(names)):
        return None
    if STAND_WORDS.search(names):
        return "stand"
    if NAMED_AFTER.search(names):
        return None
    if STOP_WORDS.search(names):
        return "bus"
    marked = (tags.get("public_transport") == "station"
              or "ksrtc" in tags.get("operator", "").lower()
              or "transport corp" in tags.get("operator", "").lower()
              or is_area)
    return "stand" if marked else "bus"


def display_name(tags) -> str:
    return tags.get("name:en") or tags.get("name") or tags.get("name:ml") or ""


class Stops(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.points = []           # [kind, name, lon, lat, is_area]

    def _add(self, tags, lon, lat, is_area):
        k = station_kind(tags)
        if not k and tags.get("amenity") == "bus_station":
            k = stand_kind(tags, is_area)
            if k is None:
                return
        if not k and is_bus_stop(tags):
            k = "bus"
        if k:
            self.points.append([k, display_name(tags), lon, lat, is_area])

    def node(self, n):
        if n.location.valid():
            self._add(n.tags, n.location.lon, n.location.lat, False)

    def way(self, w):
        # Bus stands and stations drawn as an area: their middle.
        if not (w.tags.get("amenity") == "bus_station" or w.tags.get("railway") in ("station", "halt")):
            return
        try:
            xs = [nd.lon for nd in w.nodes]
            ys = [nd.lat for nd in w.nodes]
        except osmium.InvalidLocationError:
            return
        if xs:
            self._add(w.tags, sum(xs) / len(xs), sum(ys) / len(ys), True)


def metres(a, b) -> float:
    dy = (a[3] - b[3]) * 111320.0
    dx = (a[2] - b[2]) * 111320.0 * math.cos(math.radians(a[3]))
    return math.hypot(dx, dy)


GENERIC = re.compile(
    r"\b(ksrtc|k\s*s\s*r\s*t\s*c|bus|bust|stand|station|termi?nal|terminus|depot|sub|private|"
    r"municipal|muncipal|municipality|kerala|state|road|transport|corporation|corperation|"
    r"cooperation|co|new|old|the|of|jn|junction|building|complex|central|interstate|"
    r"local|town|city|main)\b", re.I)
KSRTC = re.compile("ksrtc|k\\s*s\\s*r\\s*t\\s*c|transport\\s+co|"
                   "കോർപറേഷൻ|കെ\\.?\\s*എസ്", re.I)
PRIVATE = re.compile(r"private|municipal|muncipal|municipality", re.I)
CLOSE_M = 150          # two stands this close with the same operator are one


def operator(name: str) -> str:
    """ksrtc, private (municipal stands serve private buses), or "" if
    the name does not say or says both ("Private/KSRTC Bus Station")."""
    k, p = bool(KSRTC.search(name)), bool(PRIVATE.search(name))
    return "ksrtc" if k and not p else "private" if p and not k else ""


def place_words(name: str) -> set:
    """The words of a stand's name that say *where* it is."""
    words = re.findall(r"\w+", GENERIC.sub(" ", name.lower().replace(".", " ")))
    return {w for w in words if len(w) >= 3 and not w.isdigit()}


def similar(a: str, b: str) -> bool:
    """Ottappalam / Ottapalam, Irinjalakuda / Irinjakuda: one place."""
    import difflib
    return a == b or (min(len(a), len(b)) >= 5 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8)


def same_stand(a, b) -> bool:
    """Two entries close together that are one stand mapped twice. A KSRTC
    stand next to a private one ("KSRTC Bus Stand, Koothattukulam" and
    "Private Bus Stand, Koothattukulam") are two stands."""
    d = metres(a, b)
    if d > MERGE_M:
        return False
    oa, ob = operator(a[1]), operator(b[1])
    if oa and ob and oa != ob:
        return False
    if oa and oa == ob and d <= CLOSE_M:
        return True
    if not re.search(r"[A-Za-z]", a[1]) or not re.search(r"[A-Za-z]", b[1]):
        return d <= CLOSE_M         # one name in Malayalam: the words cannot be compared
    wa, wb = place_words(a[1]), place_words(b[1])
    if not wa or not wb:            # "KSRTC Bus Stand" next to "Harippad"
        return d <= CLOSE_M or not (wa or wb)
    return any(similar(x, y) for x in wa for y in wb)


def merge_stands(points):
    stands = [p for p in points if p[0] == "stand"]
    others = [p for p in points if p[0] != "stand"]
    # Outlines first, then longer names: the better entry survives.
    stands.sort(key=lambda p: (not p[4], -len(p[1])))
    kept = []
    for s in stands:
        if not any(same_stand(s, k) for k in kept):
            kept.append(s)
    return others + kept, len(stands) - len(kept)


def dedupe_stations(points):
    """A station drawn both as a point and as an area: keep one."""
    seen, out = set(), []
    for p in points:
        if p[0] in ("rail", "metro"):
            key = (p[0], p[1].lower(), round(p[2], 3), round(p[3], 3))
            if key in seen:
                continue
            seen.add(key)
        out.append(p)
    return out


def main() -> int:
    if not os.path.exists(PBF):
        print("No OSM extract at %s: run tools/build_map.py first." % PBF)
        return 1
    h = Stops()
    h.apply_file(PBF, locations=True)
    points, merged = merge_stands(dedupe_stations(h.points))
    # Stations last, so they draw over the bus stops around them.
    order = {"bus": 0, "stand": 1, "metro": 2, "rail": 3}
    points.sort(key=lambda p: order[p[0]])
    features = [{"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [round(p[2], 5), round(p[3], 5)]},
                 "properties": {"k": p[0], "n": p[1]} if p[1] else {"k": p[0]}}
                for p in points]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f,
                  ensure_ascii=False, separators=(",", ":"))
    counts = {k: sum(1 for p in points if p[0] == k) for k in order}
    print("Wrote %s (%d KB): %s; %d duplicate stands merged" % (
        OUT, os.path.getsize(OUT) // 1024,
        ", ".join("%d %s" % (v, k) for k, v in counts.items()), merged))
    return 0


if __name__ == "__main__":
    sys.exit(main())
