"""Fare estimates (fares.py) and how Fastest / Cheapest label trips.

    python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transitnav import fares, planner  # noqa: E402


def leg(kind, metres, **extra):
    return dict({"type": kind, "distance_m": metres, "duration_s": metres / 5,
                 "geometry": []}, **extra)


def trip(*legs):
    return {"legs": list(legs), "access": "walk",
            "summary": {"duration_s": sum(l["duration_s"] for l in legs)}}


class FareTest(unittest.TestCase):
    def test_short_bus_ride_costs_the_minimum(self):
        # keralartc.com: Ordinary minimum Rs 10, Fast Passenger Rs 15.
        self.assertEqual(fares.bus_fare(3000)["min"], 10)
        self.assertEqual(fares.bus_fare(3000)["max"], 15)

    def test_the_orders_own_example(self):
        # G.O. 30-04-2022: 10 km ordinary = 10 + 7.5 x 1 = 17.50 -> Rs 18.
        by = {t["name"]: t for t in fares.bus_fare(10000)["types"]}
        self.assertEqual(by["City / Ordinary"]["rupees"], 18)

    def test_ordinary_fare_stages(self):
        # 2.5 km stages: Rs 10, 13, 15, 18, 20, 23, 25.
        got = [fares.ksrtc_fare(100, 10, 2.5, "nearest", 2500 * n) for n in range(1, 8)]
        self.assertEqual(got, [10, 13, 15, 18, 20, 23, 25])

    def test_the_2020_order_checks_out(self):
        # The Hindu, 2020: 40 km (fare stage 16) ordinary at Rs 8 + 90 p/km = Rs 42.
        self.assertEqual(fares.ksrtc_fare(90, 8, 2.5, "nearest", 40000), 42)

    def test_each_class_rounds_its_own_way(self):
        by = {t["name"]: t["rupees"] for t in fares.bus_fare(200000)["types"]}   # 200 km
        self.assertEqual(by["City / Ordinary"], 208)           # 10 + 197.5 = 207.50 -> nearest
        self.assertEqual(by["Fast Passenger"], 220)            # 15 + 195 x 1.05 = 219.75 -> next Re 1
        self.assertEqual(by["Super Fast"], 228)                # 22 + 190 x 1.08 = 227.20 -> next Re 1
        self.assertEqual(by["Express / Super Express"], 235)   # 28 + 185 x 1.10 = 231.50 -> next Rs 5
        self.assertEqual(by["Multi Axle Volvo"], 510)          # 100 + 180 x 2.25 = 505 -> next Rs 10

    def test_kuthiathode_to_kundanoor(self):
        f = fares.bus_fare(16200)           # 16.2 km
        by = {t["name"]: t for t in f["types"]}
        self.assertEqual(by["City / Ordinary"]["rupees"], 24)    # 10 + 13.7
        self.assertEqual((by["City / Ordinary"]["low"], by["City / Ordinary"]["high"]), (23, 25))
        self.assertEqual(by["Fast Passenger"]["rupees"], 27)     # 15 + 11.2 x 1.05 = 26.76
        self.assertEqual(by["Super Fast"]["rupees"], 29)         # 22 + 6.2 x 1.08 = 28.70
        self.assertEqual(by["Express / Super Express"]["rupees"], 30)   # 29.32 -> next Rs 5
        self.assertEqual(by["Multi Axle Volvo"]["rupees"], 100)  # inside the 20 km minimum
        self.assertEqual((f["min"], f["max"]), (23, 27))
        self.assertEqual(len(f["types"]), 13)
        self.assertIn("Swift", f["notes"][0])

    def test_metro_slabs(self):
        # KMRL fare chart: 0-2 km Rs 10 ... 20+ km Rs 60.
        self.assertEqual(fares.metro_fare(1500)["min"], 10)
        self.assertEqual(fares.metro_fare(7000)["min"], 30)
        self.assertEqual(fares.metro_fare(25000)["min"], 60)

    def test_train_is_not_priced(self):
        plan = fares.price(trip(leg("walk", 800, access="walk"),
                                leg("transit", 200000, mode="train"),
                                leg("transit", 12000, mode="bus")))
        fare = plan["summary"]["fare"]
        self.assertFalse(fare["complete"])
        self.assertEqual(fare["missing"], ["train"])
        self.assertEqual(fare["min"], 18)   # 12 km ordinary, the stage below (10 km): Rs 18
        self.assertIn("train fare", fare["text"])
        self.assertIsNone(plan["legs"][1]["fare"])

    def test_bus_trip_is_priced(self):
        plan = fares.price(trip(leg("walk", 300, access="walk"),
                                leg("transit", 20000, mode="bus")))
        self.assertTrue(plan["summary"]["fare"]["complete"])
        # 20 km is a whole number of stages: ordinary 10 + 17.5 = 27.50 -> 28;
        # fast passenger 15 + 15 x 1.05 = 30.75 -> 31.
        self.assertEqual(plan["summary"]["fare"]["text"], "₹28–31")


class WaysTest(unittest.TestCase):
    def test_a_train_then_a_bus(self):
        t = trip(leg("walk", 800, access="walk"), leg("transit", 200000, mode="train"),
                 leg("walk", 300, access="walk"), leg("transit", 12000, mode="bus"))
        self.assertEqual(planner._ways(t), "Train + bus")

    def test_far_end_by_auto(self):
        t = trip(leg("transit", 200000, mode="train"), leg("ride", 9000, access="road"))
        self.assertEqual(planner._ways(t), "Train + auto")

    def test_cheapest_puts_priced_trips_first(self):
        bus = fares.price(trip(leg("transit", 200000, mode="bus")))
        train = fares.price(trip(leg("transit", 200000, mode="train")))
        self.assertLess(planner._fare_key(bus), planner._fare_key(train))


if __name__ == "__main__":
    unittest.main()
