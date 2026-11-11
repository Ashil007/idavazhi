"""Where is my train? From the riders on it.

A rider on a train who turns on "Share where this train is" (the trip alarm,
web/livetrain.js) sends their position about every 45 seconds, with the
train number and a random code made for that one trip - nothing that says
who they are. Anyone planning a trip with that train can then see where it
was last seen, and how many riders saw it there.

Reports live only in memory, for REPORT_TTL; nothing is written to disk and
no IP address is kept. Reports that can't be right are dropped: far from
every station the train calls at, or a jump faster than any train runs.
"""

from __future__ import annotations

import math
import re
import threading
import time

from . import limits, timetable
from .timings import _metres

REPORT_TTL = 20 * 60          # forget a report after this long
SHOW_MAX_AGE = 15 * 60        # older than this, the train is "not seen"
TOGETHER_M = 3000             # reports this close (and recent) are the same train
TOGETHER_S = 5 * 60
MAX_SPEED = 200 / 3.6         # m/s: faster than this between two reports is a bad fix
ROUTE_MARGIN_DEG = 0.3        # about 30 km around the train's stations
MAX_TRAINS = 5000
MAX_RIDERS = 60               # riders kept per train

NUMBER = re.compile(r"\d{4,5}")
TOKEN = re.compile(r"[a-f0-9]{16,40}")

_lock = threading.Lock()
_reports: dict = {}           # number -> {token: (lat, lon, t)}
_boxes: dict = {}             # number -> (south, north, west, east) from the timetable


class LiveError(ValueError):
    """A report or question that cannot be accepted; the message is for the rider."""


def _box(number):
    """The area the train runs through (its stations, plus a margin), or None
    if the timetable doesn't know it - then any place in India is accepted."""
    if not _boxes:
        tt = timetable.load()
        if tt is None:
            return None
        for train in tt.trains:
            pts = [tt.stations[s[0]][1:3] for s in train["stops"] if s[0] in tt.stations]
            pts = [p for p in pts if p[0] is not None and p[1] is not None]
            if pts:
                lats, lons = [p[0] for p in pts], [p[1] for p in pts]
                _boxes[train["number"]] = (min(lats) - ROUTE_MARGIN_DEG, max(lats) + ROUTE_MARGIN_DEG,
                                           min(lons) - ROUTE_MARGIN_DEG, max(lons) + ROUTE_MARGIN_DEG)
    return _boxes.get(number)


def _number(raw):
    number = str(raw or "").strip()
    if not NUMBER.fullmatch(number):
        raise LiveError("Which train is this?")
    return number


def report(body, client="?", now=None):
    """Remember one rider's position on a train. Returns whether it was kept."""
    number = _number(body.get("number"))
    token = str(body.get("token") or "")
    if not TOKEN.fullmatch(token):
        raise LiveError("Reload the page and try again.")
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
    except (KeyError, TypeError, ValueError):
        raise LiveError("Where is the train?") from None
    if not (math.isfinite(lat) and math.isfinite(lon) and 6 <= lat <= 37 and 68 <= lon <= 98):
        raise LiveError("That place is not in India.")
    try:
        limits.hit("trainreport", client)
    except limits.Limited:
        raise LiveError("Too many reports for now.") from None
    box = _box(number)
    if box and not (box[0] <= lat <= box[1] and box[2] <= lon <= box[3]):
        return False                                   # nowhere near this train's route
    now = time.time() if now is None else now
    with _lock:
        riders = _reports.setdefault(number, {})
        before = riders.get(token)
        if before and now > before[2]:
            if _metres(before[0], before[1], lat, lon) / (now - before[2]) > MAX_SPEED:
                return False                           # a GPS jump, not a train
        riders[token] = (lat, lon, now)
        _prune(now)
    return True


def _prune(now):
    """Drop old reports; keep memory bounded. Called with _lock held."""
    for number in list(_reports):
        riders = {k: v for k, v in _reports[number].items() if now - v[2] <= REPORT_TTL}
        if len(riders) > MAX_RIDERS:
            riders = dict(sorted(riders.items(), key=lambda kv: -kv[1][2])[:MAX_RIDERS])
        if riders:
            _reports[number] = riders
        else:
            del _reports[number]
    while len(_reports) > MAX_TRAINS:
        oldest = min(_reports, key=lambda n: max(v[2] for v in _reports[n].values()))
        del _reports[oldest]


def live(number, now=None):
    """Where train `number` was last seen: {"seen": False} or
    {"seen": True, "lat", "lon", "age_s", "riders"}. The newest report and
    the ones close to it (in place and time) are averaged; riders far away
    from it are on another day's run, or wrong."""
    number = _number(number)
    now = time.time() if now is None else now
    with _lock:
        fixes = [v for v in _reports.get(number, {}).values() if now - v[2] <= SHOW_MAX_AGE]
    if not fixes:
        return {"seen": False}
    newest = max(fixes, key=lambda f: f[2])
    group = [f for f in fixes if newest[2] - f[2] <= TOGETHER_S
             and _metres(newest[0], newest[1], f[0], f[1]) <= TOGETHER_M]
    return {"seen": True,
            "lat": round(sum(f[0] for f in group) / len(group), 5),
            "lon": round(sum(f[1] for f in group) / len(group), 5),
            "age_s": int(now - newest[2]),
            "riders": len(group)}


def reset():
    with _lock:
        _reports.clear()
