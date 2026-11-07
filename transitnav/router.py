"""Street routing through public routing servers.

Walking, riding and bus legs only need a shortest path along streets, and a
routing server already holds the planet's street graph in memory: it answers
in well under a second, where downloading the same streets from Overpass and
building a graph locally takes tens of seconds to minutes.

Two independent servers, both run by FOSSGIS over OpenStreetMap data:

  * Valhalla (valhalla1.openstreetmap.de) - tried first. It has real
    pedestrian, motor_scooter and bus profiles, and a distance matrix that
    answers many places in one call.
  * OSRM (routing.openstreetmap.de) - the fallback. Foot and car profiles;
    car stands in for scooters and buses.

A server that stops answering is skipped for a few minutes, so an outage
costs one timeout rather than one per request. Every function returns None
when no server can answer, and the planner falls back to its own graph.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .geo import haversine, path_length

# Your own Valhalla server (tools/routing_server.py starts it in Docker with
# Kerala's map data): no internet, no queue, answers in milliseconds. It only
# knows Kerala, so elsewhere the public servers below take over.
LOCAL_VALHALLA = os.environ.get("TRANSITNAV_VALHALLA", "http://127.0.0.1:8002")
VALHALLA = "https://valhalla1.openstreetmap.de"
OSRM = "https://routing.openstreetmap.de/routed-%s/route/v1/driving/"
# Our profile names -> each server's own.
VALHALLA_COSTING = {"foot": "pedestrian", "scooter": "motor_scooter", "bus": "bus",
                    "car": "auto"}
OSRM_PROFILE = {"foot": "foot", "scooter": "car", "bus": "car", "car": "car"}

USER_AGENT = "vazhi-transit/1.0 (local journey planner; stdlib urllib)"
TIMEOUT_S = 6        # it normally answers in well under a second
COOLDOWN_S = 60
FAILS_TO_SIDELINE = 2

_down_until = {"local": 0.0, "valhalla": 0.0, "osrm": 0.0}
# The local server is either running or not: a refused connection is instant,
# so it is checked again sooner than a slow public server.
LOCAL_TIMEOUT_S = 3
LOCAL_COOLDOWN_S = 20
_fails: dict = {}
_state_lock = threading.Lock()
# Answers are remembered for the life of the server: re-planning a trip, or
# routing a leg whose distance was just measured, costs nothing.
_memo: dict = {}
MEMO_MAX = 512


class _NoRoute(Exception):
    """The server answered, but there is no path (not a server failure)."""


def _usable(backend: str) -> bool:
    with _state_lock:
        return time.time() >= _down_until[backend]


def _fetch(backend: str, url: str):
    """GET a JSON answer. Raises _NoRoute for a definite "no path", returns
    None (and sidelines the server) when it fails to answer at all."""
    with _state_lock:
        if url in _memo:
            return _memo[url]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Encoding": "gzip"})
    try:
        timeout = LOCAL_TIMEOUT_S if backend == "local" else TIMEOUT_S
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
        data = json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500 and exc.code != 429:
            raise _NoRoute() from None     # e.g. Valhalla 442 "no path found"
        data, why = None, "HTTP %d" % exc.code
    except Exception as exc:  # noqa: BLE001 - timeouts, refused connections, bad JSON
        data, why = None, "%s: %s" % (type(exc).__name__, exc)
    with _state_lock:
        if data is None:
            # One slow answer is not an outage: sideline a server only after
            # FAILS_TO_SIDELINE failures in a row, and only briefly.
            _fails[backend] = _fails.get(backend, 0) + 1
            if _fails[backend] >= FAILS_TO_SIDELINE:
                cooldown = LOCAL_COOLDOWN_S if backend == "local" else COOLDOWN_S
                _down_until[backend] = time.time() + cooldown
                _fails[backend] = 0
                if backend != "local":   # a stopped local server is normal, not news
                    print("  street router %s is not answering (%s); skipping it for %d s"
                          % (backend, why, cooldown), flush=True)
            return None
        _fails[backend] = 0
        if len(_memo) >= MEMO_MAX:
            _memo.pop(next(iter(_memo)))
        _memo[url] = data
    return data


def _decode_polyline6(text: str):
    """Valhalla's encoded polyline (precision 1e-6) -> [[lat, lon], ...]."""
    coords, index, lat, lon = [], 0, 0, 0
    while index < len(text):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(text[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        coords.append([lat / 1e6, lon / 1e6])
    return coords


def _valhalla_route(a, b, profile, backend="valhalla", base=VALHALLA):
    q = json.dumps({"locations": [{"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]}],
                    "costing": VALHALLA_COSTING[profile], "units": "kilometers"},
                   separators=(",", ":"))
    data = _fetch(backend, base + "/route?" + urllib.parse.urlencode({"json": q}))
    if data is None:
        return None
    line = []
    for leg in data["trip"]["legs"]:
        line += _decode_polyline6(leg["shape"])
    return line, data["trip"].get("summary", {}).get("time")


def _osrm_route(a, b, profile):
    url = (OSRM % OSRM_PROFILE[profile]) + "%.6f,%.6f;%.6f,%.6f?overview=full&geometries=geojson" % (
        a[1], a[0], b[1], b[0])
    data = _fetch("osrm", url)
    if data is None:
        return None
    if data.get("code") != "Ok" or not data.get("routes"):
        raise _NoRoute()
    best = data["routes"][0]
    return [[lat, lon] for lon, lat in best["geometry"]["coordinates"]], best.get("duration")


def route(a, b, profile: str, max_snap_m: float):
    """Shortest street path between two (lat, lon) points for `profile`
    ("foot", "scooter", "bus" or "car").

    Returns (coords, metres) with the short hops from each point onto its
    street included, or None if no server can answer or either point is more
    than `max_snap_m` from a street (a route that starts kilometres from
    where the rider is standing is not an answer).
    """
    found = route_timed(a, b, profile, max_snap_m)
    return None if found is None else found[:2]


def route_timed(a, b, profile: str, max_snap_m: float):
    """As route(), plus the server's travel time in seconds for that profile
    (road speeds, junctions, one-way streets) as a third item - None if the
    server gave none. `route` is this without the time."""
    backends = (
        ("local", lambda a, b, p: _valhalla_route(a, b, p, "local", LOCAL_VALHALLA)),
        ("valhalla", _valhalla_route),
        ("osrm", _osrm_route),
    )
    for backend, fn in backends:
        if not _usable(backend):
            continue
        try:
            found = fn(a, b, profile)
            line, secs = found if found else (None, None)
        except _NoRoute:
            if backend == "local":
                continue        # outside the local map's area: ask a public server
            return None
        except (KeyError, IndexError, TypeError, ValueError):
            line = None   # malformed answer: try the other server
        if not line:
            continue
        if (haversine(a[0], a[1], line[0][0], line[0][1]) > max_snap_m
                or haversine(b[0], b[1], line[-1][0], line[-1][1]) > max_snap_m):
            if backend == "local":
                # The local map ends at Kerala's border: a point beyond it is
                # snapped to the nearest road it has, far away. A public
                # server has the whole map.
                continue
            return None
        coords = [list(a)]
        for p in line + [list(b)]:
            if p != coords[-1]:
                coords.append(p)
        if len(coords) < 2:
            coords.append(list(b))
        return coords, path_length(coords), secs
    return None


def _road_ref(name: str) -> str:
    """"NH66" -> "NH 66", "SH1" -> "SH 1": how road numbers are written."""
    return re.sub(r"^(NH|SH|MDR|AH)\s*(\d)", r"\1 \2", name.strip())


def _via(trip, avoid=()):
    """"via NH 66" - the road(s) a route spends most of its length on. A
    name in `avoid` (another option's label) is skipped, so options that
    share their longest road are still told apart."""
    lengths = {}
    for leg in trip.get("legs", ()):
        for m in leg.get("maneuvers", ()):
            for name in m.get("street_names", ())[:1]:
                name = _road_ref(name)
                lengths[name] = lengths.get(name, 0.0) + m.get("length", 0.0)
    ranked = [n for n, _ in sorted(lengths.items(), key=lambda x: -x[1])]
    total = sum(lengths.values()) or 1.0
    picked = [n for n in ranked if n not in avoid][:2] or ranked[:2]
    if not picked:
        return None, None
    if len(picked) == 1 or lengths[picked[0]] / total >= 0.45:
        return "via " + picked[0], picked[0]
    return "via %s and %s" % (picked[0], picked[1]), picked[0]


def route_alternatives(a, b, profile: str, max_snap_m: float, count: int = 2):
    """Up to 1 + `count` different street routes from a to b, best first, as
    dicts {"coords", "metres", "seconds", "via"}. Only Valhalla offers
    alternatives (your own server first, the public one otherwise); returns
    [] if neither answers - callers then keep their single route."""
    q = json.dumps({"locations": [{"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]}],
                    "costing": VALHALLA_COSTING[profile], "units": "kilometers",
                    "alternates": count}, separators=(",", ":"))
    for backend, base in (("local", LOCAL_VALHALLA), ("valhalla", VALHALLA)):
        if not _usable(backend):
            continue
        try:
            data = _fetch(backend, base + "/route?" + urllib.parse.urlencode({"json": q}))
        except _NoRoute:
            continue
        if not data or "trip" not in data:
            continue
        trips = [data["trip"]] + [alt["trip"] for alt in data.get("alternates", ())
                                  if "trip" in alt]
        out, used = [], set()
        for trip in trips:
            line = []
            for leg in trip.get("legs", ()):
                line += _decode_polyline6(leg["shape"])
            if not line or (haversine(a[0], a[1], line[0][0], line[0][1]) > max_snap_m
                            or haversine(b[0], b[1], line[-1][0], line[-1][1]) > max_snap_m):
                continue        # e.g. the local map ends short of the far point
            coords = [list(a)] + [p for p in line if p] + [list(b)]
            via, lead = _via(trip, avoid=used)
            used.add(lead)
            out.append({"coords": coords, "metres": path_length(coords),
                        "seconds": trip.get("summary", {}).get("time"), "via": via})
        if out:
            return out
    return []


def _valhalla_matrix(point, others, profile, inbound, max_snap_m,
                     backend="valhalla", base=VALHALLA):
    here = [{"lat": point[0], "lon": point[1]}]
    there = [{"lat": lat, "lon": lon} for lat, lon in others]
    q = json.dumps({"sources": there if inbound else here,
                    "targets": here if inbound else there,
                    "costing": VALHALLA_COSTING[profile], "units": "kilometers"},
                   separators=(",", ":"))
    data = _fetch(backend, base + "/sources_to_targets?"
                  + urllib.parse.urlencode({"json": q}))
    if data is None:
        return None
    rows = data["sources_to_targets"]
    cells = [r[0] for r in rows] if inbound else rows[0]
    # Where each place joined the street network. Read from the top-level
    # lists: per-cell begin/end positions exist only for some algorithms
    # (the driving matrix has them, the walking one does not).
    snap = lambda w: (w["lat"], w["lon"])
    srcs, dsts = [snap(w) for w in data["sources"]], [snap(w) for w in data["targets"]]
    out = []
    for i, cell in enumerate(cells):
        if cell.get("distance") is None:
            out.append(None)
            continue
        o = others[i]
        start, end = (srcs[i], dsts[0]) if inbound else (srcs[0], dsts[i])
        frm, to = (o, point) if inbound else (point, o)
        hop_a = haversine(frm[0], frm[1], start[0], start[1])
        hop_b = haversine(to[0], to[1], end[0], end[1])
        if hop_a > max_snap_m or hop_b > max_snap_m:
            out.append(None)
        else:
            out.append(cell["distance"] * 1000.0 + hop_a + hop_b)
    return out


def distances(point, others, profile: str, inbound: bool = False, max_snap_m: float = 600.0):
    """Street distance in metres between `point` and each of `others`, all
    (lat, lon): from the point to each, or with `inbound` from each to the
    point (one-way streets make these differ for a vehicle).

    One matrix call to Valhalla when it is up; otherwise a few routes at a
    time from whichever server answers. Returns a list matching `others`
    (None where there is no route), or None if no server answered.
    """
    if not others:
        return []
    if _usable("local"):
        try:
            found = _valhalla_matrix(point, list(others), profile, inbound, max_snap_m,
                                     "local", LOCAL_VALHALLA)
            # All None means the point is outside the local map: ask a public server.
            if found is not None and any(f is not None for f in found):
                return found
        except (_NoRoute, KeyError, IndexError, TypeError, ValueError):
            pass
    if _usable("valhalla"):
        try:
            found = _valhalla_matrix(point, list(others), profile, inbound, max_snap_m)
            if found is not None:
                return found
        except (_NoRoute, KeyError, IndexError, TypeError, ValueError):
            pass
    legs = [(o, point) if inbound else (point, o) for o in others]
    with ThreadPoolExecutor(max_workers=3) as pool:
        found = list(pool.map(lambda ab: route(ab[0], ab[1], profile, max_snap_m), legs))
    if all(f is None for f in found) and not any(_usable(b) for b in ("local", "valhalla", "osrm")):
        return None
    return [None if f is None else f[1] for f in found]
