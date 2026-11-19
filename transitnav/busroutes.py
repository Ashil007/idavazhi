"""Where buses really run: the roads in OpenStreetMap's bus routes.

Around Kochi alone, mappers have drawn some 900 bus routes as OSM route
relations (private buses mostly, KSRTC too), each the exact roads a bus
takes. Their roads together are the bus network, and two things follow:

  * a stop is only boarded or left at if a mapped route passes it - a stop
    drawn on a lane where no route runs is skipped for one that is (where
    the area has routes mapped at all; elsewhere every stop counts), and
  * the bus line on the map follows those roads, not the street router's
    shortest path, which happily cuts through residential lanes.

The roads come from Overpass in fixed tiles, cached on disk like the stops
(preload.py fetches Kerala's on server start), and are kept in memory as a
graph (graph.Graph) for paths and nearness. A trip in an area whose tiles
are not on disk yet waits at most WAIT_S for them, then plans without
(the download carries on, for the next trip). Roads are taken as two-way:
the line is drawn, not driven, and one-way tags would not survive the
preload's block downloads.

Each route says whose buses run it (operator "KSRTC", "Private Owners"),
so the roads of KSRTC's routes and of private ones are kept apart too
(operator_share): a ride can be told to be by private bus, by KSRTC, or
by either.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from . import overpass
from .geo import haversine
from .graph import Graph

TILE_DEG = 0.1             # route data is cached in tiles this size (~11 km)
MAX_TRIP_M = 40000         # longer bus trips go by stands and main roads
SERVED_M = 50              # a stop this close to a mapped route is on it
# An end with no mapped route this close has no route data around it: there,
# every stop counts (the map simply lacks routes, not buses).
DATA_NEAR_M = 1500
# A path along the mapped routes may be this much longer than the street
# router's: buses do go round, but a gap in the data can force a silly detour.
DETOUR_FACTOR = 1.6
DETOUR_EXTRA_M = 3000
NEAR_ROUTE_M = 30          # a path point this close to a route is on it
WAIT_S = 12                # longest a plan waits for route tiles not on disk

_graphs: dict = {}         # tuple(tiles) -> Graph or None
_by_operator: dict = {}    # id(Graph) -> {"ksrtc": Graph or None, "private": ...}
_pending: dict = {}        # tuple(tiles) -> download in progress
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="busroutes")
GRAPHS_MAX = 8
OPERATORS = ("ksrtc", "private")
_KSRTC_RE = re.compile(r"ksrtc|kurtc|road transport corp|\bswift\b", re.I)
_PRIVATE_RE = re.compile(r"private|owners", re.I)


def build_query(bbox, timeout: int = 90) -> str:
    """Every bus route that crosses `bbox` - its tags (whose buses) and
    member roads - and those roads: their node lists and coordinates (the
    whole of each road, as a network tile has it)."""
    box = overpass.area_clause(bbox)
    nl = overpass.NL
    return ("[out:json][timeout:%d];" % timeout + nl
            + 'relation["route"="bus"](%s)->.r;' % box + nl
            + ".r out body qt;" + nl
            + "way(r.r)(%s);" % box + nl
            + "(._;>;);" + nl
            + "out skel qt;")


def operator_of(tags):
    """"ksrtc", "private" or None: whose buses run a route, from its tags."""
    text = " ".join(tags.get(k) or "" for k in ("operator", "network"))
    if _KSRTC_RE.search(text):
        return "ksrtc"
    if _PRIVATE_RE.search(text):
        return "private"
    return None


def parse(data):
    """An Overpass answer -> (nodes {id: (lat, lon)}, ways {id: [node ids]},
    {"ksrtc": {way ids}, "private": {way ids}})."""
    nodes, ways, kinds = {}, {}, {k: set() for k in OPERATORS}
    for el in data.get("elements", ()):
        kind = el.get("type")
        if kind == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])
        elif kind == "way" and len(el.get("nodes") or ()) > 1:
            ways[el["id"]] = el["nodes"]
        elif kind == "relation":
            op = operator_of(el.get("tags") or {})
            if op:
                kinds[op].update(m["ref"] for m in el.get("members", ())
                                 if m.get("type") == "way")
    return nodes, ways, kinds


def _graph_of(nodes, ways):
    g = Graph.from_osm(nodes, ways) if ways else None
    return g if g is not None and g.edge_count else None


def tiles_for(a, b, buffer_m):
    return overpass.tiles_for_corridor(a, b, buffer_m, TILE_DEG)


def _build(key, report):
    try:
        data = overpass.query_tiles(list(key), TILE_DEG, build_query, report, "bus routes")
    except Exception:  # noqa: BLE001 - no route data is never fatal: plan without it
        data = None
    g = None
    if data is not None:
        nodes, ways, kinds = parse(data)
        g = _graph_of(nodes, list(ways.values()))
        if g is not None:
            _by_operator[id(g)] = {op: _graph_of(nodes, [ways[i] for i in ids if i in ways])
                                   for op, ids in kinds.items()}
    with _lock:
        _pending.pop(key, None)
        if data is not None:       # a failed download is tried again next time
            if len(_graphs) >= GRAPHS_MAX:
                old = _graphs.pop(next(iter(_graphs)))
                _by_operator.pop(id(old), None)
            _graphs[key] = g
    return g


def graph(tiles, report=None, wait_s=WAIT_S):
    """The bus-route graph over `tiles`, or None if the area has no routes
    mapped, or they are not on disk and do not arrive within `wait_s`."""
    key = tuple(sorted(tiles))
    with _lock:
        if key in _graphs:
            return _graphs[key]
        future = _pending.get(key)
        if future is None:
            future = _pending[key] = _pool.submit(_build, key, report)
    try:
        return future.result(timeout=wait_s)
    except FuturesTimeout:
        return None


def distance_m(g, lat, lon, max_m):
    """Metres from (lat, lon) to the nearest mapped bus route, or None if
    none is within `max_m`."""
    if g is None:
        return None
    found = g._projections(lat, lon, max_m, limit=1)
    return min(off for _, _, off, _, _ in found) if found else None


def has_data(g, point):
    """Are bus routes mapped around `point` at all?"""
    return distance_m(g, point[0], point[1], DATA_NEAR_M) is not None


def served(g, stop):
    """Does a mapped bus route pass `stop`?"""
    return distance_m(g, stop["lat"], stop["lon"], SERVED_M) is not None


def path(g, a, b, street_m=None):
    """(coords, metres) along mapped bus routes from stop a to stop b, given
    as (lat, lon); None without one, or when it is a long detour on the
    street router's `street_m`."""
    if g is None:
        return None
    found = g.route_between(a, b, SERVED_M * 1.5, offnet_factor=3.0)
    if found is None:
        return None
    coords, metres = found
    if street_m and metres > street_m * DETOUR_FACTOR + DETOUR_EXTRA_M:
        return None
    return coords, metres


def operator_share(g, coords):
    """{"ksrtc": share, "private": share}: how much of a path runs along
    KSRTC's mapped routes and along private buses' (each 0..1). None
    without route data."""
    if g is None:
        return None
    ops = _by_operator.get(id(g)) or {}
    return {op: coverage(ops.get(op), coords) or 0.0 for op in OPERATORS}


def coverage(g, coords):
    """The share (0..1) of a path's length that runs along mapped bus
    routes; None without route data."""
    if g is None or len(coords) < 2:
        return None
    total = on = 0.0
    for p, q in zip(coords, coords[1:]):
        m = haversine(p[0], p[1], q[0], q[1])
        if m <= 0:
            continue
        total += m
        # A long segment is checked at a few points along it.
        n = max(1, int(m // 60))
        hits = 0
        for k in range(n):
            t = (k + 0.5) / n
            lat, lon = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
            if distance_m(g, lat, lon, NEAR_ROUTE_M) is not None:
                hits += 1
        on += m * hits / n
    return on / total if total else None

