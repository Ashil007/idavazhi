"""Build the map around Kerala: main roads, cities and state borders of the
neighbouring states, for when the map is zoomed out.

    python tools/build_region.py

The app's detailed map covers Kerala only (tools/build_map.py). Around it
the map would be empty land, so this takes Natural Earth's world data
(public domain, no credit needed, downloaded once to .cache/naturalearth):
highways, cities and towns, state borders and state names, cut to the area
around Kerala and with Kerala itself left out, where the detailed map
already has all of these. It writes web/map/region.geojson, which
web/basemap.js draws. build_map.py runs it after the tiles.
"""

import json
import os
import re
import sys
import urllib.request

from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NE_DIR = os.path.join(ROOT, ".cache", "naturalearth")
NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/%s.geojson"
OUT = os.path.join(ROOT, "web", "map", "region.geojson")
WEST, SOUTH, EAST, NORTH = 74.80, 8.15, 77.45, 12.85     # same area as build_map.py
OUTER_DEG = 8.0              # same reach as the sea (tools/build_sea.py)
SIMPLIFY_DEG = 0.002         # about 200 m: this is only seen zoomed out
MIN_STATE_SQ_DEG = 0.5       # smaller states and territories get no name label
ROAD_TYPES = {"Major Highway": "major", "Beltway": "major", "Secondary Highway": "minor",
              "Bypass": "minor", "Road": "minor"}


def natural_earth(name):
    path = os.path.join(NE_DIR, name + ".geojson")
    if not os.path.exists(path):
        os.makedirs(NE_DIR, exist_ok=True)
        print("Downloading Natural Earth %s (once)..." % name, flush=True)
        urllib.request.urlretrieve(NE_URL % name, path + ".part")
        os.replace(path + ".part", path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)["features"]


def main():
    region = box(WEST - OUTER_DEG, SOUTH - OUTER_DEG, EAST + OUTER_DEG, NORTH + OUTER_DEG)
    out = []

    states = [f for f in natural_earth("ne_10m_admin_1_states_provinces")
              if f["geometry"] and shape(f["geometry"]).intersects(region)]
    kerala = next(shape(f["geometry"]) for f in states
                  if f["properties"].get("name") == "Kerala")
    named = set()
    for f in states:
        p = f["properties"]
        # Indian states only (Sri Lanka's districts would crowd its map),
        # once each, and not the small enclaves (Puducherry, Mahe).
        if p.get("adm0_a3") != "IND" or p.get("name") in ("Kerala", None) or p["name"] in named:
            continue
        g = shape(f["geometry"]).intersection(region)
        if g.is_empty or g.area < MIN_STATE_SQ_DEG:
            continue
        named.add(p["name"])
        pt = g.representative_point()
        out.append({"type": "Feature", "properties": {"kind": "state", "name": p["name"]},
                    "geometry": mapping(pt)})

    # Kerala's own border is in the detailed map; leave it out here.
    near_kerala = kerala.buffer(0.03)
    for f in natural_earth("ne_10m_admin_1_states_provinces_lines"):
        if not f["geometry"]:
            continue
        g = shape(f["geometry"])
        if not g.intersects(region):
            continue
        g = g.intersection(region).difference(near_kerala).simplify(SIMPLIFY_DEG)
        if not g.is_empty:
            out.append({"type": "Feature", "properties": {"kind": "border"}, "geometry": mapping(g)})

    roads = 0
    for f in natural_earth("ne_10m_roads"):
        cls = ROAD_TYPES.get(f["properties"].get("type"))
        if not cls or not f["geometry"]:
            continue
        g = shape(f["geometry"])
        if not g.intersects(region):
            continue
        g = g.intersection(region).difference(kerala).simplify(SIMPLIFY_DEG)
        if not g.is_empty:
            roads += 1
            out.append({"type": "Feature", "properties": {"kind": "road", "class": cls},
                        "geometry": mapping(g)})

    places = 0
    outside = region.difference(kerala.buffer(0.05))
    for f in natural_earth("ne_10m_populated_places_simple"):
        p = f["properties"]
        g = shape(f["geometry"])
        if not outside.contains(g):
            continue
        places += 1
        out.append({"type": "Feature",
                    "properties": {"kind": "place", "name": p.get("name"),
                                   "rank": int(p.get("scalerank") or 10),
                                   "pop": int(p.get("pop_max") or 0)},
                    "geometry": mapping(g)})

    text = json.dumps({"type": "FeatureCollection", "features": out}, separators=(",", ":"))
    text = re.sub(r"(-?\d+\.\d{4})\d+", r"\1", text)     # four decimals: ~10 m
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("Around Kerala: %d roads, %d cities and towns. Written to %s (%.0f KB)"
          % (roads, places, OUT, len(text) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
