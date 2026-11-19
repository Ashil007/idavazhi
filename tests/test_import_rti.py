"""The importer for the MVD private bus schedule (tools/import_rti_bus_timings.py)."""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import import_rti_bus_timings as rti  # noqa: E402
from transitnav import timings  # noqa: E402

# Made-up places on a line going north from (10.00, 76.30), about 1.1 km per
# 0.01 degree of latitude. There are two "Medical College"s: one on the road,
# one 60 km away.
PLACES = {
    "Alpha Bus Stand": [(10.000, 76.300)],
    "Medical College": [(10.540, 76.300), (10.040, 76.300)],
    "Gamma Jn": [(10.080, 76.300)],
    "Delta": [(10.120, 76.300)],
}


def fake_search(text, near=None, limit=8):
    hits = PLACES.get(text, [])
    return [{"name": text, "detail": "Bus stop", "lat": a, "lon": b} for a, b in hits][:limit]


class ParseTest(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(rti.minutes("05:07 am"), 307)
        self.assertEqual(rti.minutes("12:10 am"), 10)
        self.assertEqual(rti.minutes("12:10 pm"), 730)
        self.assertEqual(rti.minutes("1.05 p.m."), 785)
        self.assertIsNone(rti.minutes("13:00 pm"))
        self.assertIsNone(rti.minutes(""))

    def test_names(self):
        self.assertEqual(rti.tidy("VYTTILA JN."), "Vyttila Jn")
        self.assertEqual(rti.queries("ALUVA PRIVATE BUS STAND"), ["Aluva Bus Stand"])
        self.assertEqual(rti.queries("CHITTETHUKARA BUS STOP- KAKKANAD"),
                         ["Chittethukara Bus Stop - Kakkanad", "Chittethukara Bus Stop", "Kakkanad"])
        self.assertEqual(rti.queries("VYTTILA ANTICLOCKWISE"), ["Vyttila"])
        self.assertEqual(rti.queries("FORTKOCHI BUS TERMINAL"), ["Fort Kochi Bus Terminal"])

    def test_generic_words_alone_do_not_match(self):
        def search(text, near=None, limit=8):
            return [{"name": "KSRTC Bus Terminal", "detail": "Bus stand", "lat": 10.2, "lon": 76.4}]
        self.assertEqual(rti.Finder(search=search).candidates("FORTKOCHI BUS TERMINAL", None), [])


class PlaceRouteTest(unittest.TestCase):
    def setUp(self):
        self.finder = rti.Finder(search=fake_search)

    def test_picks_the_stop_on_the_road(self):
        stops = [("ALPHA BUS STAND", 300), ("MEDICAL COLLEGE", 310), ("GAMMA JN.", 320)]
        points = rti.place_route(stops, self.finder, (10.0, 76.3))
        self.assertEqual(points[1], (10.040, 76.300))       # not the one 60 km away

    def test_unknown_or_impossible_stop_left_out(self):
        PLACES["Far Away"] = [(11.500, 76.300)]            # 165 km in 5 minutes
        try:
            stops = [("ALPHA BUS STAND", 300), ("FAR AWAY", 305), ("NOWHERE", 310), ("DELTA", 340)]
            points = rti.place_route(stops, self.finder, (10.0, 76.3))
        finally:
            del PLACES["Far Away"]
        self.assertEqual(points[0], (10.000, 76.300))
        self.assertIsNone(points[1])
        self.assertIsNone(points[2])
        self.assertEqual(points[3], (10.120, 76.300))

    def test_hand_fix_wins(self):
        finder = rti.Finder(search=fake_search, overrides={"medical college": (10.05, 76.31)})
        stops = [("ALPHA BUS STAND", 300), ("MEDICAL COLLEGE", 310)]
        self.assertEqual(rti.place_route(stops, finder, None)[1], (10.05, 76.31))


class ReportsTest(unittest.TestCase):
    BUS = {"Vehicle Number": "KL 07 AB 1234", "schedule": [{"trip": 1, "stations": [
        {"station": "ALPHA BUS STAND", "arrivalTime": "06:00 am", "departureTime": "06:00 am"},
        {"station": "MEDICAL COLLEGE", "arrivalTime": "06:10 am", "departureTime": "06:11 am"},
        {"station": "GAMMA JN.", "arrivalTime": "06:20 am", "departureTime": "06:20 am"},
    ]}]}

    def test_one_time_per_stop_left(self):
        points = {"ALPHA BUS STAND": (10.0, 76.3), "MEDICAL COLLEGE": (10.04, 76.3), "GAMMA JN.": (10.08, 76.3)}
        out = rti.trip_reports(self.BUS, self.BUS["schedule"][0], points)
        self.assertEqual([r["dep_min"] for r in out], [360, 371])
        self.assertEqual(out[0]["toward_name"], "Gamma Jn")
        self.assertEqual((out[0]["toward_lat"], out[0]["toward_lon"]), (10.04, 76.3))   # heads to the next stop
        self.assertTrue(out[0]["route"].startswith("KL 07 AB 1234 · Alpha Bus Stand - Gamma Jn"))
        self.assertLessEqual(len(out[0]["route"]), 60)
        self.assertEqual(out[0]["operator"], "private")

    def test_long_route_keeps_its_source(self):
        bus = {"Vehicle Number": "KL 07 BF 4343", "schedule": [{"trip": 1, "stations": [
            {"station": "VYTTILA MOBILITY HUB", "departureTime": "04:01 am"},
            {"station": "NORTH PARAVUR PRIVATE BUS STATION", "departureTime": "05:01 am"}]}]}
        points = {"VYTTILA MOBILITY HUB": (9.97, 76.32), "NORTH PARAVUR PRIVATE BUS STATION": (10.15, 76.23)}
        route = rti.trip_reports(bus, bus["schedule"][0], points)[0]["route"]
        self.assertLessEqual(len(route), 60)
        self.assertTrue(route.startswith("KL 07 BF 4343 · Vyttila"))
        self.assertTrue(route.endswith(" (MVD list)"))

    def test_unplaced_stop_gives_no_time(self):
        points = {"ALPHA BUS STAND": (10.0, 76.3), "GAMMA JN.": (10.08, 76.3)}
        out = rti.trip_reports(self.BUS, self.BUS["schedule"][0], points)
        self.assertEqual([r["board_name"] for r in out], ["Alpha Bus Stand"])
        self.assertEqual(out[0]["toward_lat"], 10.08)        # skips to the next placed stop

    def test_build_counts_unmatched(self):
        bus = dict(self.BUS, schedule=[{"trip": 1, "stations": self.BUS["schedule"][0]["stations"] + [
            {"station": "NOWHERE", "arrivalTime": "06:30 am", "departureTime": "06:30 am"}]}])
        reports, matched, unmatched = rti.build([("ernakulam", bus)], rti.Finder(search=fake_search),
                                                log=lambda *a: None)
        self.assertEqual(unmatched, {"Nowhere": 1})
        self.assertIn("Medical College", matched)
        # Alpha and Medical College; Gamma has no placed stop after it to head toward.
        self.assertEqual(len(reports), 2)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = timings.DB_PATH
        timings.DB_PATH = os.path.join(self.dir, "timings.db")
        self.report = {"board_name": "Alpha Bus Stand", "board_lat": 10.0, "board_lon": 76.3,
                       "toward_name": "Gamma Jn", "toward_lat": 10.04, "toward_lon": 76.3,
                       "dep_min": 360, "days": "daily", "operator": "private", "route": "x (MVD list)"}

    def tearDown(self):
        timings.DB_PATH = self.old
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_store_then_seen_by_planner(self):
        self.assertEqual(rti.store([self.report]), 1)
        times = timings.near((10.0, 76.3), (10.08, 76.3))
        self.assertEqual([t["time"] for t in times], ["06:00"])
        self.assertEqual(times[0]["ok"], 1)
        self.assertEqual(times[0]["operator_id"], "private")

    def test_second_import_needs_replace_and_keeps_riders_times(self):
        rti.store([self.report])
        timings.add({"board": {"name": "Alpha", "lat": 10.0, "lon": 76.3},
                     "toward": {"name": "Gamma", "lat": 10.04, "lon": 76.3},
                     "time": "07:40", "operator": "ksrtc", "device": "a" * 20})
        with self.assertRaises(SystemExit):
            rti.store([self.report])
        rti.store([dict(self.report, dep_min=375)], replace=True)
        times = [t["time"] for t in timings.near((10.0, 76.3), (10.08, 76.3))]
        self.assertEqual(times, ["06:15", "07:40"])        # old import gone, rider's time kept


if __name__ == "__main__":
    unittest.main()
