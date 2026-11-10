"""Build the sea for the app's own map from the Kerala OpenStreetMap extract.

    python tools/build_sea.py

OpenStreetMap stores the coast as lines (natural=coastline, land on the
left), not as a sea area, and the tile build (tools/build_map.py) leaves the
sea out. This joins the coastline of the extract into one line, carries its
two ends to the edge of the map area, and cuts that area into land and sea.
Around that area, where the app has no OpenStreetMap data, the sea is cut
from Natural Earth's coarse world land outline (public domain, downloaded
once to .cache/naturalearth), so the map zoomed out shows the real shape of
south India and Sri Lanka instead of a box.
The sea is written to web/map/sea.geojson, which web/basemap.js draws under
the roads. Re-run it after a new extract (build_map.py does).
"""

import json
import os
import sys
import urllib.request

import osmium
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely.ops import linemerge, polygonize, unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PBF = os.path.join(ROOT, ".cache", "osm", "kerala-latest.osm.pbf")
OUT = os.path.join(ROOT, "web", "map", "sea.geojson")
LAND = os.path.join(ROOT, ".cache", "naturalearth", "ne_10m_land.geojson")
LAND_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
            "master/geojson/ne_10m_land.geojson")
WEST, SOUTH, EAST, NORTH = 74.80, 8.15, 77.45, 12.85     # same area as build_map.py
OUTER_DEG = 8.0            # how far the sea reaches past the map area (web/basemap.js keeps the view inside)
OUTER_SIMPLIFY_DEG = 0.003  # about 300 m: the outline outside Kerala is only seen zoomed out
SIMPLIFY_DEG = 0.00008      # about 9 m: invisible at street zoom, a third of the size
# Points certainly in the Arabian Sea / Laccadive Sea: any face holding one is sea.
SEA_SEEDS = [(WEST + 0.01, lat / 10) for lat in range(83, 124, 2)] + \
            [(lon / 10, SOUTH + 0.01) for lon in range(749, 770, 2)]


class Coast(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def way(self, w):
        if w.tags.get("natural") == "coastline" and len(w.nodes) > 1:
            try:
                self.lines.append(LineString([(n.lon, n.lat) for n in w.nodes]))
            except osmium.InvalidLocationError:
                pass


def to_edge(end, inner):
    """Carry an open end of the coast straight out to the map edge, in the
    direction the coast was heading (the extract stops at the state border)."""
    x, y = end
    dx, dy = x - inner[0], y - inner[1]
    # Head north at the northern end and south at the southern one, whatever
    # the last few metres did: the coast runs roughly north-south here.
    if abs(dy) < 1e-9:
        dy = 1e-9
    target_y = NORTH + 0.05 if dy > 0 else SOUTH - 0.05
    t = (target_y - y) / dy
    return (x + dx * t, target_y)


def outer_sea(area):
    """The sea around the map area, from Natural Earth's land outline."""
    if not os.path.exists(LAND):
        os.makedirs(os.path.dirname(LAND), exist_ok=True)
        print("Downloading Natural Earth's land outline (~10 MB, once)...", flush=True)
        try:
            urllib.request.urlretrieve(LAND_URL, LAND + ".part")
        except OSError as exc:
            print("  could not download it (%s); the sea stops at the map area." % exc)
            return None
        os.replace(LAND + ".part", LAND)
    region = box(WEST - OUTER_DEG, SOUTH - OUTER_DEG, EAST + OUTER_DEG, NORTH + OUTER_DEG)
    with open(LAND, encoding="utf-8") as f:
        land = [shape(ft["geometry"]) for ft in json.load(f)["features"]]
    land = unary_union([g.intersection(region) for g in land if g.intersects(region)])
    sea = region.difference(area).difference(land)
    return sea.simplify(OUTER_SIMPLIFY_DEG, preserve_topology=True)


def main():
    if not os.path.exists(PBF):
        print("No extract at %s - run tools/build_map.py first." % PBF)
        return 1
    print("Reading the coastline from the Kerala extract...", flush=True)
    h = Coast()
    h.apply_file(PBF, locations=True)
    merged = linemerge(h.lines)
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    rings = [p for p in parts if p.is_ring]
    chains = sorted((p for p in parts if not p.is_ring), key=lambda p: -p.length)
    print("%d coastline ways -> %d open chain(s), %d island(s)"
          % (len(h.lines), len(chains), len(rings)))
    if not chains:
        print("No open coastline found.")
        return 1

    lines = list(rings)
    for c in chains:
        pts = list(c.coords)
        if c is chains[0]:
            # The main coast: extend both ends past the map edge.
            pts = [to_edge(pts[0], pts[min(20, len(pts) - 1)])] + pts + \
                  [to_edge(pts[-1], pts[max(-21, -len(pts))])]
        lines.append(LineString(pts))

    area = box(WEST, SOUTH, EAST, NORTH)
    faces = list(polygonize(unary_union(lines + [area.exterior])))
    faces = [f.intersection(area) for f in faces if f.intersects(area)]
    seeds = [Point(s) for s in SEA_SEEDS]
    sea = [f for f in faces if any(f.contains(s) for s in seeds)]
    if not sea:
        print("Could not tell land from sea.")
        return 1
    geom = unary_union(sea).simplify(SIMPLIFY_DEG, preserve_topology=True)
    outer = outer_sea(area)
    if outer is not None:
        geom = unary_union([geom, outer])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    feature = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"class": "ocean"}, "geometry": mapping(geom)}]}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(feature, f, separators=(",", ":"))
    # Six decimals are ~0.1 m; trim to five (~1 m) to keep the file small.
    with open(OUT, encoding="utf-8") as f:
        text = f.read()
    import re
    text = re.sub(r"(-?\d+\.\d{5})\d+", r"\1", text)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print("Sea written to %s (%.0f KB, %.2f sq deg)"
          % (OUT, os.path.getsize(OUT) / 1024, geom.area))
    return 0


if __name__ == "__main__":
    sys.exit(main())
