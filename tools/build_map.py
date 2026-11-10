"""Build the app's own base-map tiles for Kerala from OpenStreetMap.

    python tools/build_map.py

Downloads the Kerala extract (~180 MB, OpenStreetMap France) if it is not in
.cache/osm yet, then runs tilemaker (in Docker) with its OpenMapTiles layer
schema to write .cache/map/kerala-<date>.mbtiles. Each build is a new file:
the running app switches to the newest within seconds (Windows cannot
replace a file the app has open), and older builds are deleted once free. The app serves that file at
/tiles/{z}/{x}/{y}.pbf and draws it in a plain style (web/basemap.js).
It then rebuilds the sea (tools/build_sea.py) from the same extract, and
the roads and cities around Kerala (tools/build_region.py).
Needs Docker Desktop running. Takes a few minutes; re-run it to refresh the
map from newer OpenStreetMap data.
"""

import glob
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".cache")
PBF = os.path.join(CACHE, "osm", "kerala-latest.osm.pbf")
PBF_URL = "https://download.openstreetmap.fr/extracts/asia/india/kerala-latest.osm.pbf"
MAP_DIR = os.path.join(CACHE, "map")
IMAGE = "ghcr.io/systemed/tilemaker:master"
BBOX = "74.80,8.15,77.45,12.85"      # Kerala: min lon, min lat, max lon, max lat


def main():
    if not os.path.exists(PBF):
        os.makedirs(os.path.dirname(PBF), exist_ok=True)
        print("Downloading the Kerala OpenStreetMap extract (~180 MB)...", flush=True)
        urllib.request.urlretrieve(PBF_URL, PBF + ".part")
        os.replace(PBF + ".part", PBF)
    os.makedirs(MAP_DIR, exist_ok=True)
    out = os.path.join(MAP_DIR, "kerala-%s.mbtiles" % time.strftime("%Y%m%d-%H%M%S"))
    tmp = out + ".building.mbtiles"
    if os.path.exists(tmp):
        os.remove(tmp)
    print("Building map tiles with tilemaker (a few minutes)...", flush=True)
    rel = lambda p: "/data/" + os.path.relpath(p, CACHE).replace("\\", "/")
    # tilemaker needs the area stated to read its (optional) coastline
    # shapefiles; without them it simply leaves the sea out, which
    # tools/build_sea.py makes from the extract's coastline instead.
    result = subprocess.run(["docker", "run", "--rm", "-v", "%s:/data" % CACHE, IMAGE,
                             rel(PBF), "--output", rel(tmp), "--bbox", BBOX])
    if result.returncode != 0 or not os.path.exists(tmp):
        print("tilemaker failed (exit %d)." % result.returncode)
        return 1
    os.replace(tmp, out)          # a new name, so nothing has it open
    print("Map tiles ready: %s (%.0f MB)" % (out, os.path.getsize(out) / 1e6))
    print("The running app switches to it within about 15 seconds.")
    remove_old(keep=out)
    for script in ("build_sea.py", "build_region.py"):
        code = subprocess.run([sys.executable, os.path.join(ROOT, "tools", script)]).returncode
        if code:
            return code
    return 0


def remove_old(keep):
    """Delete earlier builds; one the app still has open stays until next time."""
    for f in glob.glob(os.path.join(MAP_DIR, "kerala*.mbtiles*")):
        if os.path.abspath(f) == os.path.abspath(keep):
            continue
        try:
            os.remove(f)
            print("Removed the older build %s" % os.path.basename(f))
        except OSError:
            print("Kept %s for now (the app still has it open; the next build "
                  "removes it)." % os.path.basename(f))


if __name__ == "__main__":
    sys.exit(main())
