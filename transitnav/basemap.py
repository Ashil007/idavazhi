"""The app's own simple base map: vector tiles made from OpenStreetMap.

`tools/build_map.py` turns the Kerala OSM extract into
`.cache/map/kerala-<date>.mbtiles` with tilemaker (OpenMapTiles layer schema).
Each build is a new file and the newest one is served, so a rebuild takes
effect while the app runs: Windows will not replace a file the app has open. This module serves those tiles to
the page, which draws them in a deliberately plain style (web/basemap.js):
soft land, white roads, few labels - the look of a delivery app's map rather
than a full topographic one.

An .mbtiles file is a SQLite database, so the standard library reads it.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import sqlite3
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_DIR = os.path.join(ROOT, ".cache", "map")
RECHECK_S = 15            # how often to look for a newer build

_newest = {"path": None, "checked": 0.0}
_lock = threading.Lock()


def newest_build():
    """The most recent finished map file (kerala.mbtiles or kerala-<date>.mbtiles)."""
    files = [f for f in glob.glob(os.path.join(MAP_DIR, "kerala*.mbtiles"))
             if ".building." not in os.path.basename(f)]
    return max(files, key=os.path.getmtime) if files else None


def _current():
    with _lock:
        if time.time() - _newest["checked"] > RECHECK_S:
            _newest.update(path=newest_build(), checked=time.time())
        return _newest["path"]


@contextlib.contextmanager
def _db():
    """A read-only connection to the newest map, open only for one query.

    Held connections would keep an old build's file locked on Windows, so it
    could not be deleted after a rebuild; opening SQLite costs well under a
    millisecond."""
    path = _current()
    if path is None:
        yield None
        return
    uri = "file:%s?mode=ro" % path.replace("\\", "/")
    conn = sqlite3.connect(uri, uri=True)
    try:
        yield conn
    finally:
        conn.close()


def info():
    """{"available", "bounds": [w, s, e, n], "minzoom", "maxzoom"} for the page."""
    with _db() as conn:
        if conn is None:
            return {"available": False}
        meta = dict(conn.execute("SELECT name, value FROM metadata").fetchall())
    bounds = [float(v) for v in (meta.get("bounds") or "-180,-85,180,85").split(",")]
    out = {"available": True, "bounds": bounds,
           "minzoom": int(meta.get("minzoom", 0)), "maxzoom": int(meta.get("maxzoom", 14))}
    try:   # the layers present, for anyone styling the map
        out["layers"] = [l["id"] for l in json.loads(meta.get("json", "{}")).get("vector_layers", [])]
    except ValueError:
        pass
    return out


def tile(z: int, x: int, y: int):
    """Raw tile bytes (usually gzip-compressed protobuf) or None."""
    if z < 0 or z > 22:
        return None
    with _db() as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, (1 << z) - 1 - y),          # MBTiles rows count from the bottom (TMS)
        ).fetchone()
    return row[0] if row else None
