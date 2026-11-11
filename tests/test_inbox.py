"""The owner's inbox: /api/inbox on the server and the Messages app reading it.

Runs the real server on a free local port with a throwaway database; needs
no internet.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from transitnav import feedback, limits, server  # noqa: E402
import inbox  # noqa: E402

TOKEN = "test-inbox-password-0123456789abcdef"


class InboxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = mock.patch.object(feedback, "DB_PATH", os.path.join(cls.tmp, "feedback.db"))
        cls.db.start()
        cls.httpd = server.serve("127.0.0.1", 0)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.db.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        limits.reset()
        os.environ.pop("VAZHI_INBOX_TOKEN", None)
        os.environ.pop("VAZHI_INBOX_URL", None)
        self.addCleanup(os.environ.pop, "VAZHI_INBOX_TOKEN", None)
        self.addCleanup(os.environ.pop, "VAZHI_INBOX_URL", None)

    def ask(self, token=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        req = urllib.request.Request(self.base + "/api/inbox", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_no_password_set_means_no_inbox(self):
        with mock.patch.object(server.settings, "get", lambda k, d="": ""):
            self.assertEqual(self.ask(TOKEN)[0], 404)

    def test_wrong_or_missing_password(self):
        os.environ["VAZHI_INBOX_TOKEN"] = TOKEN
        self.assertEqual(self.ask()[0], 401)
        self.assertEqual(self.ask(TOKEN + "x")[0], 401)
        self.assertEqual(self.ask(TOKEN[:-1])[0], 401)

    def test_right_password_gets_messages(self):
        os.environ["VAZHI_INBOX_TOKEN"] = TOKEN
        feedback.add({"text": "The 7:40 bus never came", "contact": "Anu", "page": "app"})
        code, h, body = self.ask(TOKEN)
        self.assertEqual(code, 200)
        self.assertIn("no-store", h["Cache-Control"])
        self.assertNotIn("Access-Control-Allow-Origin", h)    # other sites' pages can't read it
        data = json.loads(body)
        self.assertTrue(any(m["text"] == "The 7:40 bus never came" for m in data["messages"]))

    def test_guessing_is_limited(self):
        os.environ["VAZHI_INBOX_TOKEN"] = TOKEN
        with mock.patch.dict(limits.LIMITS, {"inbox": (3, 3600)}):
            codes = [self.ask("guess%d" % i)[0] for i in range(5)]
        self.assertEqual(codes[-1], 429)

    def test_app_reads_from_the_server(self):
        os.environ["VAZHI_INBOX_TOKEN"] = TOKEN
        os.environ["VAZHI_INBOX_URL"] = self.base
        feedback.add({"text": "Lovely app", "rating": 5, "page": "trip"})
        rows = inbox.load()
        self.assertTrue(any(r[1] == "Lovely app" and r[6] == 5 for r in rows))
        self.assertEqual(len(rows[0]), 8)
        self.assertEqual(rows, inbox.rows_of(feedback.inbox()))   # same as reading the file

    def test_app_explains_a_wrong_password(self):
        os.environ["VAZHI_INBOX_TOKEN"] = TOKEN
        os.environ["VAZHI_INBOX_URL"] = self.base
        app_settings = mock.Mock()                # the app has another password than the server
        app_settings.get = lambda k, d="": {"VAZHI_INBOX_URL": self.base}.get(k, "wrong-password-0123456789xyz")
        with mock.patch.object(inbox, "settings", app_settings):
            with self.assertRaises(inbox.InboxError) as e:
                inbox.load()
        self.assertIn("refused the password", str(e.exception))

    def test_password_only_over_https(self):
        self.assertTrue(inbox.safe_url("https://vazhi.example"))
        self.assertTrue(inbox.safe_url("http://127.0.0.1:8001"))
        self.assertFalse(inbox.safe_url("http://vazhi.example"))
        os.environ["VAZHI_INBOX_URL"] = "http://vazhi.example"
        os.environ["VAZHI_INBOX_TOKEN"] = TOKEN
        with self.assertRaises(inbox.InboxError):
            inbox.load()


if __name__ == "__main__":
    unittest.main()
