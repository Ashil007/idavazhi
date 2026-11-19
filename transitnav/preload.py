"""Download a whole region's stations and track ahead of time.

Run on server start (see serve.py) or by hand:

    python -m transitnav.preload kerala
    python -m transitnav.preload 9.8,76.1,10.2,76.5     (south,west,north,east)
    python -m transitnav.preload timetable              (long-distance trains)

Everything lands in the same per-tile disk cache that trip planning reads, so
once a region is loaded every trip inside it plans without touching Overpass.
Tiles stay fresh for TILE_TTL_S (a month); a later run only fetches tiles that
have expired, so restarting the server costs nothing.

Roads are not preloaded: walking, riding and bus legs come from the street
routing server, which already holds every road and answers in under a second.
The roads of OpenStreetMap's bus routes are (busroutes.py): they say which
stops buses really call at and which roads they take.
"""

from __future__ import annotations

import sys
import threading
import time

from . import busroutes, overpass, timetable
from .config import MODES

# (south, west, north, east). Kept generous: a trip near the edge still finds
# its stations, and train tiles are a degree wide anyway.
REGIONS = {
    "kerala": ("Kerala", (8.15, 74.80, 12.85, 77.45)),
    "kochi": ("Kochi", (9.80, 76.15, 10.25, 76.50)),
    "bengaluru": ("Bengaluru", (12.75, 77.35, 13.25, 77.85)),
    "chennai": ("Chennai", (12.80, 80.00, 13.30, 80.35)),
    "hyderabad": ("Hyderabad", (17.20, 78.20, 17.65, 78.70)),
    "mumbai": ("Mumbai", (18.85, 72.75, 19.35, 73.20)),
    "delhi": ("Delhi NCR", (28.35, 76.85, 28.90, 77.55)),
    "kolkata": ("Kolkata", (22.40, 88.20, 22.75, 88.50)),
}
DEFAULT_REGION = "kerala"

# What gets loaded, smallest first so the quick wins land early. Bus stops are
# loaded but not the road network: the street router covers bus routes.
DATASETS = [
    ("metro", "stops", "metro stations"),
    ("metro", "network", "metro track"),
    ("train", "stops", "railway stations"),
    ("train", "network", "railway track"),
    ("bus", "stops", "bus stops"),
    ("bus", "routes", "bus routes"),
]

# Shown in the web page while a preload runs.
status = {"state": "idle", "region": None, "done": 0, "total": 0, "current": "", "failed": 0}
_lock = threading.Lock()


def parse_region(text: str):
    """A REGIONS name or "south,west,north,east" -> (label, bbox)."""
    key = text.strip().lower()
    if key in REGIONS:
        return REGIONS[key]
    try:
        s, w, n, e = (float(v) for v in key.split(","))
    except ValueError:
        raise ValueError("unknown region %r; use one of %s, or south,west,north,east"
                         % (text, ", ".join(sorted(REGIONS)))) from None
    if not (s < n and w < e):
        raise ValueError("region box must be south,west,north,east with south<north, west<east")
    return ("%.2f,%.2f,%.2f,%.2f" % (s, w, n, e), (s, w, n, e))


def _builders(mode: str, kind: str):
    """The planner's exact per-tile query (the cache key) and a long-timeout
    version for a block of tiles."""
    cfg = MODES[mode]
    if kind == "routes":
        return (busroutes.build_query,
                lambda box: busroutes.build_query(box, timeout=overpass.PRELOAD_TIMEOUT_S))
    if kind == "stops":
        f = cfg["stop_filters"]
        return (lambda box: overpass.build_stops_in_bbox_query(box, f),
                lambda box: overpass.build_stops_in_bbox_query(
                    box, f, timeout=overpass.PRELOAD_TIMEOUT_S))
    f, x = cfg["network_filters"], cfg["network_exclude"]
    return (lambda box: overpass.build_network_query(box, f, x),
            lambda box: overpass.build_network_query(
                box, f, x, timeout=overpass.PRELOAD_TIMEOUT_S))


def _load(label, bbox, datasets, log, max_block=None, last=True, workers=1) -> tuple:
    """Download `datasets` for `bbox` into the tile cache. Returns
    (tiles fetched, tiles failed). `last` False keeps the page's status
    "running" because another phase follows."""
    plan = []
    for mode, kind, what in datasets:
        deg = busroutes.TILE_DEG if kind == "routes" else MODES[mode]["tile_deg"]
        build, build_big = _builders(mode, kind)
        tiles = overpass.tiles_for_bbox(bbox, deg)
        todo = [t for t in tiles
                if not overpass.is_cached(build(overpass.tile_bbox(t, deg)), overpass.TILE_TTL_S)]
        plan.append((what, kind, deg, build, build_big, todo))

    total = sum(len(p[5]) for p in plan)
    with _lock:
        status.update(state="running", region=label, done=0, total=total, current="", failed=0)
    if not total:
        log("Offline data for %s is already on disk and up to date." % label)
        if last:
            with _lock:
                status.update(state="done")
        return 0, 0

    log("Preloading %s: %s for %d map tiles. Trips there will plan without "
        "downloading once this finishes." % (label, ", ".join(p[0] for p in plan), total))
    started = time.time()
    failed = 0
    for what, kind, deg, build, build_big, todo in plan:
        if not todo:
            continue
        with _lock:
            status["current"] = what
        t0 = time.time()

        def progress(n):
            with _lock:
                status["done"] += n

        bad = overpass.preload_tiles(todo, deg, build, build_big, kind, progress,
                                     max_block, workers)
        failed += bad
        log("  %s: %d tiles in %.0f s%s" % (what, len(todo), time.time() - t0,
                                          (", %d failed" % bad) if bad else ""))
    with _lock:
        status.update(current="", failed=failed)
        if last:
            status["state"] = "done" if not failed else "partial"
    log("Preload of %s finished in %.0f s%s." % (
        label, time.time() - started,
        "" if not failed else " (%d tiles failed - they download on demand, "
                              "or on the next start)" % failed))
    return total, failed


def run(region: str = DEFAULT_REGION, log=print, last=True) -> int:
    """Preload `region`. Returns the number of tiles that failed."""
    label, bbox = parse_region(region)
    return _load(label, bbox, DATASETS, log, last=last)[1]


def run_timetable(log=print) -> bool:
    """Fetch and index the Indian Railways timetable used for train trips
    over 300 km (see timetable.py). A no-op once it is on disk."""
    if timetable.load() is not None:
        return True
    with _lock:
        status.update(state="running", region="Indian Railways timetable", done=0, total=1,
                      current="about 100 MB, once", failed=0)
    ok = timetable.ensure(log)
    with _lock:
        status["done"] = 1
    return ok


def start_background(region: str = DEFAULT_REGION):
    """Fetch the train timetable, then preload `region`, off the request path."""
    def work():
        log = lambda m: print(m, flush=True)
        try:
            run_timetable(log)
        except Exception as exc:  # noqa: BLE001 - a plan will retry it on demand
            print("Could not fetch the train timetable (%s); long train trips will "
                  "try again when planned." % exc, flush=True)
        try:
            run(region, log)
        except Exception as exc:  # noqa: BLE001 - never take the server down
            with _lock:
                status.update(state="failed", current="")
            print("Preload failed: %s" % exc, flush=True)
    th = threading.Thread(target=work, name="preload", daemon=True)
    th.start()
    return th


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REGION
    if what == "timetable":
        sys.exit(0 if run_timetable() else 1)
    sys.exit(1 if run(what) else 0)
