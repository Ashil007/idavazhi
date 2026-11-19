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
import socket
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

USER_AGENT = "idavazhi/1.0 (local journey planner; stdlib urllib)"

# Buses keep to the bigger roads. Left alone, the router's bus profile cuts
# through residential lanes whenever that is a little shorter - a path no
# bus takes. So a bus may use the small roads (Valhalla's local level:
# unclassified, residential, service) only this far from either end, where
# a stop or a stand can sit on one; in between it keeps to tertiary roads
# and bigger. Only the app's own server allows this (tools/routing_server.py
# turns it on). Where it finds no such path, or only one much longer than
# the plain route (a stop deep in the lanes), the plain bus route is used.
BUS_LANE_REACH_M = 1000
BUS_DETOUR_FACTOR = 1.3
BUS_DETOUR_EXTRA_M = 1000
# Valhalla stops expanding a level only once the search is beyond the
# distance AND has moved up from it more than max_up_transitions times, so
# 0 makes the distance count at once.
_BUS_HIERARCHY = lambda: {"1": {"max_up_transitions": 400, "expand_within_distance": 20000},
                          "2": {"max_up_transitions": 0,
                                "expand_within_distance": BUS_LANE_REACH_M}}


def _short_enough(kept_m, plain_m):
    """Is the main-road bus path no great detour on the plain one?"""
    return kept_m <= plain_m * BUS_DETOUR_FACTOR + BUS_DETOUR_EXTRA_M
TIMEOUT_S = 4        # it normally answers in well under a second
# A call still unanswered after this long means the server is struggling:
# other calls skip it instead of queueing behind it for TIMEOUT_S each.
SLOW_S = 2.0
COOLDOWN_S = 60
FAILS_TO_SIDELINE = 2

_down_until = {"local": 0.0, "valhalla": 0.0, "osrm": 0.0}
# The local server is either running or not: a refused connection is instant,
# so it is checked again sooner than a slow public server.
LOCAL_TIMEOUT_S = 3
LOCAL_COOLDOWN_S = 20
_fails: dict = {}
_state_lock = threading.Lock()
# backend -> start times of its calls still waiting for an answer.
_inflight: dict = {"local": [], "valhalla": [], "osrm": []}
# Answers are remembered for the life of the server: re-planning a trip, or
# routing a leg whose distance was just measured, costs nothing.
_memo: dict = {}
MEMO_MAX = 512


class _NoRoute(Exception):
    """The server answered, but there is no path (not a server failure)."""


_local_seen = {"at": 0.0, "up": False}


def local_available() -> bool:
    """Is the app's own route service answering? Checked at most every 30 s
    (for the health check)."""
    now = time.time()
    if now - _local_seen["at"] > 30:
        try:
            with urllib.request.urlopen(LOCAL_VALHALLA + "/status", timeout=1) as r:
                up = r.status == 200
        except Exception:  # noqa: BLE001
            up = False
        _local_seen.update(at=now, up=up)
    return _local_seen["up"]


_probe = {"at": 0.0, "up": False}
PROBE_EVERY_S = 10
PROBE_TIMEOUT_S = 0.3


def _local_listening() -> bool:
    """Is anything listening at LOCAL_VALHALLA? A running server on this PC
    accepts at once; asking it a real question when it is not running costs
    about 2 s on Windows, and every planning step would pay that."""
    now = time.time()
    if now - _probe["at"] < PROBE_EVERY_S:
        return _probe["up"]
    parts = urllib.parse.urlsplit(LOCAL_VALHALLA)
    try:
        socket.create_connection((parts.hostname, parts.port or 80), PROBE_TIMEOUT_S).close()
        up = True
    except OSError:
        up = False
    _probe.update(at=now, up=up)
    return up


def _usable(backend: str) -> bool:
    if backend == "local" and not _local_listening():
        return False
    with _state_lock:
        now = time.time()
        if now < _down_until[backend]:
            return False
        waiting = _inflight.get(backend)
        return not (waiting and now - min(waiting) > SLOW_S)


def _fetch(backend: str, url: str):
    """GET a JSON answer. Raises _NoRoute for a definite "no path", returns
    None (and sidelines the server) when it fails to answer at all."""
    with _state_lock:
        if url in _memo:
            return _memo[url]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Encoding": "gzip"})
    started = time.time()
    with _state_lock:
        _inflight.setdefault(backend, []).append(started)
    try:
        return _ask(backend, url, req)
    finally:
        with _state_lock:
            _inflight[backend].remove(started)


def _ask(backend, url, req):
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
        # "Too many requests" or a server error: it will say the same to the
        # next call, so stop asking at once.
        plain_no = True
    except Exception as exc:  # noqa: BLE001 - timeouts, refused connections, bad JSON
        data, why = None, "%s: %s" % (type(exc).__name__, exc)
        # A timeout has already cost the rider TIMEOUT_S; a second one on the
        # next step would cost it again, so one is enough to stop asking.
        # A refused connection means nothing is running there (on Windows
        # even finding that out takes about 2 s), so it is just as clear.
        reason = getattr(exc, "reason", exc)
        plain_no = ("timed out" in str(exc).lower() or isinstance(exc, TimeoutError)
                    or isinstance(reason, ConnectionRefusedError)
                    or getattr(reason, "winerror", None) == 10061)
    with _state_lock:
        if data is None:
            # Any other failure might be one bad answer, not an outage:
            # sideline only after FAILS_TO_SIDELINE in a row, and only briefly.
            _fails[backend] = _fails.get(backend, 0) + 1
            if plain_no or _fails[backend] >= FAILS_TO_SIDELINE:
                cooldown = LOCAL_COOLDOWN_S if backend == "local" else COOLDOWN_S
                already = time.time() < _down_until[backend]
                _down_until[backend] = time.time() + cooldown
                _fails[backend] = 0
                # Said once per outage; a stopped local server is normal, not news.
                if backend != "local" and not already:
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


def _bus_roads(profile):
    """Costing options keeping a bus off the lanes mid-route (see
    BUS_LANE_REACH_M), or None for other profiles."""
    if profile != "bus":
        return None
    return {"bus": {"hierarchy_limits": _BUS_HIERARCHY()}}


def _valhalla_route(a, b, profile, backend="valhalla", base=VALHALLA, options=None):
    req = {"locations": [{"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]}],
           "costing": VALHALLA_COSTING[profile], "units": "kilometers"}
    if options:
        req["costing_options"] = options
    q = json.dumps(req, separators=(",", ":"))
    data = _fetch(backend, base + "/route?" + urllib.parse.urlencode({"json": q}))
    if data is None:
        return None
    line = []
    for leg in data["trip"]["legs"]:
        line += _decode_polyline6(leg["shape"])
    return line, data["trip"].get("summary", {}).get("time")


def _local_route(a, b, profile):
    """The app's own server. A bus keeps to the main roads (BUS_LANE_REACH_M)
    unless that is no path or a long detour; both are asked (milliseconds)."""
    plain = _valhalla_route(a, b, profile, "local", LOCAL_VALHALLA)
    if profile != "bus" or not plain or not plain[0]:
        return plain
    try:
        kept = _valhalla_route(a, b, profile, "local", LOCAL_VALHALLA, _bus_roads(profile))
    except _NoRoute:
        return plain
    if kept and kept[0] and _short_enough(path_length(kept[0]), path_length(plain[0])):
        return kept
    return plain


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
        ("local", _local_route),
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


TRACE_POINTS = 400       # a path is thinned to about this many points to match


def trace(coords, profile: str):
    """Match a path drawn on the map (a bus route) onto the app's own
    server's roads: {"coords", "metres", "seconds", "via"} as from
    route_alternatives, or None when the server is not running or cannot
    match it. The local server only: this is a long request."""
    if len(coords) < 2 or not _usable("local"):
        return None
    step = max(1, len(coords) // TRACE_POINTS)
    pts = coords[::step]
    if pts[-1] != coords[-1]:
        pts.append(coords[-1])
    body = json.dumps({"shape": [{"lat": p[0], "lon": p[1]} for p in pts],
                       "costing": VALHALLA_COSTING[profile], "shape_match": "map_snap",
                       "units": "kilometers"}, separators=(",", ":")).encode()
    req = urllib.request.Request(LOCAL_VALHALLA + "/trace_route", data=body,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=LOCAL_TIMEOUT_S) as resp:
            trip = json.loads(resp.read().decode("utf-8", "replace"))["trip"]
        line = []
        for leg in trip["legs"]:
            line += _decode_polyline6(leg["shape"])
    except Exception:  # noqa: BLE001 - unmatched or not answering: the caller keeps its path
        return None
    if len(line) < 2:
        return None
    via, _ = _via(trip)
    return {"coords": line, "metres": path_length(line),
            "seconds": trip.get("summary", {}).get("time"), "via": via}


ROAD_RUN_POINTS = 600    # a path is thinned to about this many points for its road types


def road_runs(coords, profile: str = "bus"):
    """The roads a drawn path runs along, in order: [(road class, metres,
    name)], one entry per stretch of one class and name ("secondary", 3091,
    "Edappally Pukkattupady Road"). None when the app's own route service is
    not running or cannot match the path."""
    if len(coords) < 2 or not _usable("local"):
        return None
    step = max(1, len(coords) // ROAD_RUN_POINTS)
    pts = coords[::step]
    if pts[-1] != coords[-1]:
        pts.append(coords[-1])
    body = json.dumps({"shape": [{"lat": p[0], "lon": p[1]} for p in pts],
                       "costing": VALHALLA_COSTING[profile], "shape_match": "map_snap",
                       "filters": {"attributes": ["edge.road_class", "edge.length",
                                                  "edge.names"], "action": "include"}},
                      separators=(",", ":")).encode()
    req = urllib.request.Request(LOCAL_VALHALLA + "/trace_attributes", data=body,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=LOCAL_TIMEOUT_S * 3) as resp:
            edges = json.loads(resp.read().decode("utf-8", "replace"))["edges"]
    except Exception:  # noqa: BLE001 - unmatched or not answering: road types unknown
        return None
    runs = []
    for e in edges:
        cls, metres = e.get("road_class"), e.get("length", 0) * 1000
        name = (e.get("names") or [""])[0]
        if runs and runs[-1][0] == cls and runs[-1][2] == name:
            runs[-1][1] += metres
        else:
            runs.append([cls, metres, name])
    return [tuple(r) for r in runs] or None


def route_alternatives(a, b, profile: str, max_snap_m: float, count: int = 2):
    """Up to 1 + `count` different street routes from a to b, best first, as
    dicts {"coords", "metres", "seconds", "via"}. Only Valhalla offers
    alternatives (your own server first, the public one otherwise); returns
    [] if neither answers - callers then keep their single route."""
    req = {"locations": [{"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]}],
           "costing": VALHALLA_COSTING[profile], "units": "kilometers", "alternates": count}
    plain = json.dumps(req, separators=(",", ":"))
    tries = [("local", LOCAL_VALHALLA, plain), ("valhalla", VALHALLA, plain)]
    if _bus_roads(profile):
        # A bus on the main roads first (see BUS_LANE_REACH_M).
        tries.insert(0, ("local", LOCAL_VALHALLA, json.dumps(
            dict(req, costing_options=_bus_roads(profile)), separators=(",", ":"))))
    plain_m = None
    for backend, base, q in tries:
        if not _usable(backend):
            continue
        if q is not plain and plain_m is None:
            # How long the plain route is, to tell a detour (see _local_route).
            try:
                first = _valhalla_route(a, b, profile, "local", LOCAL_VALHALLA)
            except _NoRoute:
                first = None
            plain_m = path_length(first[0]) if first and first[0] else float("inf")
        try:
            data = _fetch(backend, base + "/route?" + urllib.parse.urlencode({"json": q}))
        except _NoRoute:
            continue
        if not data or "trip" not in data:
            continue
        trips = [data["trip"]] + [alt["trip"] for alt in data.get("alternates", ())
                                  if "trip" in alt]
        if q is not plain and plain_m is not None:
            line = []
            for leg in trips[0].get("legs", ()):
                line += _decode_polyline6(leg["shape"])
            if not line or not _short_enough(path_length(line), plain_m):
                continue        # a long detour round the lanes: the plain routes
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


# OSM road classes, biggest first, as the route service names them.
ROAD_RANK = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
             "residential", "service_other")
ROAD_NEAR_M = 30           # a stop this close to a road is on it
_road_class = {}           # (lat, lon) rounded -> class or None
ROAD_CLASS_MAX = 50000


def road_classes(points):
    """The biggest OSM road class ("trunk", "primary", "secondary", ...) within
    ROAD_NEAR_M of each (lat, lon), from the app's own route service. None
    for a point with no road that close, and for every point when the route
    service is not running (the public one is not asked: this is many points)."""
    keys = [(round(lat, 5), round(lon, 5)) for lat, lon in points]
    ask = [i for i, k in enumerate(keys) if k not in _road_class]
    if ask and _usable("local"):
        for start in range(0, len(ask), 100):
            chunk = ask[start:start + 100]
            body = json.dumps({"locations": [{"lat": points[i][0], "lon": points[i][1]}
                                             for i in chunk],
                               "costing": "bus", "verbose": True}).encode()
            req = urllib.request.Request(LOCAL_VALHALLA + "/locate", data=body,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=LOCAL_TIMEOUT_S) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001 - not answering: classes stay unknown
                break
            if len(_road_class) > ROAD_CLASS_MAX:
                _road_class.clear()
            for i, loc in zip(chunk, data):
                ranks = [ROAD_RANK.index(c) for c in (
                    (e.get("edge") or {}).get("classification", {}).get("classification")
                    for e in loc.get("edges") or [] if e.get("distance", 1e9) <= ROAD_NEAR_M)
                    if c in ROAD_RANK]
                _road_class[keys[i]] = ROAD_RANK[min(ranks)] if ranks else None
    return [_road_class.get(k) for k in keys]


JUNCTION_M = 100          # main roads meeting this close to a stop
_junction = {}             # (lat, lon) rounded -> number of main roads


def main_roads_meeting(points):
    """How many different main roads (trunk or primary: national and state
    highways) pass within JUNCTION_M of each (lat, lon): 2 or more at a big
    junction (Edappally: NH 66 and NH 544), 1 along a highway (Koonamthai).
    None for every point when the app's own route service is not running."""
    keys = [(round(lat, 5), round(lon, 5)) for lat, lon in points]
    ask = [i for i, k in enumerate(keys) if k not in _junction]
    if ask and _usable("local"):
        for start in range(0, len(ask), 100):
            chunk = ask[start:start + 100]
            body = json.dumps({"locations": [{"lat": points[i][0], "lon": points[i][1],
                                              "radius": JUNCTION_M} for i in chunk],
                               "costing": "bus", "verbose": True}).encode()
            req = urllib.request.Request(LOCAL_VALHALLA + "/locate", data=body,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=LOCAL_TIMEOUT_S) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001 - not answering: unknown
                break
            if len(_junction) > ROAD_CLASS_MAX:
                _junction.clear()
            for i, loc in zip(chunk, data):
                roads = []          # one set of names per road; aliases join up
                for e in loc.get("edges") or []:
                    if (e.get("edge") or {}).get("classification", {}).get(
                            "classification") not in ("motorway", "trunk", "primary"):
                        continue
                    names = {re.sub(r"[^a-z0-9]", "", n.lower())
                             for n in (e.get("edge_info") or {}).get("names") or []} - {""}
                    if not names:
                        continue
                    hit = [r for r in roads if r & names]
                    for r in hit:
                        roads.remove(r)
                        names |= r
                    roads.append(names)
                _junction[keys[i]] = len(roads)
    return [_junction.get(k) for k in keys]


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
