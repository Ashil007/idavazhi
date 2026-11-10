"""Journey planning: access -> line-haul -> access, over real OpenStreetMap data.

The shape of a plan is always the same three legs:

    origin --walk/ride--> nearest stop ==vehicle== nearest stop --walk/ride--> destination

Each leg is a genuine shortest path over a graph built from OSM ways: the two
access legs run over the street network for walking or for a two-wheeler, the
middle leg over that mode's own infrastructure (roads for bus, heavy rail for
train, subway/light rail for metro).
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from itertools import product

from . import overpass, router, stopnames, timetable
from .config import (
    ACCESS_MARGIN_FACTOR,
    ACCESS_MODES,
    ACCESS_MARGIN_MAX_M,
    ACCESS_MARGIN_MIN_M,
    ACCESS_SNAP_RADIUS_M,
    INTERCHANGE_PENALTY_M,
    INTERMEDIATE_STOP_TOLERANCE_M,
    LONG_ACCESS_WARN_M,
    MAX_WALK_TO_STOP_M,
    NETWORK_WIDEN_FACTOR,
    OFFNET_FACTOR,
    PARALLEL_TRACK_DEG,
    STOP_CANDIDATES,
    STOP_SHORTLIST,
    TRACK_SWITCH_PENALTY_M,
    TRANSFER_WALK_S,
    access_config,
    mode_config,
)
from .geo import (
    M_PER_DEG_LAT,
    bbox_around,
    corridor_polygon,
    format_distance,
    format_duration,
    haversine,
    m_per_deg_lon,
    path_length,
    point_to_segment_m,
    polygon_to_overpass,
)
from .graph import Graph


class PlanError(RuntimeError):
    """A journey that cannot be planned, with a message meant for the user.

    `title` is the few words the pop-up shows in large type ("Nearest bus stop
    is 10 km away"); `actions` are buttons that plan again another way, each
    {"label", "mode"?, "access"?}.
    """

    def __init__(self, message, title=None, actions=None):
        super().__init__(message)
        self.title = title
        self.actions = actions or []


TWO_WHEELER = {"label": "Use a two-wheeler", "access": "two_wheeler"}
# The two ways to a stop too far to walk to from the start.
BOOK_UBER = {"label": "Book an Uber", "access": "cab"}
OWN_TWO_WHEELER = {"label": "I'll use my own two-wheeler", "access": "two_wheeler"}
TRY_BUS = {"label": "Try a bus", "mode": "bus"}


def _stop_label(stop: dict, cfg: dict) -> str:
    return stop.get("name") or ("unnamed " + cfg["stop_noun"])


# How long a plan waits for map tiles that are not on disk yet. Overpass can
# take many minutes when busy; past this the plan stops with a clear message
# and the download carries on in the background for next time.
MAP_WAIT_S = 45.0
# Stops closer together than this are one place: riding between them is not a
# trip (two sides of one road often have two stops with the same name).
MIN_RIDE_M = 400.0
# Extra distance an unnamed stop is charged when choosing where to board.
UNNAMED_STOP_M = 150.0


def _fetch_tiles(tiles, deg, build, report=None, what="map data"):
    """overpass.query_tiles, but waiting at most MAP_WAIT_S."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(overpass.query_tiles, tiles, deg, build, report, what)
    pool.shutdown(wait=False)
    try:
        return future.result(timeout=MAP_WAIT_S)
    except FuturesTimeout:
        raise PlanError(
            "Map data for this area is still downloading - the free map service is "
            "busy. It carries on in the background: try again in a few minutes.",
            title="The map is still loading") from None


def _keep_stops(stops, cfg):
    """Drop stops a rider cannot use (see "skip_stop_names" in config)."""
    pattern = cfg.get("skip_stop_names")
    if not pattern:
        return stops
    return [s for s in stops if not re.search(pattern, s.get("name") or "", re.I)]


def _find_stops(origin, destination, cfg, acfg, report):
    """One Overpass call gives every stop of this mode in the travel corridor.

    Querying the whole corridor rather than two `around:` circles means the same
    response supplies the boarding stop, the alighting stop *and* the
    intermediate stops the vehicle passes, at no extra request cost.
    """
    buffer_m = max(cfg["stop_search_radii"][-1], cfg["network_margin_m"])
    report("stops", "Finding every %s along the way" % cfg["stop_noun"])
    tiles = overpass.tiles_for_corridor(origin, destination, buffer_m, cfg["tile_deg"])
    data = _fetch_tiles(
        tiles, cfg["tile_deg"],
        lambda box: overpass.build_stops_in_bbox_query(box, cfg["stop_filters"]),
        report, cfg["stop_noun"] + "s")
    # Riders' names for stops, and (buses) the stops riders added and confirmed.
    stops = _keep_stops(stopnames.apply(
        overpass.parse_stops(data),
        bbox_around([origin, destination], buffer_m) if cfg["vehicle"] == "bus" else None), cfg)
    if not stops:
        raise PlanError(
            "The map has no %ss anywhere near this route yet. Try another way to "
            "travel." % cfg["stop_noun"],
            title="No %ss on the map here" % cfg["stop_noun"],
            actions=[TRY_BUS] if cfg["vehicle"] != "bus" else [])

    # The limits in MODES are for walking; a two-wheeler covers several
    # times the distance in the same time, so it may reach farther stations.
    max_access = cfg.get("max_access_m", MAX_WALK_TO_STOP_M) * acfg.get("reach_factor", 1.0)
    if acfg["router"] == "foot" and cfg["vehicle"] != "bus":
        # A farther metro or train station is reached by bus or auto-rickshaw
        # (shown as such), not refused. Buses have stops everywhere.
        max_access = max(max_access, ROAD_ACCESS_MAX_M)

    def ranked(point, inbound):
        """The stops easiest to reach from `point` (or, `inbound`, to reach
        it from). Straight-line distance only makes the shortlist: a river or
        a railway with few crossings can make the nearest station on the map
        a far longer walk than the next one, so the shortlist is re-ranked by
        real walking or riding distance from the street router."""
        scored = []
        for s in stops:
            d = haversine(point[0], point[1], s["lat"], s["lon"])
            if d <= max_access:
                item = dict(s)
                item["distance_m"] = d
                scored.append(item)
        scored.sort(key=lambda s: s["distance_m"])
        scored = scored[:STOP_SHORTLIST]
        real = router.distances(point, [(s["lat"], s["lon"]) for s in scored],
                              acfg["router"], inbound, ACCESS_SNAP_RADIUS_M)
        if real is not None and any(m is not None for m in real):
            for s, m in zip(scored, real):
                s["distance_m"] = float("inf") if m is None else m
            # A stop with a name beats a nameless one a little nearer: the
            # rider has to find it, and "unnamed bus stop" helps nobody.
            scored.sort(key=lambda s: s["distance_m"] + (0 if s.get("name") else UNNAMED_STOP_M))
        elif real is not None and scored:
            # Not one stop reachable by road: the point itself is off the
            # road network (a forest, a field, a lake). Plan anyway, with the
            # first or last stretch as a straight-line estimate.
            for s in scored:
                s["distance_m"] *= DETOUR_FACTOR
                s["offroad"] = True
        return scored[:STOP_CANDIDATES]

    report("stops", "Comparing real %s distances to the nearest %ss"
           % ("walking" if acfg["router"] == "foot" else "riding", cfg["stop_noun"]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        near_origin, near_dest = pool.map(ranked, (origin, destination), (False, True))
    if not near_origin or not near_dest:
        end = "your start" if not near_origin else "your destination"
        point = origin if not near_origin else destination
        closest = min(stops, key=lambda s: haversine(point[0], point[1], s["lat"], s["lon"]))
        far = haversine(point[0], point[1], closest["lat"], closest["lon"])
        actions = []
        if not near_origin:
            # Too far to walk to from the start: an Uber or the rider's own
            # two-wheeler gets them to a farther stop.
            if acfg["router"] != "car":
                actions.append(BOOK_UBER)
            if acfg["router"] == "foot":
                actions.append(OWN_TWO_WHEELER)
        elif acfg["router"] == "foot":
            actions.append(TWO_WHEELER)
        if cfg["vehicle"] != "bus":
            actions.append(TRY_BUS)
        raise PlanError(
            "There is no %s within %s of %s. The closest one is %s." % (
                cfg["stop_noun"], format_distance(max_access), end,
                _stop_label(closest, cfg)),
            title="Nearest %s is %s away" % (cfg["stop_noun"], format_distance(far)),
            actions=actions)
    report("stops", "Found %s %ss nearby" % (format(len(stops), ","), cfg["stop_noun"]))
    return near_origin, near_dest, stops


def _stops_in(area, cfg, known):
    """Every stop of this mode in `area`, merged with the `known` list.

    A widened track search needs the stations along the wider corridor too:
    they are where lines are linked at interchanges, and where the rider is
    told the train calls."""
    if isinstance(area, dict):
        data = _fetch_tiles(
            area["tiles"], cfg["tile_deg"],
            lambda box: overpass.build_stops_in_bbox_query(box, cfg["stop_filters"]),
            what=cfg["stop_noun"] + "s")
    else:
        data = overpass.query(overpass.build_stops_in_bbox_query(area, cfg["stop_filters"]))
    ids = {s["id"] for s in known}
    return known + [s for s in stopnames.apply(overpass.parse_stops(data)) if s["id"] not in ids]


def _network_areas(origin, destination, direct, anchors, cfg, mode):
    """Where to fetch the vehicle network: a tight area first and, for rail,
    a much wider corridor to fall back on.

    The tight area keeps the usual download small. It is not enough on its
    own because a railway may bend far away from the straight line between
    two points before it reaches the far one, and a search that only holds
    part of the line reports "no route" where a route plainly exists.
    """
    margin = cfg["network_margin_m"]
    if mode == "bus":   # roads are dense enough that a bus never needs the detour
        if direct > 30000:
            return [polygon_to_overpass(
                corridor_polygon(origin, destination, max(margin, direct * 0.12)))]
        return [bbox_around(anchors, margin)]
    # Track comes in cached tiles first: every trip in the city shares them.
    deg = cfg["tile_deg"]
    if direct > 30000:
        first = overpass.tiles_for_corridor(origin, destination,
                                            max(margin, direct * 0.12), deg)
    else:
        first = overpass.tiles_for_bbox(bbox_around(anchors, margin), deg)
    # The wider corridor is tiles too, so a preloaded region answers it from disk.
    wide = overpass.tiles_for_corridor(
        origin, destination, max(3 * margin, direct * NETWORK_WIDEN_FACTOR), deg)
    extra = [t for t in wide if t not in set(first)]
    return [{"tiles": first}, {"tiles": first + extra}] if extra else [{"tiles": first}]


def _build_network(area, filters, excludes, report, what, oneway_keys=None, tile_deg=None):
    """Download and build a network. `area` is a bbox, a polygon, a list of
    bboxes, or {"tiles": [...]} for cached tiles of `tile_deg` degrees.

    This only runs where stops were already found, so the network cannot
    genuinely be empty. An empty answer here is Overpass dropping the query
    under load; retry rather than tell the user nothing is mapped, which is
    what an unguarded check would wrongly claim.
    """
    if isinstance(area, dict):
        data = _fetch_tiles(
            area["tiles"], tile_deg,
            lambda box: overpass.build_network_query(box, filters, excludes),
            report, "the %s network" % what)
        nodes, ways, directions = overpass.parse_network(data)
        if not ways:
            raise PlanError("The map has no %s around this trip yet." % what,
                            title="Part of the map is missing here")
        graph = Graph.from_osm(nodes, ways, directions)
        report("network", "Built a graph of %s nodes and %s edges"
               % (format(graph.node_count, ","), format(graph.edge_count, ",")))
        return graph

    report("network", "Downloading the %s network from OpenStreetMap" % what)
    ql = overpass.build_network_query(area, filters, excludes, oneway_keys=oneway_keys)
    ways = []
    for attempt in range(3):
        nodes, ways, directions = overpass.parse_network(overpass.query(ql), oneway_keys)
        if ways:
            break
        if attempt < 2:
            report("network", "Overpass returned no data, which usually means it "
                              "is overloaded. Retrying (%d of 2)" % (attempt + 1))
            time.sleep(3.0 * (attempt + 1))
    if not ways:
        raise PlanError(
            "The free map service did not send the %s for this area - it is "
            "probably busy. Please try again in a minute." % what,
            title="The map service is busy")
    graph = Graph.from_osm(nodes, ways, directions)
    report("network", "Built a graph of %s nodes and %s edges"
           % (format(graph.node_count, ","), format(graph.edge_count, ",")))
    return graph


def _link_interchanges(graph, stops, cfg):
    """Let the router change track or line at a station.

    For every station, the nearest track node of each distinct graph component
    within snapping range is joined to the nearest one overall. Whether that
    join is a mere switch to the other-direction track or a genuine change of
    line is decided by heading: parallel tracks are one line, crossing tracks
    are two. Returns how many line-change links were made.
    """
    # Group by the components as they were *before* any linking. Union-find
    # merges pieces as links are added, so grouping by live components would
    # link the two directions of a line at the first station only - and every
    # route would then have to ride out to that station to turn around.
    original = {nid: graph._find(nid) for nid in graph.adj}

    changes = 0
    for stop in stops:
        reps = {}
        for nid, _ in graph.nearest_nodes(stop["lat"], stop["lon"], cfg["snap_radius_m"], limit=60):
            reps.setdefault(original[nid], nid)
        if len(reps) < 2:
            continue
        nodes = list(reps.values())
        hub = nodes[0]
        hub_dir = graph.orientation(hub)
        hlat, hlon = graph.coords[hub]
        for other in nodes[1:]:
            olat, olon = graph.coords[other]
            gap = haversine(hlat, hlon, olat, olon)
            other_dir = graph.orientation(other)
            crossing = False
            if hub_dir is not None and other_dir is not None:
                diff = abs(hub_dir - other_dir) % 180.0
                crossing = min(diff, 180.0 - diff) >= PARALLEL_TRACK_DEG
            penalty = INTERCHANGE_PENALTY_M if crossing else TRACK_SWITCH_PENALTY_M
            graph.add_transfer(hub, other, gap + penalty,
                               {"station": stop.get("name"), "change": crossing})
            changes += crossing
    return changes


def _line_changes(graph, node_path):
    """Stations where the chosen path changes line, in travel order."""
    names = []
    for u, v in zip(node_path, node_path[1:]):
        info = graph.transfers.get((u, v))
        if info and info["change"]:
            name = info["station"] or "an unnamed interchange"
            if not names or names[-1] != name:
                names.append(name)
    return names


def _ride_seconds(cfg, ride_m, n_stops=0):
    """Moving time plus stops for a ride of `ride_m` passing `n_stops` stops.

    A mode with an "intercity" profile (buses) covers the first stretch at
    town pace, stopping at every stop, and the rest at intercity pace with
    a stop every few km - an express does not call at the 300 stops a
    200 km road passes."""
    ic = cfg.get("intercity")
    if not ic or ride_m <= ic["town_m"]:
        return ride_m / (cfg["speed_kmh"] / 3.6) + cfg["dwell_s"] * n_stops
    town, rest = ic["town_m"], ride_m - ic["town_m"]
    stops = n_stops * town / ride_m + rest / ic["stop_every_m"]
    return (town / (cfg["speed_kmh"] / 3.6) + rest / (ic["speed_kmh"] / 3.6)
            + cfg["dwell_s"] * stops)


def _wait_seconds(cfg, ride_m):
    """Allowance for waiting for the vehicle: long-distance buses run less often."""
    ic = cfg.get("intercity")
    return ic["wait_s"] if ic and ride_m > ic["from_m"] else cfg["wait_s"]


def _trip_seconds(board, alight, ride_m, n_changes, cfg, access_mps):
    """Rough door-to-door time for one choice of stops, used only to compare
    choices: the walks or rides at each end, the ride, and each change."""
    return ((board["distance_m"] + alight["distance_m"]) / access_mps
            + _ride_seconds(cfg, ride_m)
            + n_changes * (cfg["wait_s"] + TRANSFER_WALK_S))


def _usable_pairs(origin_stops, dest_stops):
    pairs = [(a, b) for a, b in product(origin_stops, dest_stops)
             if a["id"] != b["id"] and a["distance_m"] + b["distance_m"] < float("inf")
             and haversine(a["lat"], a["lon"], b["lat"], b["lon"]) >= MIN_RIDE_M]
    pairs.sort(key=lambda p: p[0]["distance_m"] + p[1]["distance_m"])
    return pairs


# A bus stops on both sides of the road, and the map usually has one stop for
# each side. Routing to the stop on the far side sends the bus past it to the
# next U-turn and back: Kodamthuruthu to Chandiroor Junction rode 1.1 km up
# NH 66 and back again. Where the ride passes this close to the stop with
# more than OVERSHOOT_M still to go, the ride ends (or starts) right there:
# the rider gets off across the road from the stop and crosses. The walk is
# still measured from the chosen stop - the map often has no crossing at a
# junction, and walking via the bus's U-turn is not what anyone does.
TWIN_STOP_M = 60.0
OVERSHOOT_M = 150.0


def _close_pass(coords, stop):
    """Where the path first passes within TWIN_STOP_M of `stop`, at the
    closest point of that pass: (segment index, point, gap in m), or None."""
    best = None
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        gap, t = point_to_segment_m(stop["lat"], stop["lon"], a[0], a[1], b[0], b[1])
        if gap <= TWIN_STOP_M:
            if best is None or gap < best[2]:
                best = (i, [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], gap)
        elif best is not None:
            break   # that pass is over
    return best


def _cut_after_pass(coords, stop):
    """`coords` ending where it first passes `stop`, then across to the stop,
    when the path goes on from there and comes back; unchanged otherwise."""
    hit = _close_pass(coords, stop)
    if hit is None:
        return coords
    i, point, gap = hit
    if path_length([point] + coords[i + 1:]) <= gap + OVERSHOOT_M:
        return coords   # the normal last few metres up to the stop
    return coords[:i + 1] + [point, [stop["lat"], stop["lon"]]]


def _no_overshoot(coords, board, alight):
    """The bus ride without a loop past either stop and back (see
    TWIN_STOP_M): (coords, share of the original length still ridden)."""
    full = path_length(coords)
    out = _cut_after_pass(coords, alight)
    out = _cut_after_pass(out[::-1], board)[::-1]
    return out, (path_length(out) / full if full else 1.0)


def _route_transit(graph, origin_stops, dest_stops, cfg, report, access_mps):
    """Pick a boarding/alighting pair and trace the vehicle path between them.

    Every pair of nearby stops joined by track or road is traced, and the one
    with the shortest door-to-door time wins: the nearest station is no use if
    it sends the rider the long way round. A nearer stop on an isolated
    fragment of the network is skipped rather than returned as a broken route.
    """
    snap = cfg["snap_radius_m"]
    best, dead = None, []
    for board, alight in _usable_pairs(origin_stops, dest_stops):
        cands_a = graph.nearest_nodes(board["lat"], board["lon"], snap, limit=16)
        cands_b = graph.nearest_nodes(alight["lat"], alight["lon"], snap, limit=16)
        # Search from every nearby track node at once, each costed by its
        # distance from the platform. Picking the single nearest connected
        # pair first goes wrong once interchanges are linked: everything is
        # then one component, and the nearest node at a big station can be
        # on a track that needs a long detour to reach (Indiranagar to
        # Majestic rode out to Baiyappanahalli and back: 12.3 km, not 8.0).
        found = graph.route(dict(cands_a), dict(cands_b)) if cands_a and cands_b else None
        if found is None:
            dead.append(board["distance_m"] + alight["distance_m"])
            continue
        _, node_path = found
        # Draw the leg from platform to platform, not from the track nodes.
        coords = ([[board["lat"], board["lon"]]] + graph.path_coords(node_path)
                  + [[alight["lat"], alight["lon"]]])
        if cfg["vehicle"] == "bus":
            coords, _ = _no_overshoot(coords, board, alight)
        # The search cost includes interchange penalties, so measure the
        # geometry for the distance the rider actually covers.
        ride_m = path_length(coords)
        changes = _line_changes(graph, node_path)
        secs = _trip_seconds(board, alight, ride_m, len(changes), cfg, access_mps)
        if best is None or secs < best[0]:
            best = (secs, board, alight, coords, ride_m, changes)
    if best is None:
        raise PlanError(
            "No %s line joins the stops near these two places. They may be on "
            "different lines, or the map may be missing part of the line."
            % cfg["label"].lower(),
            title="No %s route found" % cfg["label"].lower(),
            actions=[TRY_BUS] if cfg["vehicle"] != "bus" else [])
    _, board, alight, coords, ride_m, changes = best
    skipped = sum(d < board["distance_m"] + alight["distance_m"] for d in dead)
    if skipped:
        report("route", "Skipped %d closer stop pair(s) with no connecting %s"
               % (skipped, "track" if cfg["vehicle"] != "bus" else "road"))
    return board, alight, coords, ride_m, skipped, changes


def _route_bus_fast(origin_stops, dest_stops, cfg, report, access_mps):
    """The bus leg from the street router: well under a second, against a
    city-sized road download. The closest few stop pairs are routed at once
    and the quickest door to door wins. Returns what `_route_transit` does,
    or None to fall back to it."""
    pairs = _usable_pairs(origin_stops, dest_stops)[:4]
    report("route", "Tracing the bus route between stops")
    with ThreadPoolExecutor(max_workers=4) as pool:
        found = list(pool.map(
            lambda p: router.route_timed((p[0]["lat"], p[0]["lon"]), (p[1]["lat"], p[1]["lon"]),
                                       cfg["router"], cfg["snap_radius_m"] * 2), pairs))
    best = None
    for (board, alight), hit in zip(pairs, found):
        if hit is not None:
            coords, kept = _no_overshoot(hit[0], board, alight)
            metres = hit[1] * kept
            road_s = None if hit[2] is None else hit[2] * kept
            secs = ((board["distance_m"] + alight["distance_m"]) / access_mps
                    + _bus_ride_seconds(cfg, metres, road_s))
            if best is None or secs < best[0]:
                best = (secs, board, alight, coords, metres, road_s)
    if best is None:
        return None
    _, board, alight, coords, metres, road_s = best
    return board, alight, coords, metres, 0, [], road_s


def _bus_ride_seconds(cfg, ride_m, road_s, n_stops=0):
    """Bus time on the road: the router's travel time for the actual roads
    plus an allowance for stops, when it gave one; otherwise the distance
    estimate (_ride_seconds)."""
    factors = cfg.get("road_time_factor")
    if road_s is None or not factors:
        return _ride_seconds(cfg, ride_m, n_stops)
    ic = cfg.get("intercity")
    long_ride = ic is not None and ride_m > ic["from_m"]
    return road_s * factors["intercity" if long_ride else "town"]


def _likely_stops(stops, cfg, ride_m):
    """On a long bus ride, the stops a long-distance bus is likely to call
    at: every bus stand, and a named stop roughly every few km - not the
    hundred roadside stops the road passes. Short rides keep every stop."""
    ic = cfg.get("intercity")
    if not ic or ride_m <= ic["from_m"]:
        return stops
    kept, last = [], -float("inf")
    for s in stops:
        if s.get("kind") == "station" or (s.get("name") and
                                          s["along_m"] - last >= ic["list_every_m"]):
            kept.append(s)
            last = s["along_m"]
    return kept


def _driving(origin, destination):
    """(metres, seconds) by car door to door, for comparison - only from the
    local routing server, so it never slows a plan down; None otherwise."""
    if not router._usable("local"):
        return None
    try:
        found = router.route_timed(origin, destination, "car", ACCESS_SNAP_RADIUS_M)
    except Exception:  # noqa: BLE001 - a comparison is never worth failing a plan
        return None
    if found is None or found[2] is None:
        return None
    return found[1], found[2]


def _leg_seconds(acfg, metres):
    """Time for a walk or ride of `metres`; a cab adds the wait for it to arrive."""
    return metres / (acfg["speed_kmh"] / 3.6) + acfg.get("pickup_wait_s", 0)


def _access_leg_fast(access, acfg, start, end, label):
    """A walking or riding leg from the street router, or None."""
    found = router.route(start, end, acfg["router"], ACCESS_SNAP_RADIUS_M)
    if found is None:
        return None
    coords, metres = found
    return {
        "type": "walk" if access == "walk" else "ride",
        "access": access,
        "label": label,
        "geometry": coords,
        "distance_m": metres,
        "duration_s": _leg_seconds(acfg, metres),
        "approximate": False,
    }


def prefetch(point, mode):
    """Download the stops and track around one point into the cache, so a plan
    started from here does not wait on Overpass. Called in the background as
    soon as the user places a point; errors are ignored."""
    cfg = mode_config(mode)
    deg = cfg["tile_deg"]
    radius = max(cfg["stop_search_radii"][-1], cfg["network_margin_m"])
    tiles = overpass.tiles_for_bbox(bbox_around([point], radius), deg)
    overpass.query_tiles(tiles, deg,
                         lambda box: overpass.build_stops_in_bbox_query(box, cfg["stop_filters"]))
    if mode != "bus":   # buses use the street router, which needs no download
        overpass.query_tiles(tiles, deg, lambda box: overpass.build_network_query(
            box, cfg["network_filters"], cfg["network_exclude"]))


def _access_graph(legs, acfg, report):
    """One street graph covering both access legs, from a single download.

    `legs` are (from, to) point pairs. Each gets its own box, grown in
    proportion to its length so that a route forced to detour - to a bridge,
    a level crossing, round a one-way block - still lies inside the data. The
    two boxes become separate components of one graph, which is harmless and
    halves the number of round trips.

    Callers bound how long they wait for it (see _access_legs).
    """
    areas = []
    for a, b in legs:
        straight = haversine(a[0], a[1], b[0], b[1])
        margin = min(ACCESS_MARGIN_MAX_M,
                     max(ACCESS_MARGIN_MIN_M, straight * ACCESS_MARGIN_FACTOR))
        areas.append(bbox_around([a, b], margin))
    return _build_network(areas, acfg["filters"], acfg["exclude"], report,
                          acfg["network_noun"], acfg["oneway_keys"])


def _access_leg(graph, access, acfg, start, end, label):
    """Shortest path for walking or riding between two points.

    Only if no mapped way joins them at all - a point in the middle of a lake,
    say - is the leg drawn as a straight line, and it is flagged as such."""
    found = graph.route_between(start, end, ACCESS_SNAP_RADIUS_M, OFFNET_FACTOR)
    if found is not None:
        coords, metres = found
        approximate = False
    else:
        coords = [list(start), list(end)]
        metres = haversine(start[0], start[1], end[0], end[1])
        approximate = True
    return {
        "type": "walk" if access == "walk" else "ride",
        "access": access,
        "label": label,
        "geometry": coords,
        "distance_m": metres,
        "duration_s": _leg_seconds(acfg, metres),
        "approximate": approximate,
    }


def _intermediate_stops(all_stops, coords, board, alight, cfg):
    """Stops of this mode that the traced line actually passes.

    A stop counts when it lies within a short tolerance of the routed geometry;
    ordering is by distance travelled along that geometry, which gives the
    call-list a real vehicle would follow.

    Segments are indexed into a grid first. A dense city can return thousands of
    bus stops, and testing every stop against every segment would be a
    multi-million-operation scan on the request path.
    """
    tol = INTERMEDIATE_STOP_TOLERANCE_M
    if len(coords) < 2:
        return []

    cell = max(tol, 150.0)
    mid_lat = coords[len(coords) // 2][0]
    dlat = cell / M_PER_DEG_LAT
    dlon = cell / m_per_deg_lon(mid_lat)

    grid: dict = {}
    for i in range(1, len(coords)):
        a, b = coords[i - 1], coords[i]
        lo_y, hi_y = sorted((a[0], b[0]))
        lo_x, hi_x = sorted((a[1], b[1]))
        for cy in range(int(lo_y // dlat), int(hi_y // dlat) + 1):
            for cx in range(int(lo_x // dlon), int(hi_x // dlon) + 1):
                grid.setdefault((cy, cx), []).append(i)

    # Cumulative distance so a hit can be placed along the line.
    cumulative = [0.0]
    for i in range(1, len(coords)):
        cumulative.append(
            cumulative[-1]
            + haversine(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1])
        )

    skip_ids = {board["id"], alight["id"]}
    seen = []
    for stop in all_stops:
        if stop["id"] in skip_ids:
            continue
        cy, cx = int(stop["lat"] // dlat), int(stop["lon"] // dlon)
        candidates = set()
        for oy in (-1, 0, 1):
            for ox in (-1, 0, 1):
                candidates.update(grid.get((cy + oy, cx + ox), ()))
        if not candidates:
            continue
        best = (float("inf"), 0.0)
        for i in candidates:
            a, b = coords[i - 1], coords[i]
            dist, t = point_to_segment_m(stop["lat"], stop["lon"], a[0], a[1], b[0], b[1])
            if dist < best[0]:
                along = cumulative[i - 1] + (cumulative[i] - cumulative[i - 1]) * t
                best = (dist, along)
        if best[0] <= tol:
            seen.append({
                "id": stop["id"], "name": stop["name"], "kind": stop.get("kind"),
                "lat": stop["lat"], "lon": stop["lon"], "along_m": best[1],
            })

    seen.sort(key=lambda s: s["along_m"])

    def key(name):
        # "Indiranagar 6th main" and "Indiranagar 6th Main" are one place.
        return " ".join(name.lower().split()) if name else None

    # Stops on both sides of a road share a name, and a route that loops
    # round a block passes the same stop twice: list each place once, and
    # never repeat the boarding or alighting stop as a stop on the way.
    names = {key(board.get("name")), key(alight.get("name"))} - {None}
    deduped, last = [], None
    for s in seen:
        name = key(s["name"])
        too_close = last is not None and s["along_m"] - last <= 60.0
        if too_close or (name is not None and name in names):
            continue
        deduped.append(s)
        last = s["along_m"]
        if name is not None:
            names.add(name)
    return deduped


# How long a plan waits for streets from Overpass when the street router is
# down. Overpass can take minutes; past this the walk is estimated instead.
STREET_FALLBACK_S = 8.0
# Street distance per straight-line metre, for an estimated leg.
DETOUR_FACTOR = 1.4


def _estimated_leg(access, acfg, start, end, label):
    """A walk or ride drawn as a straight line, its length scaled up for the
    usual detours. Used only when no street data could be had in time."""
    metres = haversine(start[0], start[1], end[0], end[1]) * DETOUR_FACTOR
    return {
        "type": "walk" if access == "walk" else "ride",
        "access": access,
        "label": label,
        "geometry": [list(start), list(end)],
        "distance_m": metres,
        "duration_s": _leg_seconds(acfg, metres),
        "approximate": True,
        "estimated": True,
    }


def _access_legs(access, acfg, ends, report, offroad=(False, False)):
    """The walk or ride to the first stop and from the last, both at once
    from the street router.

    A leg the router cannot answer is routed over streets downloaded from
    Overpass - but the plan waits only STREET_FALLBACK_S for them. Past that
    the leg is a straight-line estimate, and the download carries on in the
    background so the next plan here gets the real route from the cache.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        quick = list(pool.map(lambda e: _access_leg_fast(access, acfg, *e), ends))
    # An end known to be off every road gets its estimate straight away,
    # rather than after waiting on a street download that cannot help.
    for n, off in enumerate(offroad):
        if off and quick[n] is None:
            quick[n] = _estimated_leg(access, acfg, *ends[n])
            quick[n]["offroad"] = True
    slow = [e for e, leg in zip(ends, quick) if leg is None]
    street = None
    if slow:
        report("access", "The street router did not answer, so fetching the streets instead")
        pool = ThreadPoolExecutor(max_workers=1)
        # The background download must not report into this request's
        # progress stream, which may be closed by the time it finishes.
        future = pool.submit(_access_graph, [(a, b) for a, b, _ in slow], acfg,
                             lambda *args: None)
        pool.shutdown(wait=False)
        try:
            street = future.result(timeout=STREET_FALLBACK_S)
        except FuturesTimeout:
            report("access", "The streets did not arrive in time, so the %s is estimated; "
                             "they keep downloading for next time" % acfg["label"].lower())
        except Exception:  # noqa: BLE001 - the download itself failed
            report("access", "The streets could not be downloaded, so the %s is estimated"
                   % acfg["label"].lower())
    out = []
    for (a, b, label), leg in zip(ends, quick):
        if leg is None:
            leg = (_access_leg(street, access, acfg, a, b, label) if street is not None
                   else _estimated_leg(access, acfg, a, b, label))
        out.append(leg)
    return out


# ---------------------------------------------------------- long-distance trains

TT_SHORTLIST = 40       # nearest stations searched (a big city has dozens close by)
TT_BIG_STATIONS = 5     # plus the busiest stations in reach, wherever they are
# Nobody walks 25 km to catch a long-distance train, but plenty of people take
# a bus or taxi that far. Stations up to this far away are considered, and
# any part beyond walking (or riding) range is costed and shown as a road
# trip at TT_ROAD_KMH.
TT_ROAD_REACH_M = 60000
TT_ROAD_KMH = 30.0          # only when no router gives a road time
ROAD_LEG_FACTOR = 1.25      # a bus or auto is slower than the router's car time
ROAD_LEG_WAIT_S = 300       # catching the bus or finding an auto
AUTO_MAX_M = 15000          # up to here an auto-rickshaw is the obvious choice
# Up to this far (by road) people walk to a station; beyond it they take a
# bus or an auto-rickshaw. One rule for choosing stations and for the legs
# shown, so the plan never picks a station on costs it then does not show.
WALK_MAX_M = 2500
# A cab is not worth booking for less than this; the stop is walked to instead.
CAB_MIN_M = 800
# How many route options a plan offers (the best plus alternatives).
MAX_OPTIONS = 3
# With a bus or auto for the first stretch, a metro or train station this far
# away is still a sensible place to start from.
ROAD_ACCESS_MAX_M = 10000


def _road_leg_seconds(metres):
    """Estimated bus/auto time for `metres` before the router is asked."""
    return metres / (TT_ROAD_KMH / 3.6) * ROAD_LEG_FACTOR + ROAD_LEG_WAIT_S


def _tt_access_leg(access, acfg, start, end, station_name, outbound, own_reach_m,
                   force_road=False):
    """Getting to or from a station. Within walking (riding) range it is an
    ordinary walk or ride; beyond it - or with `force_road` - a bus or
    auto-rickshaw (a taxi for long ones), timed from the router's road time.
    If no router answers, a straight line marked as an estimate - never a
    slow street download for a leg this long."""
    road = force_road or (
        haversine(start[0], start[1], end[0], end[1]) * DETOUR_FACTOR > own_reach_m)
    profile = "bus" if road else acfg["router"]
    found = router.route_timed(start, end, profile, ACCESS_SNAP_RADIUS_M)
    road_s = None
    if found is None:
        coords = [list(start), list(end)]
        metres = haversine(start[0], start[1], end[0], end[1]) * DETOUR_FACTOR
        approximate = True
    else:
        coords, metres, road_s = found
        approximate = False
    if road:
        how = "Bus or auto-rickshaw" if metres <= AUTO_MAX_M else "Bus or taxi"
        label = ("%s to %s" % (how, station_name)) if outbound else (
            "%s to your destination" % how)
        moving = road_s * ROAD_LEG_FACTOR if road_s else metres / (TT_ROAD_KMH / 3.6)
        return {"type": "ride", "access": "road", "label": label, "geometry": coords,
                "distance_m": metres, "duration_s": moving + ROAD_LEG_WAIT_S,
                "approximate": approximate}
    label = ("%s to %s" % (acfg["verb"], station_name)) if outbound else (
        "%s to your destination" % acfg["verb"])
    return {"type": "walk" if access == "walk" else "ride", "access": access, "label": label,
            "geometry": coords, "distance_m": metres,
            "duration_s": _leg_seconds(acfg, metres), "approximate": approximate}


def _clock(minutes: int) -> str:
    return "%02d:%02d" % (minutes % 1440 // 60, minutes % 60)


def _days_later(start: int, end: int) -> str:
    days = end // 1440 - start // 1440
    return "" if days <= 0 else (" next day" if days == 1 else " %d days later" % days)


def _train_line(tt, stops):
    """Map line for a ride: through every station the train passes, skipping
    any whose mapped location would throw the line far off course (a few
    stations in the dataset are placed in the wrong town)."""
    pts = [tt.station(s[0]) for s in stops]
    pts = [(p["lat"], p["lon"]) for p in pts if p]
    line = []
    for i, p in enumerate(pts):
        if line and 0 < i < len(pts) - 1:
            prev, nxt = line[-1], pts[i + 1]
            via = haversine(*prev, *p) + haversine(*p, *nxt)
            if via > 2.5 * haversine(*prev, *nxt) + 15000:
                continue
        line.append(p)
    return [list(p) for p in line]


def _plan_timetable(origin, destination, direct, mode, cfg, acfg, access, report):
    """A long train trip from the Indian Railways timetable: real trains,
    their times, and at most one change."""
    tt = timetable.load()
    if tt is None:
        report("stops", "Downloading the Indian Railways timetable (once, about 100 MB)")
        try:
            timetable.ensure(lambda m: None)
        except Exception as exc:  # noqa: BLE001
            print("  timetable download failed: %s" % exc)
            raise PlanError("The Indian Railways timetable could not be downloaded. "
                            "Check your internet connection and try again.",
                            title="Train timetable not available") from None
        tt = timetable.load()
        if tt is None:
            raise PlanError("The Indian Railways timetable could not be read.",
                            title="Train timetable not available")

    report("stops", "Finding railway stations with long-distance trains near both ends")
    served = tt.served()
    # After a day on a train nobody walks 8 km to their door: beyond a
    # comfortable walk the leg becomes a bus or taxi. A two-wheeler rider
    # keeps the usual riding range.
    if acfg["router"] == "foot":
        own_reach = WALK_MAX_M
    elif access == "cab":
        own_reach = TT_ROAD_REACH_M     # a cab goes to any station in reach
    else:
        own_reach = cfg.get("max_access_m", MAX_WALK_TO_STOP_M) * acfg.get("reach_factor", 1.0)
    max_access = max(own_reach, TT_ROAD_REACH_M)
    access_mps = acfg["speed_kmh"] / 3.6

    def seconds(m):
        # On foot (or two-wheeler) within reach; by bus or taxi beyond it.
        return _leg_seconds(acfg, m) if m <= own_reach else _road_leg_seconds(m)

    # A two-wheeler is parked at the first station, so the far end is always
    # a walk - or a bus or auto beyond walking range.
    wcfg = access_config("walk")

    def seconds_out(m):
        if acfg["router"] == "foot":
            return seconds(m)
        return m / (wcfg["speed_kmh"] / 3.6) if m <= WALK_MAX_M else _road_leg_seconds(m)

    def candidates(point, inbound):
        near = []
        for s in served:
            d = haversine(point[0], point[1], s["lat"], s["lon"])
            if d <= max_access:
                near.append((d, s))
        near.sort(key=lambda x: x[0])
        picked = near[:TT_SHORTLIST]
        # The nearest station may only see a few local trains; a junction a
        # little farther away often has the express that saves hours.
        busiest = sorted(near[TT_SHORTLIST:], key=lambda x: -x[1]["trains"])[:TT_BIG_STATIONS]
        picked += busiest
        # Straight line times a detour factor, not the street router: on a
        # trip of a day or two, a few minutes' error in reaching the station
        # cannot change which train is best, and a dozen router calls cost
        # seconds - or much longer when the public router is struggling.
        # Only the two legs finally chosen are routed.
        return {s["code"]: (d * DETOUR_FACTOR, s) for d, s in picked}

    with ThreadPoolExecutor(max_workers=2) as pool:
        near_o, near_d = pool.map(candidates, (origin, destination), (False, True))
    for near, point, end in ((near_o, origin, "your start"), (near_d, destination, "your destination")):
        if not near:
            closest = min(served, key=lambda s: haversine(point[0], point[1], s["lat"], s["lon"]))
            far = haversine(point[0], point[1], closest["lat"], closest["lon"])
            raise PlanError(
                "There is no railway station with trains within %s of %s. The closest "
                "one is %s." % (format_distance(max_access), end, closest["name"]),
                title="Nearest railway station is %s away" % format_distance(far),
                actions=([TWO_WHEELER] if acfg["router"] == "foot" else []) + [TRY_BUS])

    report("route", "Searching %s trains for the fastest connection"
           % format(len(tt.trains), ","))
    journeys = tt.search({c: seconds(m) for c, (m, _) in near_o.items()},
                         {c: seconds_out(m) for c, (m, _) in near_d.items()})
    if not journeys:
        raise PlanError(
            "No train runs between stations near these two places, directly or with "
            "one change.",
            title="No train for this trip",
            actions=([TWO_WHEELER] if acfg["router"] == "foot" else []) + [TRY_BUS])
    report("access", "Finding the shortest %s route to and from the stations"
           % acfg["label"].lower())
    car = _driving(origin, destination) if direct <= cfg["max_trip_m"] else None

    def build(journey):
        """One complete plan for one timetable journey."""
        rides = []
        for t, i, k in journey["rides"]:
            train = tt.train(t)
            stops = tt.trains[t]["stops"]
            board, alight = tt.station(stops[i][0]), tt.station(stops[k][0])
            dep, arr = stops[i][2], stops[k][1]
            line = _train_line(tt, stops[i:k + 1])
            calls = [tt.station(s[0]) for s in stops[i + 1:k] if s[3]]
            rides.append({
                "type": "transit",
                "mode": mode,
                "label": "%s %s" % (train["number"], train["name"]),
                "detail": "%s %s  \u2192  %s %s%s" % (board["name"], _clock(dep), alight["name"],
                                                     _clock(arr), _days_later(dep, arr)),
                "train": train,
                "departs": _clock(dep),
                "arrives": _clock(arr),
                "changes": [],
                "from": board,
                "to": alight,
                "geometry": line,
                "distance_m": path_length(line),
                "duration_s": (arr - dep) * 60,
                "wait_s": 0,
                "stops_passed": [{"id": c["id"], "name": c["name"], "lat": c["lat"], "lon": c["lon"]}
                                 for c in calls if c],
                "approximate": False,
            })

        first, last = rides[0]["from"], rides[-1]["to"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            access_leg, egress_leg = pool.map(
                lambda a: _tt_access_leg(*a),
                [(access, acfg, origin, (first["lat"], first["lon"]), first["name"], True,
                  own_reach),
                 ("walk", wcfg, (last["lat"], last["lon"]), destination, last["name"], False,
                  WALK_MAX_M)])

        legs = [access_leg]
        for n, ride in enumerate(rides):
            if n:
                legs.append({
                    "type": "wait",
                    "label": "Change at %s" % ride["from"]["name"],
                    "detail": "Arrive %s, next train leaves %s" % (rides[n - 1]["arrives"],
                                                                   ride["departs"]),
                    "geometry": [],
                    "distance_m": 0.0,
                    "duration_s": journey["waits"][n - 1] * 60,
                    "approximate": False,
                })
            legs.append(ride)
        legs.append(egress_leg)

        total_m = sum(l["distance_m"] for l in legs)
        total_s = sum(l["duration_s"] for l in legs)
        changes = [r["from"]["name"] for r in rides[1:]]

        notes, warnings = [], []
        if car is not None:
            notes.append("For comparison, driving straight there by car or taxi takes about "
                         "%s (%s)." % (format_duration(car[1]), format_distance(car[0])))
        walks = [l["distance_m"] for l in (access_leg, egress_leg) if l["type"] == "walk"]
        if walks and max(walks) > LONG_ACCESS_WARN_M:
            warnings.append({
                "title": "%s walk to the station" % format_distance(max(walks)),
                "text": "That is a long way on foot. A two-wheeler, bus or auto-rickshaw "
                        "to the station would be quicker.",
                "action": TWO_WHEELER})
        if any(l.get("access") == "road" for l in (access_leg, egress_leg)):
            warnings.append({
                "title": "The station is far away",
                "text": "It is too far to %s, so that part is shown as a bus or "
                        "auto-rickshaw ride (a taxi for long ones)." % (
                            "walk" if access == "walk" else "ride")})
        if access_leg["approximate"] or egress_leg["approximate"]:
            notes.append("No road was found near one end, so that part is a straight-line "
                         "estimate.")
        warnings.append({
            "title": "Check your train before you go",
            "text": "These train times are from an old (2016) timetable, and some trains "
                    "do not run every day. Check the train on IRCTC or NTES first."})
        notes.append("The total time starts when the first train leaves.")

        names = [r["train"]["number"] + " " + r["train"]["name"] for r in rides]
        option = names[0] if len(names) == 1 else "%s, change at %s" % (
            " then ".join(r["train"]["number"] for r in rides), changes[0])
        return {
            "mode": mode,
            "mode_label": cfg["label"],
            "color": cfg["color"],
            "access": access,
            "access_label": acfg["label"],
            "origin": {"lat": origin[0], "lon": origin[1]},
            "destination": {"lat": destination[0], "lon": destination[1]},
            "legs": legs,
            "notes": notes,
            "warnings": warnings,
            "timetable": True,
            "option_label": option,
            "summary": {
                "distance_m": total_m,
                "duration_s": total_s,
                "distance_text": format_distance(total_m),
                "duration_text": format_duration(total_s),
                # Only the rider's own walking or riding; a bus or auto to a
                # far station is not "on foot".
                "access_m": sum(l["distance_m"] for l in (access_leg, egress_leg)
                                if l.get("access") == access),
                "access_text": acfg["noun"],
                "ride_m": sum(r["distance_m"] for r in rides),
                "stops_passed": sum(len(r["stops_passed"]) for r in rides),
                "changes": len(changes),
                "direct_m": direct,
            },
        }

    # Like a map app: the best few journeys, fastest door to door first; the
    # page shows the rest as alternatives to switch to.
    options = sorted((build(j) for j in journeys[:MAX_OPTIONS]),
                     key=lambda o: o["summary"]["duration_s"])
    best = options[0]
    best["alternatives"] = options[1:]
    return best


def _bus_options(result, cfg, all_stops, board, alight, wait_s, report):
    """Like a map app: the bus ride along up to MAX_OPTIONS different roads
    between the same two stops, each a complete plan labelled by its main
    road ("via NH 66"), fastest door to door first. The rest ride along as
    `alternatives`. Without a Valhalla server answering, the plan is
    returned as it is."""
    report("route", "Looking for other roads the bus could take")
    found = router.route_alternatives((board["lat"], board["lon"]), (alight["lat"], alight["lon"]),
                                      cfg["router"], cfg["snap_radius_m"] * 2, MAX_OPTIONS - 1)
    if not found:
        return result
    n_transit = next(i for i, l in enumerate(result["legs"]) if l["type"] == "transit")
    options = []
    for k, alt in enumerate(found):
        coords, kept = _no_overshoot(alt["coords"], board, alight)
        alt = dict(alt, coords=coords, metres=alt["metres"] * kept,
                   seconds=None if alt["seconds"] is None else alt["seconds"] * kept)
        stops = _likely_stops(_intermediate_stops(all_stops, alt["coords"], board, alight, cfg),
                              cfg, alt["metres"])
        ride = _bus_ride_seconds(cfg, alt["metres"], alt["seconds"], len(stops)) + wait_s
        leg = dict(result["legs"][n_transit], geometry=alt["coords"], distance_m=alt["metres"],
                   duration_s=ride, stops_passed=stops, via=alt["via"])
        legs = list(result["legs"])
        legs[n_transit] = leg
        total_m = sum(l["distance_m"] for l in legs)
        total_s = sum(l["duration_s"] for l in legs)
        summary = dict(result["summary"], distance_m=total_m, duration_s=total_s,
                       distance_text=format_distance(total_m),
                       duration_text=format_duration(total_s),
                       ride_m=alt["metres"], stops_passed=len(stops))
        options.append(dict(result, legs=legs, summary=summary,
                            option_label=alt["via"] or "Route %d" % (k + 1)))
    options.sort(key=lambda o: o["summary"]["duration_s"])
    best = options[0]
    best["alternatives"] = options[1:]
    return best


def plan(origin, destination, mode, report=None, access="walk"):
    """Plan a journey. `origin`/`destination` are (lat, lon); `access` is how
    the rider reaches and leaves the stops ("walk" or "two_wheeler")."""
    try:
        cfg = mode_config(mode)
        acfg = access_config(access)
    except ValueError as exc:
        raise PlanError(str(exc)) from None   # a bad mode is a request error, not a crash
    report = report or (lambda *a: None)

    direct = haversine(origin[0], origin[1], destination[0], destination[1])
    if direct < 120:
        raise PlanError("Your start and destination are only a short walk apart.",
                        title="You are almost there")
    limit = cfg.get("long_trip_m", cfg["max_trip_m"])
    if direct > limit:
        raise PlanError(
            "These places are %s apart. Vazhi plans %s trips up to %s. Pick closer "
            "places, or another way to travel."
            % (format_distance(direct), cfg["label"].lower(), format_distance(limit)),
            title="Too far for a %s trip" % cfg["label"].lower())

    # Long train trips are planned from the Indian Railways timetable: real
    # trains and times, not a corridor of track downloaded around the trip.
    # Train trips go by the Indian Railways timetable - real trains and their
    # times - whenever a train in it fits. A shorter trip it cannot answer
    # (a line it lacks, a station with no listed trains) falls back to an
    # estimate along the OSM track.
    if mode == "train":
        try:
            return _plan_timetable(origin, destination, direct, mode, cfg, acfg, access, report)
        except PlanError:
            if direct > cfg["max_trip_m"]:
                raise
            report("route", "No timetable train fits this trip, so estimating from the "
                            "railway track instead")

    near_origin, near_dest, all_stops = _find_stops(origin, destination, cfg, acfg, report)

    # No stop reachable by road from one end: say so, rather than blame the stops.
    # Both ends served by the same stops: a vehicle cannot help here.
    if not _usable_pairs(near_origin, near_dest):
        raise PlanError(
            "Your start and destination use the same nearby %s (%s), so the %s "
            "would not take you anywhere."
            % (cfg["stop_noun"], _stop_label(near_origin[0], cfg), cfg["label"].lower()),
            title="Walking is better here")

    anchors = [origin, destination]
    anchors += [(s["lat"], s["lon"]) for s in near_origin + near_dest]
    what = "road" if mode == "bus" else cfg["label"].lower() + " track"
    areas = _network_areas(origin, destination, direct, anchors, cfg, mode)

    access_mps = acfg["speed_kmh"] / 3.6
    fast = (_route_bus_fast(near_origin, near_dest, cfg, report, access_mps)
            if mode == "bus" else None)
    road_s = None    # the street router's travel time for the bus's roads
    if fast is not None:
        board, alight, transit_coords, transit_m, skipped, changes, road_s = fast
        areas = []   # the street router answered; no road network download
    elif mode == "bus" and direct > cfg.get("road_fallback_max_m", float("inf")):
        raise PlanError(
            "The route service did not answer, so a bus trip this long cannot be "
            "worked out right now. Please try again in a minute. (If you run Vazhi "
            "yourself: open Docker Desktop, where the route service runs.)",
            title="Route service not answering")

    for attempt, area in enumerate(areas):
        if attempt:
            report("network", "No %s connection inside the first search area. Lines can "
                              "curve far from a straight path, so widening the search"
                   % what)
            all_stops = _stops_in(area, cfg, all_stops)
        graph = _build_network(area, cfg["network_filters"], cfg["network_exclude"],
                               report, what, tile_deg=cfg["tile_deg"])

        if mode != "bus":   # the road network is already one connected piece
            links = _link_interchanges(graph, all_stops, cfg)
            if links:
                report("network", "Linked %d interchange%s where lines cross"
                       % (links, "" if links == 1 else "s"))

        report("route", "Tracing the %s route between stops" % cfg["label"].lower())
        try:
            board, alight, transit_coords, transit_m, skipped, changes = _route_transit(
                graph, near_origin, near_dest, cfg, report, access_mps
            )
            break
        except PlanError:
            if attempt == len(areas) - 1:
                raise

    board_pt, alight_pt = (board["lat"], board["lon"]), (alight["lat"], alight["lon"])
    report("access", "Finding the shortest %s route to and from the %ss"
           % (acfg["label"].lower(), cfg["stop_noun"]))
    # A two-wheeler stays parked at the first stop, so the last stretch is
    # always on foot (or by bus or auto when it is too far to walk).
    wcfg = access_config("walk")
    ends = [(origin, board_pt, "%s to %s" % (acfg["verb"], _stop_label(board, cfg))),
            (alight_pt, destination, "%s to your destination" % wcfg["verb"])]
    offroad = (bool(board.get("offroad")), bool(alight.get("offroad")))
    if access == "walk":
        access_leg, egress_leg = _access_legs(access, acfg, ends, report, offroad)
    else:
        access_leg, = _access_legs(access, acfg, ends[:1], report, offroad[:1])
        egress_leg, = _access_legs("walk", wcfg, ends[1:], report, offroad[1:])
    # Nobody walks 4 km to a station when a bus or auto goes there:
    # beyond a comfortable walk, show that instead.
    legs = [access_leg, egress_leg]
    for n, (a, b, _label) in enumerate(ends):
        if legs[n].get("access") != "walk":
            continue
        if legs[n]["distance_m"] > WALK_MAX_M and not legs[n].get("offroad"):
            stop = board if n == 0 else alight
            legs[n] = _tt_access_leg("walk", wcfg, a, b, _stop_label(stop, cfg),
                                     n == 0, WALK_MAX_M, force_road=True)
    access_leg, egress_leg = legs
    # Nobody books a cab for a few hundred metres: a stop that close is a walk.
    short_cab = access == "cab" and access_leg.get("access") == "cab" and         access_leg["distance_m"] < CAB_MIN_M
    if short_cab:
        access_leg, = _access_legs("walk", wcfg, [
            (origin, board_pt, "%s to %s" % (wcfg["verb"], _stop_label(board, cfg)))],
            report, offroad[:1])

    stops_passed = _intermediate_stops(all_stops, transit_coords, board, alight, cfg)
    # Each change costs a platform walk plus another wait for the next train.
    stops_passed = _likely_stops(stops_passed, cfg, transit_m)
    wait_s = _wait_seconds(cfg, transit_m)
    ride_s = (_bus_ride_seconds(cfg, transit_m, road_s, len(stops_passed))
              + wait_s * (1 + len(changes))
              + TRANSFER_WALK_S * len(changes))

    label = "%s from %s to %s" % (cfg["label"], _stop_label(board, cfg), _stop_label(alight, cfg))
    if changes:
        label += ", changing at " + " and ".join(changes)

    transit_leg = {
        "type": "transit",
        "mode": mode,
        "label": label,
        "changes": changes,
        "from": board,
        "to": alight,
        "geometry": transit_coords,
        "distance_m": transit_m,
        "duration_s": ride_s,
        "wait_s": wait_s,
        "stops_passed": stops_passed,
        "approximate": False,
    }

    legs = [access_leg, transit_leg, egress_leg]
    total_m = sum(l["distance_m"] for l in legs)
    total_s = sum(l["duration_s"] for l in legs)

    notes, warnings = [], []
    if short_cab:
        notes.append("The stop is only a short walk away, so no cab is needed.")
    car = _driving(origin, destination)
    if car is not None:
        notes.append("For comparison, driving straight there by car or taxi takes about "
                     "%s (%s)." % (format_duration(car[1]), format_distance(car[0])))
    walk_s = direct * DETOUR_FACTOR / (ACCESS_MODES["walk"]["speed_kmh"] / 3.6)
    if walk_s < total_s:
        warnings.append({
            "title": "Walking is quicker",
            "text": "Walking straight there takes about %s - less than this %s trip."
                    % (format_duration(walk_s), cfg["label"].lower())})
    if changes:
        notes.append("You change lines at %s - follow the station signs to the other "
                     "platform." % " and ".join(changes))
    longest_walk = max([l["distance_m"] for l in (access_leg, egress_leg)
                        if l["type"] == "walk"] or [0])
    if any(l.get("access") == "road" for l in (access_leg, egress_leg)):
        warnings.append({
            "title": "The %s is far from your %s" % (
                cfg["stop_noun"], "start" if access_leg.get("access") == "road" else "destination"),
            "text": "It is more than %s away, so that part is shown as a bus or "
                    "auto-rickshaw ride instead of a long walk." % format_distance(WALK_MAX_M),
            "action": TWO_WHEELER if access == "walk" else None})
    if access == "walk" and longest_walk > LONG_ACCESS_WARN_M:
        warnings.append({
            "title": "%s walk to the %s" % (format_distance(longest_walk), cfg["stop_noun"]),
            "text": "That is a long way on foot. A two-wheeler would be quicker.",
            "action": TWO_WHEELER})
    street_m = access_leg["distance_m"] + egress_leg["distance_m"]
    if street_m > 2 * transit_m:
        warnings.append({
            "title": "The %s helps only a little" % cfg["label"].lower(),
            "text": "It covers just %s of this %s trip, because the line does not run "
                    "near one end. %s straight there may be quicker." % (
                        format_distance(transit_m), format_distance(total_m),
                        "Riding" if access != "walk" else "Another way, or a direct ride,"),
            "action": TRY_BUS if cfg["vehicle"] != "bus" else None})
    if access_leg.get("offroad") or egress_leg.get("offroad"):
        warnings.append({
            "title": "Your %s is away from any road" % (
                "start" if access_leg.get("offroad") else "destination"),
            "text": "It is more than %s from a mapped road or path, so the %s there is a "
                    "guess. Move the point onto a road for the exact way." % (
                        format_distance(ACCESS_SNAP_RADIUS_M),
                        "walk" if access == "walk" else "ride")})
    elif access_leg.get("estimated") or egress_leg.get("estimated"):
        notes.append("Part of the %s is a rough estimate (the route service was busy) - "
                     "plan again in a minute for the exact way." % (
                         "walk" if access == "walk" else "ride"))
    elif access_leg["approximate"] or egress_leg["approximate"]:
        notes.append(
            "One end of this trip has no mapped %s within %s, so that leg is "
            "drawn as a straight line." % (
                "footpath or road" if access == "walk" else "road",
                format_distance(ACCESS_SNAP_RADIUS_M),
            )
        )
    ic = cfg.get("intercity")
    long_bus = ic is not None and transit_m > ic["from_m"]
    if long_bus:
        notes.append("Shown is the road the bus takes. Bus times under the ride are added "
                     "by riders - if none are listed, ask at the bus stand, then add them.")
    notes.append("Times are estimates, including about %s of waiting for the %s."
                 % (format_duration(wait_s), cfg["vehicle"]))

    result = {
        "mode": mode,
        "mode_label": cfg["label"],
        "color": cfg["color"],
        "access": access,
        "access_label": acfg["label"],
        "origin": {"lat": origin[0], "lon": origin[1]},
        "destination": {"lat": destination[0], "lon": destination[1]},
        "legs": legs,
        "notes": notes,
        "warnings": warnings,
        "summary": {
            "distance_m": total_m,
            "duration_s": total_s,
            "distance_text": format_distance(total_m),
            "duration_text": format_duration(total_s),
            # The rider's own walking or riding; a bus or auto to a far stop
            # is not "on foot".
            "access_m": sum(l["distance_m"] for l in (access_leg, egress_leg)
                            if l.get("access") == access),
            "access_text": acfg["noun"],
            "ride_m": transit_m,
            "stops_passed": len(stops_passed),
            "changes": len(changes),
            "direct_m": direct,
        },
    }
    if mode == "bus" and road_s is not None:
        result = _bus_options(result, cfg, all_stops, board, alight, wait_s, report)
    return result
