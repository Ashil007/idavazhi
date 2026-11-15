"""How crowded is the bus? From the riders on it.

A rider on a trip with the stop alarm on is asked once, after the bus has
been moving for a minute: Seats, Standing or Packed. The answer is kept
against the stop they boarded at, the direction they are going and the kind
of ride (bus, train, metro), so the next rider planning from that stop in that
direction sees it on their route options.

Nothing that says who sent it is kept: no account, no device code, no IP
address. Reports live only in memory and are forgotten after REPORT_TTL; a
restart forgets them all. (limits.py counts requests per network address to
stop a flood, in memory and only for the length of its window.)
"""

from __future__ import annotations

import math
import threading
import time

REPORT_TTL = 45 * 60          # a report is forgotten after this long
LEVELS = ("seats", "standing", "packed")
MODES = ("bus", "train", "metro")
CELL = 3                      # stop position rounded to 3 decimals (about 110 m)
SECTORS = 8                   # direction of travel: N, NE, E, ... (45 degrees each)
MAX_KEYS = 20000              # stops x directions kept at once
MAX_PER_KEY = 40              # newest reports kept per stop and direction

_lock = threading.Lock()
_reports: dict = {}           # key -> [(level, t), ...] oldest first


class CrowdError(ValueError):
    """A report or question that cannot be accepted; the message is for the rider."""


def _coord(body, lat_key, lon_key, what):
    try:
        lat, lon = float(body[lat_key]), float(body[lon_key])
    except (KeyError, TypeError, ValueError):
        raise CrowdError("Where is %s?" % what) from None
    if not (math.isfinite(lat) and math.isfinite(lon) and 6 <= lat <= 37 and 68 <= lon <= 98):
        raise CrowdError("That place is not in India.")
    return lat, lon


def _sector(a, b):
    """Compass sector (0..SECTORS-1) of the way from a to b."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = (math.degrees(math.atan2(x, y)) + 360) % 360
    return int(((bearing + 180 / SECTORS) % 360) // (360 / SECTORS))


def key_of(body):
    """The stop, the direction and the kind of ride a report or question is about."""
    mode = str(body.get("mode") or "")
    if mode not in MODES:
        raise CrowdError("Bus, train or metro?")
    stop = _coord(body, "lat", "lon", "the stop")
    toward = _coord(body, "tlat", "tlon", "the ride going")
    if abs(stop[0] - toward[0]) < 1e-5 and abs(stop[1] - toward[1]) < 1e-5:
        raise CrowdError("Which way is the ride going?")
    return "%s|%.*f,%.*f|%d" % (mode, CELL, stop[0], CELL, stop[1], _sector(stop, toward))


def _sweep(now):
    for k in list(_reports):
        kept = [r for r in _reports[k] if now - r[1] < REPORT_TTL]
        if kept:
            _reports[k] = kept
        else:
            del _reports[k]


MAX_AHEAD = 5                 # the next stops the bus reaches, crowded the same


def report(body, now=None):
    """Remember one rider's answer. Returns the keys it was kept under.

    The answer is about the bus right now, so it holds for the rider's
    boarding stop and for the next few stops ahead ("ahead": [{lat, lon}]),
    where other riders are waiting for this same bus going the same way.
    """
    now = time.time() if now is None else now
    level = str(body.get("level") or "")
    if level not in LEVELS:
        raise CrowdError("Seats, standing or packed?")
    keys = [key_of(body)]
    ahead = body.get("ahead") or []
    if not isinstance(ahead, list):
        raise CrowdError("Which stops are ahead?")
    for stop in ahead[:MAX_AHEAD]:
        if not isinstance(stop, dict):
            raise CrowdError("Which stops are ahead?")
        try:
            keys.append(key_of({"mode": body.get("mode"), "lat": stop.get("lat"), "lon": stop.get("lon"),
                                "tlat": body.get("tlat"), "tlon": body.get("tlon")}))
        except CrowdError:
            continue          # the last stop itself, or a bad one: the rest still count
    keys = list(dict.fromkeys(keys))
    with _lock:
        new = [k for k in keys if k not in _reports]
        if new and len(_reports) + len(new) > MAX_KEYS:
            _sweep(now)
            if len(_reports) + len(new) > MAX_KEYS:
                raise CrowdError("Too many reports right now - try again later.")
        for key in keys:
            q = _reports.setdefault(key, [])
            q.append((level, now))
            del q[:-MAX_PER_KEY]
    return keys


def summary(body, now=None):
    """What riders said lately at this stop, going this way.

    {"level": "packed", "reports": 3, "minutes_ago": 6}, or {"level": None}
    when nobody has said in the last REPORT_TTL. Newer answers count more: an
    answer loses weight steadily until it is forgotten.
    """
    now = time.time() if now is None else now
    key = key_of(body)
    with _lock:
        recent = [r for r in _reports.get(key, ()) if now - r[1] < REPORT_TTL]
    if not recent:
        return {"level": None}
    weight = {lvl: 0.0 for lvl in LEVELS}
    for level, t in recent:
        weight[level] += 1.0 - (now - t) / REPORT_TTL
    # Ties go to the more crowded answer: better to expect a full bus.
    level = max(LEVELS, key=lambda lvl: (weight[lvl], LEVELS.index(lvl)))
    newest = max(t for _, t in recent)
    return {"level": level, "reports": len(recent), "minutes_ago": int((now - newest) // 60)}


def clear():
    """Forget everything (tests)."""
    with _lock:
        _reports.clear()
