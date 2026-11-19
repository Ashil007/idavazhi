"""Load-test a running Idavazhi: many pretend riders at once, then a summary.

    python tools/load_test.py --users 20 --seconds 60
    python tools/load_test.py --base https://<ip>.sslip.io --users 10 --seconds 60

Each pretend rider does what a real one does: opens the app, looks at the map
(map tiles), searches for a place, and every so often plans a bus trip
between two random points around Kottayam and Kochi. Riders get different
pretend addresses (X-Forwarded-For), so the per-visitor limits treat them as
different people - that header is only believed from the server's own
machine, so this works against a local server, not a remote one, where the
real limits apply.

Searches use a few fixed town names, so the online place search behind it is
asked at most once per name. Run it only against your own server.
"""

import argparse
import json
import random
import statistics
import threading
import time
import urllib.error
import urllib.request

TOWNS = ["Kottayam", "Ernakulam", "Aluva", "Changanassery", "Thrissur", "Alappuzha"]
# Bus trips inside this box (Kottayam - Kochi), where the local route service
# and the preloaded stops answer without asking any outside server.
BOX = (9.45, 76.25, 10.10, 76.60)


def tile_for(lat, lon, z):
    import math
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.times = {}
        self.codes = {}

    def add(self, kind, secs, code):
        with self.lock:
            self.times.setdefault(kind, []).append(secs)
            self.codes.setdefault(kind, {}).setdefault(code, 0)
            self.codes[kind][code] += 1


def rider(n, base, until, stats, plan_every):
    ip = "10.%d.%d.%d" % (n // 65536 % 256, n // 256 % 256, n % 256)
    rnd = random.Random(n)

    def get(kind, path, stream=False):
        req = urllib.request.Request(base + path, headers={"X-Forwarded-For": ip,
                                                           "Accept-Encoding": "gzip"})
        t = time.perf_counter()
        code = "error"
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
                code = r.status
                if stream:
                    text = body.decode("utf-8", "replace")
                    code = ("plan ok" if "event: result" in text
                            else "busy" if "very busy" in text
                            else "limited" if "Too many trips" in text else "plan error")
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:  # noqa: BLE001
            code = "error"
        stats.add(kind, time.perf_counter() - t, code)

    last_plan = 0.0
    while time.time() < until:
        get("page", "/app")
        lat, lon = rnd.uniform(BOX[0], BOX[2]), rnd.uniform(BOX[1], BOX[3])
        for _ in range(12):
            z = rnd.choice((11, 12, 13))
            x, y = tile_for(lat + rnd.uniform(-.05, .05), lon + rnd.uniform(-.05, .05), z)
            get("tile", "/tiles/%d/%d/%d.pbf" % (z, x, y))
        get("search", "/api/search?q=" + rnd.choice(TOWNS))
        if time.time() - last_plan >= plan_every:
            last_plan = time.time()
            a = (rnd.uniform(BOX[0], BOX[2]), rnd.uniform(BOX[1], BOX[3]))
            b = (rnd.uniform(BOX[0], BOX[2]), rnd.uniform(BOX[1], BOX[3]))
            get("plan", "/api/plan?olat=%.5f&olon=%.5f&dlat=%.5f&dlon=%.5f&mode=bus&access=walk"
                % (a[0], a[1], b[0], b[1]), stream=True)
        time.sleep(rnd.uniform(1.0, 3.0))       # reading the screen


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:8001")
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--plan-every", type=float, default=20.0,
                    help="seconds between one rider's trip plans")
    args = ap.parse_args()

    stats = Stats()
    until = time.time() + args.seconds
    threads = [threading.Thread(target=rider, args=(i, args.base, until, stats, args.plan_every),
                                daemon=True) for i in range(args.users)]
    started = time.time()
    for i, t in enumerate(threads):
        t.start()
        time.sleep(0.1)                         # riders arrive over a couple of seconds
    for t in threads:
        t.join()
    took = time.time() - started

    total = sum(len(v) for v in stats.times.values())
    print("\n%d pretend riders for %.0f s: %d requests, %.1f per second"
          % (args.users, took, total, total / took))
    print("%-8s %7s %9s %9s %9s  %s" % ("kind", "count", "median", "95%", "worst", "results"))
    for kind in ("page", "tile", "search", "plan"):
        xs = stats.times.get(kind)
        if not xs:
            continue
        print("%-8s %7d %8.2fs %8.2fs %8.2fs  %s" % (
            kind, len(xs), statistics.median(xs), pct(xs, 0.95), max(xs),
            json.dumps(stats.codes[kind])))
    plans = stats.codes.get("plan", {}).get("plan ok", 0)
    print("trip plans finished: %d (%.1f a minute)" % (plans, plans / took * 60))


if __name__ == "__main__":
    main()
