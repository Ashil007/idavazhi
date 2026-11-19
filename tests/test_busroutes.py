"""OpenStreetMap's mapped bus routes (busroutes.py): which stops a route
passes, the bus line drawn along them, and the planner choosing stops on
them over a stop drawn on a lane no bus takes."""

import unittest
from unittest import mock

from transitnav import busroutes, config, planner
from transitnav.graph import Graph

# A bus route: a straight road east from (10.0, 76.30), about 5.5 km, as
# nodes every ~550 m.
LAT = 10.0
NODES = {i: (LAT, 76.30 + i * 0.005) for i in range(11)}
ROUTE = Graph.from_osm(NODES, [list(range(11))])
LANE_M = 0.0027        # degrees of latitude: about 300 m north of the road


def stop(sid, lat, lon, name="Stop"):
    return {"id": sid, "lat": lat, "lon": lon, "name": name, "kind": "stop",
            "distance_m": 100.0, "operator": None, "network": None}


class BusRoutesTest(unittest.TestCase):
    def test_served_only_beside_a_route(self):
        self.assertTrue(busroutes.served(ROUTE, stop("a", LAT + 0.0002, 76.312)))
        self.assertFalse(busroutes.served(ROUTE, stop("b", LAT + LANE_M, 76.312)))

    def test_area_without_routes_has_no_data(self):
        self.assertTrue(busroutes.has_data(ROUTE, (LAT + LANE_M, 76.31)))
        self.assertFalse(busroutes.has_data(ROUTE, (LAT + 0.05, 76.31)))
        self.assertFalse(busroutes.has_data(None, (LAT, 76.31)))

    def test_path_follows_the_route(self):
        coords, metres = busroutes.path(ROUTE, (LAT, 76.301), (LAT, 76.349))
        self.assertAlmostEqual(metres, 0.048 * 109600, delta=200)
        self.assertTrue(all(abs(p[0] - LAT) < 1e-6 for p in coords))

    def test_long_detour_is_refused(self):
        # The street router knows a road a fifth as long: a gap in the data.
        self.assertIsNone(busroutes.path(ROUTE, (LAT, 76.301), (LAT, 76.349), street_m=500))

    def test_coverage(self):
        on = [[LAT, 76.30], [LAT, 76.34]]
        half = [[LAT, 76.30], [LAT, 76.32], [LAT + LANE_M, 76.32], [LAT + LANE_M, 76.34]]
        self.assertGreater(busroutes.coverage(ROUTE, on), 0.95)
        self.assertLess(busroutes.coverage(ROUTE, half), 0.6)
        self.assertIsNone(busroutes.coverage(None, on))

    def test_query_asks_for_route_roads(self):
        q = busroutes.build_query((9.9, 76.2, 10.0, 76.3))
        self.assertIn('relation["route"="bus"]', q)
        self.assertIn("way(r.r)", q)
        self.assertIn(".r out body", q)       # each route's tags: whose buses

    def test_whose_buses(self):
        self.assertEqual(busroutes.operator_of({"operator": "KSRTC"}), "ksrtc")
        self.assertEqual(busroutes.operator_of({"operator": "Private Owners",
                                                "network": "Kochi city bus"}), "private")
        self.assertIsNone(busroutes.operator_of({"operator": "Kochi Metro"}))

    def test_routes_split_into_tiles_with_their_relations(self):
        from transitnav import overpass
        data = {"elements": [
            {"type": "node", "id": 1, "lat": 10.01, "lon": 76.31},
            {"type": "node", "id": 2, "lat": 10.01, "lon": 76.41},
            {"type": "node", "id": 3, "lat": 10.01, "lon": 76.51},
            {"type": "way", "id": 10, "nodes": [1, 2]},
            {"type": "way", "id": 11, "nodes": [2, 3]},
            {"type": "relation", "id": 5, "tags": {"operator": "KSRTC"},
             "members": [{"type": "way", "ref": 11, "role": ""}]},
        ]}
        parts = overpass.split_by_tile(data, 0.1, "routes")
        rels = {t for t, els in parts.items() if any(e["type"] == "relation" for e in els)}
        self.assertEqual(rels, {(100, 764), (100, 765)})       # where way 11 runs
        nodes, ways, kinds = busroutes.parse(data)
        self.assertEqual(kinds["ksrtc"], {11})
        self.assertEqual(set(ways), {10, 11})


class PlannerStopsTest(unittest.TestCase):
    """The rider's start has a stop on a lane right beside it and one on
    the bus route a little farther: the bus is boarded on the route."""

    def test_pair_on_the_route_wins(self):
        cfg = config.mode_config("bus")
        lane = stop("lane", LAT + LANE_M, 76.301, "Lane")
        on = stop("on", LAT, 76.304, "On the road")
        lane["distance_m"], on["distance_m"] = 50.0, 400.0
        far = stop("far", LAT, 76.349, "Far end")

        def road(a, b, profile, snap):
            # The street router: straight there, so the lane stop is quicker.
            from transitnav.geo import haversine
            m = haversine(a[0], a[1], b[0], b[1])
            return [list(a), list(b)], m, m / 8
        with mock.patch.object(planner.router, "route_timed", road):
            got = planner._route_bus_fast([lane, on], [far], cfg, lambda *a: None, 1.3,
                                          routes=ROUTE)
        board, alight, coords, metres, _, _, road_s, on_route = got
        self.assertEqual(board["id"], "on")
        self.assertTrue(on_route)
        self.assertTrue(all(abs(p[0] - LAT) < 1e-4 for p in coords))

    def test_without_routes_quickest_pair_wins(self):
        cfg = config.mode_config("bus")
        lane = stop("lane", LAT + LANE_M, 76.301, "Lane")
        on = stop("on", LAT, 76.304, "On the road")
        lane["distance_m"], on["distance_m"] = 50.0, 400.0
        far = stop("far", LAT, 76.349, "Far end")

        def road(a, b, profile, snap):
            from transitnav.geo import haversine
            m = haversine(a[0], a[1], b[0], b[1])
            return [list(a), list(b)], m, m / 8
        with mock.patch.object(planner.router, "route_timed", road):
            got = planner._route_bus_fast([lane, on], [far], cfg, lambda *a: None, 1.3)
        self.assertEqual(got[0]["id"], "lane")
        self.assertFalse(got[-1])


class MainRoadTest(unittest.TestCase):
    """Thuravoor to Cherthala stand: the mapped routes only cover the Vayalar
    road, so their line left NH 66 at Pattanakkad. Buses keep to NH 66: where
    the street router's line keeps to the main road and the mapped one does
    not, the router's line is the bus's road."""

    # 11 km due east along LAT: the main road (trunk).
    STREET = [[LAT, 76.30 + i * 0.001] for i in range(101)]
    # Up 450 m to a smaller road, along it, and back down: 11.9 km.
    MAPPED = ([[LAT + i * 0.001, 76.30] for i in range(5)]
              + [[LAT + 0.004, 76.30 + i * 0.001] for i in range(1, 101)]
              + [[LAT + 0.004 - i * 0.001, 76.40] for i in range(1, 5)])

    @staticmethod
    def classes(points):
        # Trunk along LAT, a secondary road anywhere north of it.
        return ["trunk" if lat < LAT + 0.0005 else "secondary" for lat, _ in points]

    def wins(self, street, mapped, classes=None):
        from transitnav.geo import path_length
        with mock.patch.object(planner.router, "road_classes", classes or self.classes):
            return planner._main_road_wins(street, path_length(street), mapped,
                                           path_length(mapped))

    def test_main_road_beats_a_mapped_side_road(self):
        self.assertTrue(self.wins(self.STREET, self.MAPPED))

    def test_mapped_line_kept_when_it_is_main_road_too(self):
        self.assertFalse(self.wins(self.STREET, self.MAPPED, lambda pts: ["trunk"] * len(pts)))

    def test_mapped_line_kept_when_road_types_are_unknown(self):
        self.assertFalse(self.wins(self.STREET, self.MAPPED, lambda pts: [None] * len(pts)))

    def test_a_much_shorter_road_is_another_way_not_a_gap(self):
        # Kaloor to Fort Kochi: the router's 9 km by the Vypin ferry against
        # the buses' 16 km. A mapped line half as long again stays.
        longer = ([[LAT + i * 0.001, 76.30] for i in range(41)]
                  + [[LAT + 0.04, 76.30 + i * 0.001] for i in range(1, 101)]
                  + [[LAT + 0.04 - i * 0.001, 76.40] for i in range(1, 41)])
        self.assertFalse(self.wins(self.STREET, longer))

    def test_short_rides_follow_the_mapped_routes(self):
        self.assertFalse(self.wins(self.STREET[:40], self.MAPPED[:20] + [self.STREET[39]]))

    def test_planner_draws_the_main_road(self):
        from transitnav.geo import path_length
        cfg = config.mode_config("bus")
        board, far = stop("b", LAT, 76.30), stop("e", LAT, 76.40)

        def road(a, b, profile, snap):
            return [list(p) for p in self.STREET], path_length(self.STREET), 900.0

        with mock.patch.object(planner.router, "route_timed", road), \
                mock.patch.object(planner.router, "road_classes", self.classes), \
                mock.patch.object(planner.busroutes, "path",
                                  lambda g, a, b, m=None: (self.MAPPED, path_length(self.MAPPED))):
            got = planner._route_bus_fast([board], [far], cfg, lambda *a: None, 1.3,
                                          routes=ROUTE)
        coords, trusted = got[2], got[-1]
        self.assertTrue(all(abs(p[0] - LAT) < 1e-6 for p in coords))   # along the main road
        self.assertTrue(trusted)


class ChangeBusesTest(unittest.TestCase):
    """A long ride whose line starts on a smaller road for 3 km before the
    highway is a local bus to the junction stop, then the long bus."""

    def setUp(self):
        # 40 km due east along LAT: 3 km on a secondary road, then the trunk.
        self.coords = [[LAT, 76.30 + i * 0.00092] for i in range(401)]   # ~100 m apart
        self.cfg = config.mode_config("bus")
        self.board = stop("b", LAT, 76.30, "Start Stop")
        self.alight = stop("e", LAT, 76.30 + 400 * 0.00092, "Far Stand")
        self.junction = stop("j", LAT + 0.0002, 76.30 + 30 * 0.00092, "Junction")
        self.lane = stop("l", LAT + 0.0002, 76.30 + 15 * 0.00092, "Lane Stop")

    def option(self):
        leg = {"type": "transit", "mode": "bus", "label": "x", "changes": [],
               "from": self.board, "to": self.alight, "geometry": self.coords,
               "distance_m": 40000.0, "duration_s": 4500.0, "wait_s": 900,
               "stops_passed": [{"name": "Lane Stop", "along_m": 1500.0, "lat": LAT, "lon": 76.31},
                                {"name": "Junction", "along_m": 3000.0, "lat": LAT, "lon": 76.33},
                                {"name": "Middle", "along_m": 20000.0, "lat": LAT, "lon": 76.48}],
               "operator": None, "via": "via NH 66"}
        return {"legs": [{"type": "walk", "access": "walk", "distance_m": 200.0, "duration_s": 150.0},
                         leg, {"type": "walk", "access": "walk", "distance_m": 100.0, "duration_s": 80.0}],
                "notes": [], "warnings": [], "access": "walk", "summary": {}}

    def runs(self, coords, profile="bus"):
        from transitnav.geo import path_length
        total = path_length(coords)
        start = coords[0][1] < 76.31        # the forward line starts on the small road
        return ([("secondary", 3000.0, "Small Road"), ("trunk", total - 3000.0, "NH 66")]
                if start else [("trunk", total, "NH 66")])

    def split(self):
        opt = self.option()
        with mock.patch.object(planner.router, "road_runs", self.runs),                 mock.patch.object(planner.router, "main_roads_meeting",
                                  lambda pts: [2 if p[1] > 76.32 else 0 for p in pts]),                 mock.patch.object(planner.busroutes, "graph", lambda *a, **k: None):
            planner._split_local_ends(opt, [self.board, self.alight, self.junction, self.lane],
                                      self.cfg)
        return opt

    def test_local_bus_then_long_bus(self):
        opt = self.split()
        rides = [l for l in opt["legs"] if l["type"] == "transit"]
        self.assertEqual(len(rides), 2)
        self.assertEqual(rides[0]["to"]["id"], "j")
        self.assertEqual(rides[1]["from"]["id"], "j")
        self.assertAlmostEqual(rides[0]["distance_m"], 3000, delta=150)
        self.assertAlmostEqual(sum(r["distance_m"] for r in rides), 40000, delta=1)
        self.assertEqual([s["name"] for s in rides[0]["stops_passed"]], ["Lane Stop"])
        self.assertEqual([s["name"] for s in rides[1]["stops_passed"]], ["Middle"])
        self.assertNotIn("via", rides[0])
        self.assertEqual(rides[1]["via"], "via NH 66")
        self.assertEqual(opt["summary"]["changes"], 1)
        self.assertEqual(opt["warnings"][0]["title"], "Change buses at Junction")
        self.assertIn("Small Road", opt["warnings"][0]["text"])

    def test_ride_on_main_roads_is_one_bus(self):
        self.runs = lambda coords, profile="bus": [("trunk", 40000.0, "NH 66")]
        opt = self.split()
        self.assertEqual(sum(l["type"] == "transit" for l in opt["legs"]), 1)

    def test_short_ride_is_one_bus(self):
        self.coords = self.coords[:101]          # 10 km: a local ride anyway
        opt = self.option()
        opt["legs"][1]["distance_m"] = 10000.0
        with mock.patch.object(planner.router, "road_runs", self.runs):
            planner._split_local_ends(opt, [self.junction], self.cfg)
        self.assertEqual(sum(l["type"] == "transit" for l in opt["legs"]), 1)


class ReboardTest(unittest.TestCase):
    """A road option whose bus first loops north and back west past the
    rider: it is boarded on the loop, not at the stop it starts from."""

    O = (10.003, 76.300)                  # the rider's start
    FAR = (9.95, 76.29)                   # where the ride ends

    def setUp(self):
        self.cfg = config.mode_config("bus")
        self.board = stop("b", 10.000, 76.305, "Board")
        self.loop = stop("l", 10.010, 76.300, "Loop Stop")
        self.end = stop("e", self.FAR[0], self.FAR[1], "End")
        # East stop, north, west past the rider, then on to the end.
        self.coords = [[10.000, 76.305], [10.005, 76.305], [10.010, 76.305],
                       [10.010, 76.300], [10.010, 76.290], [9.98, 76.29], list(self.FAR)]
        from transitnav.geo import path_length
        self.metres = path_length(self.coords)

    def walk(self, a, b):
        from transitnav.geo import haversine
        m = haversine(a[0], a[1], b[0], b[1]) * 1.2
        return {"type": "walk", "access": "walk", "geometry": [list(a), list(b)],
                "distance_m": m, "duration_s": m / 1.33}

    def reboard(self, stops):
        legs = [self.walk(self.O, (self.board["lat"], self.board["lon"])),
                {"type": "transit", "mode": "bus", "from": self.board, "to": self.end,
                 "geometry": self.coords, "distance_m": self.metres, "operator": None},
                self.walk(self.FAR, (self.FAR[0] + 0.0003, self.FAR[1]))]
        alt = {"coords": self.coords, "metres": self.metres, "seconds": self.metres / 6}

        def fast(access, acfg, a, b, label):
            return self.walk(a, b)
        with mock.patch.object(planner, "_access_leg_fast", fast):
            return planner._reboard(legs, 1, alt, self.cfg, stops, None)

    def test_boarded_on_the_loop(self):
        legs, alt, tips = self.reboard([self.board, self.loop, self.end])
        self.assertEqual(legs[1]["from"]["id"], "l")
        self.assertAlmostEqual(alt["coords"][0][0], 10.010, places=4)
        self.assertLess(alt["metres"], self.metres - 1000)
        self.assertEqual(legs[0]["geometry"][-1], [10.010, 76.300])
        self.assertEqual(tips, [])

    def test_same_stop_mapped_twice_is_not_another(self):
        twin = dict(self.loop, id="b2", name="Board")
        legs, alt, _ = self.reboard([self.board, twin, self.end])
        self.assertEqual(legs[1]["from"]["id"], "b")
        self.assertEqual(alt["metres"], self.metres)

    def test_operator_from_mapped_routes(self):
        opt = {"legs": [{"type": "transit", "mode": "bus", "operator": None, "label": "Bus",
                         "from": self.board, "to": self.loop, "geometry": self.coords[:3]}]}
        with mock.patch.object(planner.busroutes, "graph", lambda *a, **k: None), \
                mock.patch.object(planner, "_whose", lambda routes, geo: "private"):
            planner._tag_operators(opt)
        self.assertEqual(opt["legs"][0]["operator"], "private")
        self.assertTrue(opt["legs"][0]["label"].startswith("Private bus from Board"))


if __name__ == "__main__":
    unittest.main()
