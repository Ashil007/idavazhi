"""Bus stop names and missing bus stops, from riders.

OpenStreetMap often has a bus stop under an old, odd or English-only name, or
has no stop at all where buses really stop. Riders fix that here:

* Rename: a rider says a stop is really called something else. Riders who
  later use that stop are asked what it is called, and pick from the names
  given so far (the map's own name included). Each rider backs one name per
  stop. A name replaces the map's once at least CONFIRM riders back it and
  more riders back it than any other name; until then the map's name stays.

* Add: a rider adds a stop the map is missing. Riders planning a trip near it
  are asked whether there really is a stop there. It is used for planning
  once at least CONFIRM riders say yes and more say yes than no; its name is
  the one most riders back.

Stored in data/stops.db - riders' data, never in .cache/ or git. Like bus
times (timings.py), a rider is known only by a hash of a random browser id.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import threading
import time

from .timings import ROOT, TimingError, _device, _metres, _rate

DB_PATH = os.path.join(ROOT, "data", "stops.db")

CONFIRM = 30              # riders who must agree before a name or stop is used
SAME_STOP_M = 40          # a new stop this close to another is the same stop
ASK_NEAR_M = 600          # riders planning this close to a new stop are asked
OVERRIDES_TTL_S = 60      # how often the planner re-reads the winning names

OSM_KEY = re.compile(r"(node|way|relation)/\d{1,15}")
NEW_KEY = re.compile(r"new/(\d{1,12})")

_lock = threading.Lock()
_cache = {"at": 0.0, "names": {}, "stops": []}


class StopError(TimingError):
    """A suggestion or answer that cannot be accepted; the message is for the rider."""


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript("""
        -- Map stops riders have named: the map's own name, for the tally.
        CREATE TABLE IF NOT EXISTS map_stops (
            key TEXT PRIMARY KEY, map_name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL);
        -- Stops riders added.
        CREATE TABLE IF NOT EXISTS new_stops (
            id INTEGER PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL,
            device TEXT NOT NULL, created REAL NOT NULL);
        -- Every name given for a stop, spelled as first given.
        CREATE TABLE IF NOT EXISTS names (
            key TEXT NOT NULL, norm TEXT NOT NULL, name TEXT NOT NULL, created REAL NOT NULL,
            PRIMARY KEY (key, norm));
        -- The one name each rider backs for a stop ('' = answered, backs none).
        CREATE TABLE IF NOT EXISTS name_votes (
            key TEXT NOT NULL, device TEXT NOT NULL, norm TEXT NOT NULL, at REAL NOT NULL,
            PRIMARY KEY (key, device));
        -- Is there really a stop here? (riders' added stops only)
        CREATE TABLE IF NOT EXISTS exist_votes (
            stop_id INTEGER NOT NULL, device TEXT NOT NULL, vote INTEGER NOT NULL,
            PRIMARY KEY (stop_id, device));
    """)
    return db


def norm(name):
    """Names that differ only in case, spaces or punctuation are one name."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", name.lower())).strip()


def _clean(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not 2 <= len(name) <= 60 or not norm(name):
        raise StopError("Give the stop's name (2 to 60 letters).")
    if re.search(r"https?://|www\.|<|>", name, re.I):
        raise StopError("A stop name cannot contain links.")
    return name


def _point(body):
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
    except (KeyError, TypeError, ValueError):
        raise StopError("Where is the stop?") from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise StopError("That is not a place on the map.")
    return lat, lon


def _back(db, key, device, name):
    """`device` backs `name` for stop `key` (replacing what it backed before)."""
    n = norm(name) if name else ""
    if n:
        db.execute("INSERT OR IGNORE INTO names VALUES (?,?,?,?)", (key, n, name, time.time()))
    db.execute("INSERT OR REPLACE INTO name_votes VALUES (?,?,?,?)", (key, device, n, time.time()))


def _tally(db, key, map_name=None):
    """[{name, norm, votes, map}] for a stop, most-backed first."""
    rows = db.execute(
        "SELECT n.name, n.norm, n.created, COUNT(v.device) AS votes FROM names n"
        " LEFT JOIN name_votes v ON v.key = n.key AND v.norm = n.norm"
        " WHERE n.key = ? GROUP BY n.norm", (key,)).fetchall()
    out = [{"name": r["name"], "norm": r["norm"], "votes": r["votes"], "created": r["created"],
            "map": False} for r in rows]
    if map_name:
        m = norm(map_name)
        hit = next((o for o in out if o["norm"] == m), None)
        if hit:
            hit["map"], hit["name"] = True, map_name
        else:
            out.append({"name": map_name, "norm": m, "votes": 0, "created": 0, "map": True})
    # Ties: the map's name first, then the name given first.
    out.sort(key=lambda o: (-o["votes"], not o["map"], o["created"]))
    return out


def _winner(tally):
    """The name riders have settled on: at least CONFIRM of them, and more
    than any other name. None while undecided."""
    if not tally or tally[0]["votes"] < CONFIRM:
        return None
    if len(tally) > 1 and tally[1]["votes"] >= tally[0]["votes"]:
        return None
    return tally[0]


def _invalidate():
    _cache["at"] = 0.0


# ------------------------------------------------------------------ writes

def rename(body, client="?"):
    """A rider says map stop `key` is called `name`. Returns the stop's tally."""
    key = str(body.get("key") or "")
    if not OSM_KEY.fullmatch(key):
        raise StopError("Which stop is this about?")
    name = _clean(body.get("name"))
    map_name = re.sub(r"\s+", " ", str(body.get("map_name") or "")).strip()[:80]
    lat, lon = _point(body)
    device = _device(body.get("device"))
    _rate("stop", client)
    with _lock:
        db = _connect()
        try:
            # The first report of a stop records the map's name for it; later
            # ones cannot change it.
            db.execute("INSERT OR IGNORE INTO map_stops VALUES (?,?,?,?)",
                       (key, map_name or "unnamed stop", lat, lon))
            row = db.execute("SELECT map_name FROM map_stops WHERE key=?", (key,)).fetchone()
            _back(db, key, device, name)
            db.commit()
            tally = _tally(db, key, row["map_name"])
        finally:
            db.close()
    _invalidate()
    return _public(tally)


def add(body, client="?"):
    """A rider adds a stop the map is missing. A stop added within
    SAME_STOP_M of one already added counts as a yes for that one instead."""
    lat, lon = _point(body)
    name = _clean(body.get("name"))
    device = _device(body.get("device"))
    _rate("stop", client)
    with _lock:
        db = _connect()
        try:
            d = SAME_STOP_M / 111320.0
            near = [r for r in db.execute(
                "SELECT id, lat, lon FROM new_stops WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                (lat - d, lat + d, lon - 2 * d, lon + 2 * d))
                if _metres(lat, lon, r["lat"], r["lon"]) <= SAME_STOP_M]
            if near:
                sid = near[0]["id"]
            else:
                sid = db.execute("INSERT INTO new_stops (lat, lon, device, created) VALUES (?,?,?,?)",
                                 (lat, lon, device, time.time())).lastrowid
            db.execute("INSERT OR REPLACE INTO exist_votes VALUES (?,?,1)", (sid, device))
            _back(db, "new/%d" % sid, device, name)
            db.commit()
        finally:
            db.close()
    _invalidate()
    return {"key": "new/%d" % sid, "merged": bool(near)}


def answer(body, client="?"):
    """A rider answers the question about a stop: the name they back (or
    none), and for an added stop whether it is really there."""
    key = str(body.get("key") or "")
    m_new = NEW_KEY.fullmatch(key)
    if not (m_new or OSM_KEY.fullmatch(key)):
        raise StopError("Which stop is this about?")
    name = body.get("name")
    name = _clean(name) if name else None
    exists = body.get("exists")
    device = _device(body.get("device"))
    _rate("stopvote", client)
    with _lock:
        db = _connect()
        try:
            if m_new:
                sid = int(m_new.group(1))
                if not db.execute("SELECT 1 FROM new_stops WHERE id=?", (sid,)).fetchone():
                    raise StopError("That stop is no longer listed.")
                if exists is not None:
                    db.execute("INSERT OR REPLACE INTO exist_votes VALUES (?,?,?)",
                               (sid, device, 1 if exists else -1))
                if exists is False:
                    name = None
                elif exists and not name:
                    # "Yes, there is a stop" with no name picked: back the name
                    # riders give most, unless this rider already backs one.
                    if db.execute("SELECT 1 FROM name_votes WHERE key=? AND device=? AND norm != ''",
                                  (key, device)).fetchone():
                        db.commit()
                        return
                    top = _tally(db, key)
                    name = top[0]["name"] if top else None
            elif not db.execute("SELECT 1 FROM map_stops WHERE key=?", (key,)).fetchone():
                raise StopError("No rider has named that stop yet.")
            _back(db, key, device, name)
            db.commit()
        finally:
            db.close()
    _invalidate()


# ------------------------------------------------------------------- reads

def _public(tally):
    return [{"name": t["name"], "votes": t["votes"], "map": t["map"]} for t in tally]


def _load():
    """The winning names of map stops, and riders' stops confirmed for use."""
    db = _connect()
    try:
        names = {}
        for r in db.execute("SELECT key, map_name FROM map_stops"):
            win = _winner(_tally(db, r["key"], r["map_name"]))
            if win and not win["map"]:
                names[r["key"]] = win["name"]
        stops = []
        for r in db.execute(
                "SELECT s.id, s.lat, s.lon, COALESCE(SUM(e.vote = 1), 0) AS yes,"
                " COALESCE(SUM(e.vote = -1), 0) AS no FROM new_stops s"
                " LEFT JOIN exist_votes e ON e.stop_id = s.id GROUP BY s.id"):
            if r["yes"] >= CONFIRM and r["yes"] > r["no"]:
                tally = _tally(db, "new/%d" % r["id"])
                if tally and tally[0]["votes"]:
                    stops.append({"id": "new/%d" % r["id"], "lat": r["lat"], "lon": r["lon"],
                                  "name": tally[0]["name"], "kind": "stop", "by_riders": True,
                                  "operator": None, "network": None, "ref": None})
        return names, stops
    finally:
        db.close()


def _current():
    now = time.monotonic()
    if now - _cache["at"] > OVERRIDES_TTL_S:
        try:
            _cache["names"], _cache["stops"] = _load()
        except sqlite3.Error:
            pass                       # keep the last good answer
        _cache["at"] = now
    return _cache["names"], _cache["stops"]


def apply(stops, bbox=None):
    """Riders' names on map stops, plus riders' confirmed stops inside
    `bbox` (south, west, north, east). Returns a new list; the map's name
    stays available as `map_name`."""
    names, extra = _current()
    out = []
    for s in stops:
        if s.get("id") in names:
            s = dict(s, map_name=s.get("name"), name=names[s["id"]])
        out.append(s)
    if bbox is not None:
        s_, w, n, e = bbox
        ids = {s.get("id") for s in out}
        out += [dict(x) for x in extra
                if s_ <= x["lat"] <= n and w <= x["lon"] <= e and x["id"] not in ids]
    return out


def ask(keys, points, device):
    """Questions for a rider about the stops of their trip (map stop `keys`
    that riders have renamed) and riders' stops near `points` that are not
    confirmed yet. Never about a stop the rider has already answered for."""
    try:
        me = _device(device)
    except TimingError:
        return []
    out = []
    db = _connect()
    try:
        for key in keys:
            if not OSM_KEY.fullmatch(key):
                continue
            row = db.execute("SELECT map_name, lat, lon FROM map_stops WHERE key=?", (key,)).fetchone()
            if not row or db.execute("SELECT 1 FROM name_votes WHERE key=? AND device=?",
                                     (key, me)).fetchone():
                continue
            tally = _tally(db, key, row["map_name"])
            if len(tally) < 2:
                continue                       # nobody has offered another name
            win = _winner(tally)
            out.append({"key": key, "kind": "name", "lat": row["lat"], "lon": row["lon"],
                        "map_name": row["map_name"],
                        "shown": win["name"] if win else row["map_name"],
                        "options": _public(tally), "needed": CONFIRM})
        for (plat, plon) in points:
            d = ASK_NEAR_M / 111320.0
            for r in db.execute(
                    "SELECT s.id, s.lat, s.lon, COALESCE(SUM(e.vote = 1), 0) AS yes,"
                    " COALESCE(SUM(e.vote = -1), 0) AS no FROM new_stops s"
                    " LEFT JOIN exist_votes e ON e.stop_id = s.id"
                    " WHERE s.lat BETWEEN ? AND ? AND s.lon BETWEEN ? AND ? GROUP BY s.id",
                    (plat - d, plat + d, plon - 2 * d, plon + 2 * d)):
                key = "new/%d" % r["id"]
                if (any(q["key"] == key for q in out)
                        or _metres(plat, plon, r["lat"], r["lon"]) > ASK_NEAR_M
                        or db.execute("SELECT 1 FROM exist_votes WHERE stop_id=? AND device=?",
                                      (r["id"], me)).fetchone()):
                    continue
                tally = _tally(db, key)
                if not tally:
                    continue
                out.append({"key": key, "kind": "new", "lat": r["lat"], "lon": r["lon"],
                            "options": _public(tally), "yes": r["yes"], "no": r["no"],
                            "needed": CONFIRM})
    finally:
        db.close()
    return out[:3]
