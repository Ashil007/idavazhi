"""Entry point: python serve.py [--port 8000] [--host 127.0.0.1] [--no-browser]
[--region kerala | --region south,west,north,east | --no-preload]."""

import argparse
import sys
import threading
import time
import webbrowser

from transitnav.server import serve


def main() -> int:
    ap = argparse.ArgumentParser(description="Vazhi Transit")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--region", default=None,
                    help="region whose stations and track are downloaded to disk at "
                         "start: a name (kerala, kochi, bengaluru, chennai, hyderabad, "
                         "mumbai, delhi, kolkata) or south,west,north,east. Default: kerala")
    ap.add_argument("--no-preload", action="store_true",
                    help="download map data only when a trip needs it")
    args = ap.parse_args()

    from transitnav import preload
    region = args.region or preload.DEFAULT_REGION
    if not args.no_preload:
        try:
            preload.parse_region(region)
        except ValueError as exc:
            print(exc)
            return 2

    try:
        httpd = serve(args.host, args.port)
    except OSError as exc:
        print("Could not bind %s:%d - %s" % (args.host, args.port, exc))
        print("Another copy may already be running; try --port 8001.")
        return 1

    # Find dead Overpass mirrors up front so no plan waits on one.
    def probe():
        from transitnav import overpass
        res = overpass.probe_mirrors()
        up = [u.split("//")[1].split("/")[0] for u, v in res.items() if v is not None]
        print("Overpass mirrors reachable: " + (", ".join(up) or "none - check your connection"))
    threading.Thread(target=probe, daemon=True).start()

    # Download the region's stations and track once, in the background; trips
    # there then plan from disk. Already-fresh data is skipped instantly.
    if not args.no_preload:
        preload.start_background(region)

    url = "http://%s:%d/" % (args.host, args.port)
    print("Vazhi Transit is running at " + url)
    print("Map data (c) OpenStreetMap contributors. Press Ctrl+C to stop.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
