"""Bus times told by riders.

No open timetable exists for Kerala's KSRTC and private buses, so riders add
the times they know: "a KSRTC bus leaves Aluva stand toward Kakkanad at
7:40". Others confirm a time ("Seen it") or flag it ("Wrong"), and a time
that more riders flag than confirm is no longer shown.

A report is a boarding point, a point the bus heads toward, a departure time
and the days it runs. A trip is matched to reports at its boarding stop whose
bus heads the same way, so a time added by one rider shows up for everyone
boarding there in that direction - not only for the exact same trip.

Stored in data/timings.db (SQLite). This is riders' data, not a cache: it is
kept out of .cache/ so clearing the cache never loses it. No IP addresses are
stored; a device is known only by a hash of a random id its browser made.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "timings.db")

BOARD_RADIUS_M = 400        # reports this close to the stop count as "at" it
SAME_WAY_DEG = 50           # bus heading within this of the trip's heading
MIN_TOWARD_M = 300          # "toward" must be a real distance from the stop
SAME_TIME_MIN = 2           # reports this close in time are the same bus

OPERATORS = {"ksrtc": "KSRTC", "private": "Private bus", "kurtc": "KURTC (city AC)",
             "swift": "KSRTC Swift", "other": "Other"}
DAYS = {"daily": "Every day", "not_sunday": "Not on Sundays", "sunday": "Sundays only"}

# Abuse limits, per client address, kept in memory only.
LIMITS = {"report": (20, 3600), "vote": (120, 3600)}

_lock = threading.Lock()
_hits: dict = defaultdict(deque)


class TimingError(ValueError):
    """A report or vote that cannot be accepted; the message is for the rider."""


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY,
            board_name TEXT NOT NULL, board_lat REAL NOT NULL, board_lon REAL NOT NULL,
            toward_name TEXT NOT NULL, toward_lat REAL NOT NULL, toward_lon REAL NOT NULL,
            dep_min INTEGER NOT NULL,          -- minutes after midnight, local time
            days TEXT NOT NULL, operator TEXT NOT NULL, route TEXT NOT NULL DEFAULT '',
            device TEXT NOT NULL, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS reports_board ON reports(board_lat, board_lon);
        CREATE TABLE IF NOT EXISTS votes (
            report_id INTEGER NOT NULL, device TEXT NOT NULL, vote INTEGER NOT NULL,
            PRIMARY KEY (report_id, device));
    """)
    return db


def _metres(a_lat, a_lon, b_lat, b_lon):
    r = 6371000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _bearing(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dl = math.radians(b_lon - a_lon)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def _angle(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _device(raw):
    raw = str(raw or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", raw):
        raise TimingError("This browser has no rider id yet - reload the page and try again.")
    return hashlib.sha256(("vazhi:" + raw).encode()).hexdigest()[:32]


def _rate(kind, client):
    limit, window = LIMITS[kind]
    now = time.monotonic()
    with _lock:
        q = _hits[(kind, client)]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise TimingError("That is a lot in a short time - please try again in a while.")
        q.append(now)


def _place(p, what):
    try:
        lat, lon = float(p["lat"]), float(p["lon"])
    except (KeyError, TypeError, ValueError):
        raise TimingError("The %s is missing." % what) from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise TimingError("The %s is not a place on the map." % what)
    name = re.sub(r"\s+", " ", str(p.get("name") or "")).strip()[:80] or "Unnamed stop"
    return name, lat, lon


def _clock(text):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise TimingError("Give the time the bus leaves, like 07:40.")
    return int(m.group(1)) * 60 + int(m.group(2))


def add(body, client="?"):
    """Store one rider's report. Returns its id."""
    board = _place(body.get("board") or {}, "boarding stop")
    toward = _place(body.get("toward") or {}, "place the bus goes toward")
    if _metres(board[1], board[2], toward[1], toward[2]) < MIN_TOWARD_M:
        raise TimingError("The bus must head somewhere away from the stop.")
    dep = _clock(body.get("time"))
    days = body.get("days") or "daily"
    operator = body.get("operator") or "other"
    if days not in DAYS or operator not in OPERATORS:
        raise TimingError("Choose the days and who runs the bus from the lists.")
    route = re.sub(r"\s+", " ", str(body.get("route") or "")).strip()[:60]
    device = _device(body.get("device"))
    _rate("report", client)
    with _lock:
        db = _connect()
        try:
            cur = db.execute(
                "INSERT INTO reports (board_name, board_lat, board_lon, toward_name, toward_lat,"
                " toward_lon, dep_min, days, operator, route, device, created)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                board + toward + (dep, days, operator, route, device, time.time()))
            rid = cur.lastrowid
            # Telling a time is also vouching for it.
            db.execute("INSERT OR REPLACE INTO votes VALUES (?,?,1)", (rid, device))
            db.commit()
            return rid
        finally:
            db.close()


def vote(body, client="?"):
    """A rider confirms (+1) or flags (-1) a time; a second vote replaces the first."""
    try:
        ids = [int(i) for i in (body.get("ids") or [body.get("id")])][:20]
        v = int(body.get("vote"))
    except (TypeError, ValueError):
        raise TimingError("Which time is this about?") from None
    if v not in (1, -1) or not ids:
        raise TimingError("Which time is this about?")
    device = _device(body.get("device"))
    _rate("vote", client)
    with _lock:
        db = _connect()
        try:
            found = 0
            for rid in ids:
                if db.execute("SELECT 1 FROM reports WHERE id=?", (rid,)).fetchone():
                    db.execute("INSERT OR REPLACE INTO votes VALUES (?,?,?)", (rid, device, v))
                    found += 1
            if not found:
                raise TimingError("That time is no longer listed.")
            db.commit()
        finally:
            db.close()


def near(board, alight, device=None):
    """Times reported at `board` (lat, lon) for buses heading toward `alight`.

    Reports of the same bus (same operator, within a couple of minutes) are
    merged into one time, their confirmations added up. Sorted by time."""
    b_lat, b_lon = board
    heading = _bearing(b_lat, b_lon, alight[0], alight[1])
    dlat = BOARD_RADIUS_M / 111320.0
    dlon = dlat / max(0.1, math.cos(math.radians(b_lat)))
    me = None
    if device:
        try:
            me = _device(device)
        except TimingError:
            me = None
    db = _connect()
    try:
        rows = db.execute(
            "SELECT r.*, COALESCE(SUM(v.vote = 1), 0) AS ok, COALESCE(SUM(v.vote = -1), 0) AS bad,"
            " MAX(CASE WHEN v.device = ? THEN v.vote END) AS mine"
            " FROM reports r LEFT JOIN votes v ON v.report_id = r.id"
            " WHERE r.board_lat BETWEEN ? AND ? AND r.board_lon BETWEEN ? AND ?"
            " GROUP BY r.id ORDER BY r.dep_min",
            (me, b_lat - dlat, b_lat + dlat, b_lon - dlon, b_lon + dlon)).fetchall()
    finally:
        db.close()

    groups = []
    for r in rows:
        if _metres(b_lat, b_lon, r["board_lat"], r["board_lon"]) > BOARD_RADIUS_M:
            continue
        their = _bearing(r["board_lat"], r["board_lon"], r["toward_lat"], r["toward_lon"])
        to_alight = _metres(r["toward_lat"], r["toward_lon"], alight[0], alight[1])
        if _angle(their, heading) > SAME_WAY_DEG and to_alight > 1000:
            continue
        g = next((g for g in groups if g["operator_id"] == r["operator"]
                  and abs(g["minute"] - r["dep_min"]) <= SAME_TIME_MIN), None)
        if g is None:
            g = {"ids": [], "minute": r["dep_min"], "time": "%02d:%02d" % divmod(r["dep_min"], 60),
                 "operator_id": r["operator"], "operator": OPERATORS[r["operator"]],
                 "days_id": r["days"], "days": DAYS[r["days"]], "toward": r["toward_name"],
                 "board": r["board_name"], "route": r["route"], "ok": 0, "wrong": 0, "mine": None}
            groups.append(g)
        g["ids"].append(r["id"])
        g["ok"] += r["ok"]
        g["wrong"] += r["bad"]
        if r["mine"] is not None:
            g["mine"] = r["mine"]
        if not g["route"] and r["route"]:
            g["route"] = r["route"]
    # More riders say it is wrong than right (by two): stop showing it.
    return [g for g in groups if g["wrong"] < g["ok"] + 2]
