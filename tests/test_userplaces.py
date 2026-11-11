"""Places riders add when the map lacks them (transitnav/userplaces.py)."""

import os
import tempfile
import unittest
from unittest import mock

from transitnav import limits, userplaces

DEVICE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
SHOP = {"name": "VPK Distributors", "kind": "shop", "lat": 10.0279, "lon": 76.3122, "device": DEVICE}


class UserPlacesTest(unittest.TestCase):
    def setUp(self):
        limits.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(userplaces, "DB_PATH", os.path.join(self.tmp.name, "places.db"))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_added_place_is_found(self):
        r = userplaces.add(SHOP)
        self.assertFalse(r["merged"])
        self.assertEqual(r["detail"], "Shop · added by riders")
        found = userplaces.search("vpk distrib", near=(10.0, 76.3))
        self.assertEqual([f["name"] for f in found], ["VPK Distributors"])
        self.assertEqual(userplaces.search("distributors vpk")[0]["name"], "VPK Distributors")
        self.assertEqual(userplaces.search("lulu"), [])

    def test_same_place_twice_is_one_place(self):
        userplaces.add(SHOP)
        again = userplaces.add(dict(SHOP, name="vpk  distributors", lat=10.0282))   # ~30 m away
        self.assertTrue(again["merged"])
        far = userplaces.add(dict(SHOP, lat=10.2))                                 # another town
        self.assertFalse(far["merged"])
        self.assertEqual(len(userplaces.search("vpk")), 2)

    def test_bad_places_are_refused(self):
        for bad in (dict(SHOP, name="x"), dict(SHOP, name="Visit www.spam.com"),
                    dict(SHOP, lat=28.6, lon=77.2),                 # Delhi: outside Kerala
                    dict(SHOP, lat="here"), dict(SHOP, device="short")):
            with self.assertRaises(Exception, msg=str(bad)):
                userplaces.add(bad)
        self.assertEqual(userplaces.add(dict(SHOP, kind="castle"))["detail"], "Place · added by riders")

    def test_adding_is_limited(self):
        with mock.patch.dict(limits.LIMITS, {"place": (2, 3600)}):
            userplaces.add(dict(SHOP, name="Shop One"), "1.2.3.4")
            userplaces.add(dict(SHOP, name="Shop Two"), "1.2.3.4")
            with self.assertRaises(userplaces.PlaceError.__mro__[1]):
                userplaces.add(dict(SHOP, name="Shop Three"), "1.2.3.4")


if __name__ == "__main__":
    unittest.main()
