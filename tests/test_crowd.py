"""How crowded is the bus: riders' answers, kept 45 minutes, nothing about who.

    python -m unittest tests.test_crowd
"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transitnav import crowd, limits, server  # noqa: E402

# Vyttila hub, heading north to Edappally; and Kaloor on the way.
VYTTILA = {"lat": 9.9672, "lon": 76.3207}
EDAPPALLY = {"lat": 10.0261, "lon": 76.3084}
KALOOR = {"lat": 9.9970, "lon": 76.2930}


def ask(level=None, stop=VYTTILA, toward=EDAPPALLY, mode="bus", **more):
    body = {"mode": mode, "lat": stop["lat"], "lon": stop["lon"], "tlat": toward["lat"], "tlon": toward["lon"]}
    if level:
        body["level"] = level
    body.update(more)
    return body


class CrowdTest(unittest.TestCase):
    def setUp(self):
        crowd.clear()

    def test_nobody_said(self):
        self.assertEqual(crowd.summary(ask()), {"level": None})

    def test_one_answer(self):
        crowd.report(ask("packed"), now=1000)
        self.assertEqual(crowd.summary(ask(), now=1000 + 6 * 60),
                         {"level": "packed", "reports": 1, "minutes_ago": 6})

    def test_forgotten_after_45_minutes(self):
        crowd.report(ask("seats"), now=1000)
        self.assertEqual(crowd.summary(ask(), now=1000 + 45 * 60)["level"], None)
        self.assertEqual(crowd.summary(ask(), now=1000 + 44 * 60)["level"], "seats")

    def test_newer_answers_count_more(self):
        crowd.report(ask("seats"), now=0)
        crowd.report(ask("seats"), now=60)
        crowd.report(ask("packed"), now=40 * 60)
        crowd.report(ask("packed"), now=41 * 60)
        self.assertEqual(crowd.summary(ask(), now=42 * 60)["level"], "packed")

    def test_other_direction_and_mode_are_separate(self):
        crowd.report(ask("packed"), now=0)
        self.assertIsNone(crowd.summary(ask(toward={"lat": 9.90, "lon": 76.33}), now=60)["level"])
        self.assertIsNone(crowd.summary(ask(mode="metro"), now=60)["level"])
        # The same stop, a little off (another rider's GPS), same way: the same.
        nudged = {"lat": VYTTILA["lat"] + 0.0002, "lon": VYTTILA["lon"]}
        self.assertEqual(crowd.summary(ask(stop=nudged), now=60)["level"], "packed")

    def test_counts_for_stops_ahead(self):
        crowd.report(ask("standing", ahead=[KALOOR, EDAPPALLY]), now=0)
        self.assertEqual(crowd.summary(ask(stop=KALOOR), now=60)["level"], "standing")

    def test_bad_answers(self):
        for body in (ask("full"), ask("seats", mode="ferry"), ask("seats", toward=VYTTILA),
                     ask("seats", stop={"lat": 51.5, "lon": -0.1}), {"level": "seats"},
                     ask("seats", ahead="Kaloor")):
            with self.assertRaises(crowd.CrowdError):
                crowd.report(body)

    def test_keeps_nothing_about_the_rider(self):
        crowd.report(ask("seats", token="abc", ip="1.2.3.4"), now=0)
        self.assertEqual(crowd._reports[next(iter(crowd._reports))], [("seats", 0)])


class CrowdServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.serve("127.0.0.1", 0)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        limits.reset()
        crowd.clear()

    def call(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_post_then_get(self):
        self.assertEqual(self.call("/api/crowd", ask("packed")), (200, {"ok": True}))
        q = "mode=bus&lat=%(lat)s&lon=%(lon)s" % VYTTILA + "&tlat=%(lat)s&tlon=%(lon)s" % EDAPPALLY
        code, d = self.call("/api/crowd?" + q)
        self.assertEqual((code, d["level"], d["reports"]), (200, "packed", 1))

    def test_bad_post(self):
        self.assertEqual(self.call("/api/crowd", ask("heaving"))[0], 400)

    def test_rate_limited(self):
        for _ in range(limits.LIMITS["crowd"][0]):
            self.assertEqual(self.call("/api/crowd", ask("seats"))[0], 200)
        self.assertEqual(self.call("/api/crowd", ask("seats"))[0], 429)


if __name__ == "__main__":
    unittest.main()
