"""Every bus stop, bus stand, railway station and metro station in Kerala.

Reads the OpenStreetMap extract that build_map.py downloads and writes
web/map/transit.geojson, which the app shows on the map when the rider turns
on "Show all stops". The tags match the ones trip planning searches
(transitnav/config.py), so the map shows the same stops a trip can use.

    python tools/build_transit.py

Each point has `k` (bus, stand, rail or metro) and `n` (its name, if any).
"""

from __future__ import annotations

import json
import os
import re
import sys

import osmium

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PBF = os.path.join(ROOT, ".cache", "osm", "kerala-latest.osm.pbf")
OUT = os.path.join(ROOT, "web", "map", "transit.geojson")

# Signal cabins are sometimes tagged as stations; nobody boards there.
CABIN = re.compile(r"\bcabin\b", re.I)


def kind(tags) -> str | None:
    """bus / stand / rail / metro, or None for anything else."""
    rail = tags.get("railway")
    if rail in ("station", "halt"):
        station = tags.get("station")
        if rail == "station" and (station in ("subway", "light_rail")
                                  or tags.get("subway") == "yes"
                                  or tags.get("light_rail") == "yes"):
            return "metro"
        if station == "monorail":
            return None
        if CABIN.search(tags.get("name", "")):
            return None
        return "rail"
    if tags.get("amenity") == "bus_station":
        return "stand"
    if tags.get("highway") == "bus_stop":
        return "bus"
    if tags.get("public_transport") == "platform" and tags.get("bus") == "yes":
        return "bus"
    return None


class Stops(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.points = []           # (kind, name, lon, lat)

    def _add(self, tags, lon, lat):
        k = kind(tags)
        if k:
            self.points.append((k, tags.get("name:en") or tags.get("name") or "", lon, lat))

    def node(self, n):
        if n.location.valid():
            self._add(n.tags, n.location.lon, n.location.lat)

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
            self._add(w.tags, sum(xs) / len(xs), sum(ys) / len(ys))


def dedupe(points):
    """A stand or station drawn both as a point and as an area: keep one.
    Bus-stop pairs (one each side of the road) are kept: both are real."""
    seen, out = set(), []
    for k, name, lon, lat in points:
        key = (k, name.lower(), round(lon, 3), round(lat, 3)) if k != "bus" or not name else None
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append((k, name, lon, lat))
    return out


def main() -> int:
    if not os.path.exists(PBF):
        print("No OSM extract at %s: run tools/build_map.py first." % PBF)
        return 1
    h = Stops()
    h.apply_file(PBF, locations=True)
    points = dedupe(h.points)
    # Stations last, so they draw over the bus stops around them.
    order = {"bus": 0, "stand": 1, "metro": 2, "rail": 3}
    points.sort(key=lambda p: order[p[0]])
    features = [{"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                 "properties": {"k": k, "n": name} if name else {"k": k}}
                for k, name, lon, lat in points]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f,
                  ensure_ascii=False, separators=(",", ":"))
    counts = {k: sum(1 for p in points if p[0] == k) for k in order}
    print("Wrote %s (%d KB): %s" % (OUT, os.path.getsize(OUT) // 1024,
                                    ", ".join("%d %s" % (v, k) for k, v in counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
