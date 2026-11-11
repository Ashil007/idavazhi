"""The web server's safety rules: headers, files, posts, limits and errors.

    python -m unittest discover tests

Runs the real server on a free local port; needs no internet.
"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transitnav import limits, server  # noqa: E402


class ServerTest(unittest.TestCase):
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

    def get(self, path, headers=None, method="GET", data=None):
        req = urllib.request.Request(self.base + path, headers=headers or {},
                                     method=method, data=data)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def post(self, path, body, headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        return self.get(path, h, "POST", data)

    # ------------------------------------------------------------ headers

    def test_security_headers_on_pages_and_api(self):
        for path in ("/", "/app", "/api/modes", "/no-such-file"):
            _, h, _ = self.get(path)
            self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"], path)
            self.assertIn("media-src 'self' blob:", h["Content-Security-Policy"], path)  # the alarm siren
            self.assertIn("script-src 'self'", h["Content-Security-Policy"], path)
            self.assertEqual(h["X-Content-Type-Options"], "nosniff", path)
            self.assertEqual(h["X-Frame-Options"], "DENY", path)
            self.assertNotIn("Python", h.get("Server", ""), path)

    def test_pages_load_no_outside_scripts(self):
        _, _, body = self.get("/app")
        self.assertNotIn(b"unpkg.com", body)
        for lib in ("/vendor/leaflet/leaflet.js", "/vendor/maplibre/maplibre-gl.js",
                    "/vendor/maplibre/leaflet-maplibre-gl.js"):
            code, _, _ = self.get(lib)
            self.assertEqual(code, 200, lib)

    def test_unchanged_file_is_304(self):
        code, h, _ = self.get("/style.css")
        self.assertEqual(code, 200)
        code, _, body = self.get("/style.css", {"If-Modified-Since": h["Last-Modified"]})
        self.assertEqual(code, 304)
        self.assertEqual(body, b"")

    # -------------------------------------------------------------- files

    def test_no_files_outside_the_web_folder(self):
        for path in ("/../transitnav/server.py", "/%2e%2e/transitnav/server.py",
                     "/..%2fserve.py", "/%2e%2e%2f%2e%2e%2fWindows/win.ini", "/data/timings.db"):
            code, _, body = self.get(path)
            self.assertEqual(code, 404, path)
            self.assertNotIn(b"import", body, path)

    # -------------------------------------------------------------- posts

    def test_post_must_be_json(self):
        code, _, _ = self.get("/api/timings", {"Content-Type": "text/plain"}, "POST", b"{}")
        self.assertEqual(code, 403)

    def test_post_from_another_site_is_refused(self):
        code, _, _ = self.post("/api/timings/vote", {}, {"Origin": "https://evil.example"})
        self.assertEqual(code, 403)

    def test_post_from_own_site_is_checked_normally(self):
        host = self.base.split("//")[1]
        code, _, body = self.post("/api/timings/vote", {}, {"Origin": "http://" + host})
        self.assertEqual(code, 400)          # empty vote: refused on its merits, not as CSRF
        self.assertIn("error", json.loads(body))

    def test_oversized_body_is_refused(self):
        code, _, _ = self.post("/api/timings", b"{" + b" " * (server.MAX_BODY + 10) + b"}")
        self.assertEqual(code, 400)

    # ---------------------------------------------------------- validation

    def test_bad_coordinates_and_modes(self):
        code, _, body = self.post("/api/plan", {"origin": {"lat": "nan", "lon": 1},
                                                "destination": {"lat": 1, "lon": 1}})
        self.assertEqual(code, 400)
        code, _, body = self.post("/api/plan", {"origin": {"lat": 9.9, "lon": 76.3},
                                                "destination": {"lat": 9.8, "lon": 76.3},
                                                "mode": "rocket"})
        self.assertEqual(code, 400)
        self.assertIn("Unknown travel mode", json.loads(body)["error"])

    def test_plan_stream_rejects_unknown_access(self):
        code, _, body = self.get("/api/plan?olat=9.9&olon=76.3&dlat=9.8&dlon=76.3"
                                 "&mode=bus&access=jetpack")
        self.assertEqual(code, 400)

    def test_ip_location_not_offered_through_a_proxy(self):
        code, _, body = self.get("/api/whereami", {"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(code, 503)

    # -------------------------------------------------------------- limits

    def test_search_rate_limit(self):
        saved = limits.LIMITS["search"]
        limits.LIMITS["search"] = (3, 60)
        try:
            codes = [self.get("/api/search?q=a", {"X-Forwarded-For": "198.51.100.7"})[0]
                     for _ in range(5)]
            self.assertEqual(codes[:3], [200, 200, 200])
            self.assertEqual(codes[3:], [429, 429])
            # Another visitor is not affected.
            self.assertEqual(self.get("/api/search?q=a",
                                      {"X-Forwarded-For": "198.51.100.8"})[0], 200)
        finally:
            limits.LIMITS["search"] = saved

    def test_video_ranges(self):
        from transitnav.server import _byte_range
        self.assertEqual(_byte_range("bytes=0-1", 10), (0, 1))
        self.assertEqual(_byte_range("bytes=4-", 10), (4, 9))
        self.assertEqual(_byte_range("bytes=-3", 10), (7, 9))
        self.assertEqual(_byte_range("bytes=5-99", 10), (5, 9))
        self.assertEqual(_byte_range("bytes=10-", 10), "bad")
        self.assertIsNone(_byte_range(None, 10))
        self.assertIsNone(_byte_range("bytes=0-1,4-5", 10))

    def test_payment_window_headers(self):
        # Razorpay's window shows the logo from its own site, and card
        # payments may need a bank popup that keeps talking to it.
        code, h, _ = self.get("/icons/icon-192.png")
        self.assertEqual(code, 200)
        self.assertEqual(h.get("Access-Control-Allow-Origin"), "*")
        _, h, _ = self.get("/app")
        self.assertEqual(h["Cross-Origin-Opener-Policy"], "same-origin-allow-popups")
        self.assertNotIn("Access-Control-Allow-Origin", h)

    def test_policy_pages(self):
        for path in ("/privacy", "/terms", "/refunds", "/contact", "/privacy.html"):
            code, h, body = self.get(path)
            self.assertEqual(code, 200, path)
            self.assertTrue(h["Content-Type"].startswith("text/html"), path)
            self.assertNotIn(b"{{", body, path)          # contact details filled in
        _, _, home = self.get("/")
        for path in (b"/privacy", b"/terms", b"/refunds", b"/contact"):
            self.assertIn(b'href="' + path + b'"', home)

    def test_health(self):
        code, _, body = self.get("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ok"])
        code, _, body = self.get("/api/health", method="HEAD")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")


if __name__ == "__main__":
    unittest.main()
