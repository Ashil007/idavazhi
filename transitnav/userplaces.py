"""Places riders add: the shop, office or house OpenStreetMap is missing.

No map has every shop. When a search finds nothing (say "VPK Distributors"),
the rider can tap the spot on the map and give its name; from then on it is
found by everyone's search, shown as "added by riders". Places are not copied
from Google Maps or any other map: Google's terms forbid it.

Stored in data/places.db (riders' data, never in git). Like bus times, a
rider is known only by a hash of a random browser id. The owner can list and
remove places:

    python -m transitnav.userplaces --list
    python -m transitnav.userplaces --remove 12
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
import time

from .stopnames import norm
from .timings import ROOT, TimingError, _device, _metres, _rate

DB_PATH = os.path.join(ROOT, "data", "places.db")

KINDS = {"shop": "Shop", "office": "Office", "food": "Restaurant", "health": "Hospital or clinic",
         "school": "School or college", "worship": "Place of worship", "home": "House", "other": "Place"}
SAME_M = 80               # the same name this close is the same place
# Vazhi covers Kerala; a little margin for the border towns.
BOUNDS = (8.0, 13.0, 74.7, 77.6)    # south, north, west, east

_lock = threading.Lock()


class PlaceError(TimingError):
    """A place that cannot be added; the message is for the rider."""


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.execute("PRAGMA journal_mode = WAL;")
    db.execute("PRAGMA busy_timeout = 5000;")
    db.execute("PRAGMA synchronous = NORMAL;")
    db.execute("""CREATE TABLE IF NOT EXISTS places (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, norm TEXT NOT NULL, kind TEXT NOT NULL,
        lat REAL NOT NULL, lon REAL NOT NULL, device TEXT NOT NULL, created REAL NOT NULL)""")
    return db


def _name(raw):
    name = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not 2 <= len(name) <= 80 or not norm(name):
        raise PlaceError("Give the place's name (2 to 80 letters).")
    if re.search(r"https?://|www\.|<|>|@", name, re.I):
        raise PlaceError("A place name cannot contain links or emails.")
    return name


def _where(body):
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
    except (KeyError, TypeError, ValueError):
        raise PlaceError("Tap where the place is on the map.") from None
    s, n, w, e = BOUNDS
    if not (s <= lat <= n and w <= lon <= e):
        raise PlaceError("Vazhi covers Kerala - that spot is outside it.")
    return lat, lon


def _result(pid, name, kind, lat, lon):
    return {"name": name, "detail": KINDS.get(kind, "Place") + " · added by riders",
            "lat": lat, "lon": lon, "source": "riders", "id": pid}


def add(body, client="?"):
    """Save a place a rider added; the same name close by is not added twice."""
    name = _name(body.get("name"))
    lat, lon = _where(body)
    kind = body.get("kind") if body.get("kind") in KINDS else "other"
    device = _device(body.get("device"))
    _rate("place", client)
    n = norm(name)
    with _lock:
        db = _connect()
        try:
            d = SAME_M / 111320.0
            for pid, pname, pkind, plat, plon in db.execute(
                    "SELECT id, name, kind, lat, lon FROM places WHERE norm = ? "
                    "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                    (n, lat - d, lat + d, lon - 2 * d, lon + 2 * d)):
                if _metres(lat, lon, plat, plon) <= SAME_M:
                    return dict(_result(pid, pname, pkind, plat, plon), merged=True)
            pid = db.execute("INSERT INTO places (name, norm, kind, lat, lon, device, created)"
                             " VALUES (?,?,?,?,?,?,?)",
                             (name, n, kind, lat, lon, device, time.time())).lastrowid
            db.commit()
        finally:
            db.close()
    return dict(_result(pid, name, kind, lat, lon), merged=False)


def search(text, near=None, limit=4):
    """Riders' places whose name has every typed word, nearest first."""
    words = re.findall(r"\w+", str(text or "").lower())
    if not words or not os.path.isfile(DB_PATH):
        return []
    db = _connect()
    try:
        rows = db.execute("SELECT id, name, kind, lat, lon FROM places WHERE lower(name) LIKE ? LIMIT 500",
                          ("%" + max(words, key=len) + "%",)).fetchall()
    finally:
        db.close()
    found = []
    for pid, name, kind, lat, lon in rows:
        low = name.lower()
        if all(w in low for w in words):
            km = _metres(near[0], near[1], lat, lon) / 1000.0 if near else 0.0
            found.append((not low.startswith(words[0]), km, _result(pid, name, kind, lat, lon)))
    found.sort(key=lambda f: (f[0], f[1]))
    return [f[2] for f in found[:limit]]


def _cli(argv):
    db = _connect()
    try:
        if "--remove" in argv:
            pid = int(argv[argv.index("--remove") + 1])
            gone = db.execute("DELETE FROM places WHERE id = ?", (pid,)).rowcount
            db.commit()
            print("Removed place #%d." % pid if gone else "No place #%d." % pid)
            return 0
        rows = db.execute("SELECT id, name, kind, lat, lon, created FROM places ORDER BY created DESC").fetchall()
        for pid, name, kind, lat, lon, created in rows:
            print("#%-5d %-40s %-18s %.5f,%.5f  %s" % (pid, name[:40], KINDS.get(kind, kind), lat, lon,
                                                     time.strftime("%d %b %Y", time.localtime(created))))
        print("%d place%s added by riders." % (len(rows), "" if len(rows) == 1 else "s"))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
