"""A weighted graph built from OSM ways, with A* shortest paths.

Nodes are OSM node ids; edge weights are metres. Edges are two-way unless a
way is one-way for the vehicle in question. The graph carries a coarse grid
spatial index (for snapping arbitrary lat/lon onto the network) and a
union-find component map (so we can reject unreachable pairs instantly instead
of running a doomed search across the whole city).
"""

from __future__ import annotations

import heapq
import math

from .geo import bearing, haversine, path_length, point_to_segment_m, M_PER_DEG_LAT, m_per_deg_lon

CELL_DEG = 0.004  # ~440 m grid cells
INF = float("inf")
# A free point may join the network on any segment this much farther away than
# its nearest one - enough to choose between the roads around a block, not
# enough to cut across the block to dodge a one-way street.
JOIN_SLACK_M = 60.0


class Graph:
    __slots__ = ("adj", "coords", "transfers", "_cells", "_segs", "_edges", "_parent", "_rank")

    def __init__(self):
        # Outgoing edges only: a one-way segment appears in one list, not both.
        self.adj: dict[int, list[tuple[int, float]]] = {}
        self.coords: dict[int, tuple[float, float]] = {}
        # (u, v) -> info for edges that are not physical track but a change of
        # track or line at a station. Both directions are stored.
        self.transfers: dict[tuple[int, int], dict] = {}
        self._cells: dict[tuple[int, int], list[int]] = {}
        self._segs: dict[tuple[int, int], list[tuple[int, int]]] = {}
        self._edges = 0
        self._parent: dict[int, int] = {}
        self._rank: dict[int, int] = {}

    # ---------------------------------------------------------------- build

    def add_node(self, nid: int, lat: float, lon: float) -> None:
        if nid not in self.coords:
            self.coords[nid] = (lat, lon)

    def add_edge(self, u: int, v: int, weight: float | None = None, direction: int = 0) -> None:
        """Join u and v. `direction` +1 allows only u->v, -1 only v->u, 0 both."""
        if u == v or u not in self.coords or v not in self.coords:
            return
        if weight is None:
            (alat, alon), (blat, blon) = self.coords[u], self.coords[v]
            weight = haversine(alat, alon, blat, blon)
        out_u = self.adj.setdefault(u, [])
        out_v = self.adj.setdefault(v, [])
        if direction >= 0:
            out_u.append((v, weight))
        if direction <= 0:
            out_v.append((u, weight))
        self._edges += 1
        # Components ignore direction. That keeps them a sound "cannot reach"
        # filter; a directed search still decides what is actually reachable.
        self._union(u, v)

    def add_transfer(self, u: int, v: int, weight: float, info: dict) -> None:
        """Join two nodes that no track connects, e.g. the platforms of two
        lines at an interchange station."""
        self.add_edge(u, v, weight)
        self.transfers[(u, v)] = info
        self.transfers[(v, u)] = info

    def orientation(self, nid: int):
        """Undirected heading of the track through a node, 0-180 degrees, or
        None if the node has no physical neighbour."""
        lat, lon = self.coords[nid]
        for nbr, _ in self.adj.get(nid, ()):
            if (nid, nbr) not in self.transfers:
                nlat, nlon = self.coords[nbr]
                return bearing(lat, lon, nlat, nlon) % 180.0
        return None

    @classmethod
    def from_osm(cls, nodes: dict[int, tuple[float, float]], ways, directions=None) -> "Graph":
        """`nodes` maps id -> (lat, lon); `ways` is a list of node-id lists;
        `directions` optionally gives each way's +1/0/-1 one-way flag."""
        g = cls()
        for i, way in enumerate(ways):
            direction = directions[i] if directions else 0
            prev = None
            for nid in way:
                pos = nodes.get(nid)
                if pos is None:
                    prev = None  # way leaves the downloaded area; break the chain
                    continue
                g.add_node(nid, pos[0], pos[1])
                if prev is not None:
                    g.add_edge(prev, nid, direction=direction)
                prev = nid
        g.build_index()
        return g

    def build_index(self) -> None:
        self._cells.clear()
        for nid, (lat, lon) in self.coords.items():
            if nid in self.adj:  # only index nodes that are actually connected
                self._cells.setdefault(
                    (int(math.floor(lat / CELL_DEG)), int(math.floor(lon / CELL_DEG))), []
                ).append(nid)

    @property
    def node_count(self) -> int:
        return len(self.adj)

    @property
    def edge_count(self) -> int:
        return self._edges

    def _build_segments(self) -> None:
        """Grid index of physical segments, built on first use: only the
        access-leg graphs snap onto segments, and the rail graphs can be large."""
        seen = set()
        coords = self.coords
        for u, out in self.adj.items():
            for v, _ in out:
                key = (u, v) if u < v else (v, u)
                if key in seen or (u, v) in self.transfers:
                    continue
                seen.add(key)
                (alat, alon), (blat, blon) = coords[key[0]], coords[key[1]]
                for cy in range(int(math.floor(min(alat, blat) / CELL_DEG)),
                                int(math.floor(max(alat, blat) / CELL_DEG)) + 1):
                    for cx in range(int(math.floor(min(alon, blon) / CELL_DEG)),
                                    int(math.floor(max(alon, blon) / CELL_DEG)) + 1):
                        self._segs.setdefault((cy, cx), []).append(key)

    # ------------------------------------------------------------ snapping

    def nearest_nodes(self, lat: float, lon: float, max_m: float = 1000.0, limit: int = 12):
        """All connected nodes within `max_m`, nearest first.

        Routing needs more than the single closest node: rail lines are mapped
        as one way per direction, so a station's nearest track node may sit on
        the opposite-direction track, in a component that cannot reach the
        destination. Callers route from all of them at once (see `route`).
        """
        if not self._cells:
            return []
        rings_lat = int(math.ceil(max_m / (CELL_DEG * M_PER_DEG_LAT))) + 1
        rings_lon = int(math.ceil(max_m / (CELL_DEG * m_per_deg_lon(lat)))) + 1
        clat = int(math.floor(lat / CELL_DEG))
        clon = int(math.floor(lon / CELL_DEG))

        found = []
        for dx in range(-rings_lat, rings_lat + 1):
            for dy in range(-rings_lon, rings_lon + 1):
                for nid in self._cells.get((clat + dx, clon + dy), ()):
                    nlat, nlon = self.coords[nid]
                    d = haversine(lat, lon, nlat, nlon)
                    if d <= max_m:
                        found.append((nid, d))
        found.sort(key=lambda t: t[1])
        return found[:limit]

    # ---------------------------------------------------------- components

    def _find(self, x: int) -> int:
        parent = self._parent
        root = x
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(x, x) != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def _union(self, a: int, b: int) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra == rb:
            return
        ka, kb = self._rank.get(ra, 0), self._rank.get(rb, 0)
        if ka < kb:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if ka == kb:
            self._rank[ra] = ka + 1

    # -------------------------------------------------------------- search

    def route(self, sources: dict, targets: dict):
        """Cheapest path from any source node to any target node.

        `sources` and `targets` map node -> extra metres: the cost of getting
        onto the network at that node, or off it. Searching all of them at once
        lets a slightly farther entry point win when it avoids a long detour,
        which picking the nearest node first and routing second cannot do.
        Returns (metres including those extras, [node ids]) or None.
        """
        sources = {n: c for n, c in sources.items() if n in self.adj}
        roots = {self._find(n) for n in sources}
        targets = {n: c for n, c in targets.items()
                   if n in self.adj and self._find(n) in roots}
        if not sources or not targets:
            return None

        coords = self.coords
        clat = sum(coords[n][0] for n in targets) / len(targets)
        clon = sum(coords[n][1] for n in targets) / len(targets)
        radius = max(haversine(clat, clon, coords[n][0], coords[n][1]) for n in targets)

        def h(nid):
            # Distance to the circle holding every target: never more than the
            # true remaining cost, so the search stays optimal.
            lat, lon = coords[nid]
            return max(0.0, haversine(lat, lon, clat, clon) - radius)

        best = dict(sources)
        came: dict[int, int] = {}
        heap = [(c + h(n), n) for n, c in sources.items()]
        heapq.heapify(heap)
        closed = set()
        done = None   # (total, node) of the cheapest arrival so far

        while heap:
            f, current = heapq.heappop(heap)
            if done is not None and f >= done[0]:
                break      # nothing left in the queue can beat it
            if current in closed:
                continue
            closed.add(current)
            gcur = best[current]
            if current in targets:
                total = gcur + targets[current]
                if done is None or total < done[0]:
                    done = (total, current)
            for nbr, w in self.adj[current]:
                if nbr in closed:
                    continue
                cand = gcur + w
                if cand < best.get(nbr, INF):
                    best[nbr] = cand
                    came[nbr] = current
                    heapq.heappush(heap, (cand + h(nbr), nbr))

        if done is None:
            return None
        node = done[1]
        path = [node]
        while node in came:
            node = came[node]
            path.append(node)
        path.reverse()
        return done[0], path

    def _has_arc(self, u: int, v: int) -> bool:
        return any(n == v for n, _ in self.adj.get(u, ()))

    def _projections(self, lat: float, lon: float, max_m: float, limit: int = 12):
        """Nearby segments with the point projected onto each, nearest first:
        [((u, v), t, offset_m, [plat, plon], segment_m)].

        Kept: up to `limit` segments within JOIN_SLACK_M of the nearest one,
        plus the nearest segment of each other component, so a point beside a
        stray disconnected footway can still reach the street network behind
        it. A farther segment of the *same* network is never a way in: the
        router must reach it along real streets.
        """
        if not self._segs:
            self._build_segments()
        rings_lat = int(math.ceil(max_m / (CELL_DEG * M_PER_DEG_LAT))) + 1
        rings_lon = int(math.ceil(max_m / (CELL_DEG * m_per_deg_lon(lat)))) + 1
        clat = int(math.floor(lat / CELL_DEG))
        clon = int(math.floor(lon / CELL_DEG))

        coords = self.coords
        seen = set()
        found = []
        for dy in range(-rings_lat, rings_lat + 1):
            for dx in range(-rings_lon, rings_lon + 1):
                for key in self._segs.get((clat + dy, clon + dx), ()):
                    if key in seen:
                        continue
                    seen.add(key)
                    (alat, alon), (blat, blon) = coords[key[0]], coords[key[1]]
                    off, t = point_to_segment_m(lat, lon, alat, alon, blat, blon)
                    if off <= max_m:
                        proj = [alat + (blat - alat) * t, alon + (blon - alon) * t]
                        found.append((key, t, off, proj, haversine(alat, alon, blat, blon)))
        found.sort(key=lambda s: s[2])

        kept, comps = [], set()
        for seg in found:
            comp = self._find(seg[0][0])
            close = seg[2] <= found[0][2] + JOIN_SLACK_M and len(kept) < limit
            if close or (comp not in comps and len(comps) < 6):
                kept.append(seg)
                comps.add(comp)
        return kept

    def route_between(self, a, b, max_m: float, offnet_factor: float = 1.0):
        """Shortest path between two free points given as (lat, lon).

        Each point joins the network where it projects onto a nearby segment,
        not at the nearest vertex: a straight road can have vertices hundreds
        of metres apart, and snapping to one would run the route past the
        point and back. One-way segments are respected when joining too.

        `offnet_factor` weights the short straight hop between a point and its
        segment, so the route prefers joining the road right beside a point to
        cutting across land to a farther one. Returns (coords, metres) with the
        plain geometric length, or None if no path exists.
        """
        starts = self._projections(a[0], a[1], max_m)
        ends = self._projections(b[0], b[1], max_m)
        if not starts or not ends:
            return None

        # Leaving a projection towards v travels u->v; arriving at one from u
        # travels u->v as well.
        sources: dict[int, tuple[float, list]] = {}
        for (u, v), t, off, proj, seg_m in starts:
            for node, along, legal in ((v, (1 - t) * seg_m, self._has_arc(u, v)),
                                       (u, t * seg_m, self._has_arc(v, u))):
                cost = off * offnet_factor + along
                if legal and cost < sources.get(node, (INF,))[0]:
                    sources[node] = (cost, proj)
        targets: dict[int, tuple[float, list]] = {}
        for (u, v), t, off, proj, seg_m in ends:
            for node, along, legal in ((u, t * seg_m, self._has_arc(u, v)),
                                       (v, (1 - t) * seg_m, self._has_arc(v, u))):
                cost = off * offnet_factor + along
                if legal and cost < targets.get(node, (INF,))[0]:
                    targets[node] = (cost, proj)

        best = None
        # Both points beside the same segment: ride along it without ever
        # reaching a vertex, which the node search alone would miss.
        for key, st, soff, sproj, seg_m in starts:
            for ekey, et, eoff, eproj, _ in ends:
                if key != ekey:
                    continue
                u, v = key
                if (et >= st and self._has_arc(u, v)) or (et <= st and self._has_arc(v, u)):
                    cost = (soff + eoff) * offnet_factor + abs(et - st) * seg_m
                    if best is None or cost < best[0]:
                        best = (cost, [list(a), sproj, eproj, list(b)])

        found = self.route({n: c for n, (c, _) in sources.items()},
                           {n: c for n, (c, _) in targets.items()})
        if found is not None and (best is None or found[0] < best[0]):
            _, path = found
            coords = ([list(a), sources[path[0]][1]] + self.path_coords(path)
                      + [targets[path[-1]][1], list(b)])
            best = (found[0], coords)
        if best is None:
            return None

        coords = [best[1][0]]
        for p in best[1][1:]:
            if p != coords[-1]:
                coords.append(p)
        return coords, path_length(coords)

    def path_coords(self, path):
        return [[self.coords[n][0], self.coords[n][1]] for n in path]
