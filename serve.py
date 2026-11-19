"""Entry point: python serve.py [--port 8000] [--host 127.0.0.1] [--no-browser]
[--region kerala | --region south,west,north,east | --no-preload]."""

import argparse
import signal
import sys
import threading
import time
import webbrowser

from transitnav.server import serve


def main() -> int:
    ap = argparse.ArgumentParser(description="Idavazhi")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--region", default=None,
                    help="region whose stations and track are downloaded to disk at "
                         "start: a name (kerala, kochi, bengaluru, chennai, hyderabad, "
                         "mumbai, delhi, kolkata) or south,west,north,east. Default: kerala")
    ap.add_argument("--no-preload", action="store_true",
                    help="download map data only when a trip needs it")
    ap.add_argument("--worker", action="store_true",
                    help="an extra copy of the app beside the main one (the cloud server "
                         "runs one per processor core): it only answers requests and "
                         "leaves downloading and index building to the main copy")
    ap.add_argument("--reload", action="store_true",
                    help="automatically reload the server when Python files are modified")
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
    if not args.worker:
        threading.Thread(target=probe, daemon=True).start()

    # Download the region's stations and track once, in the background; trips
    # there then plan from disk. Already-fresh data is skipped instantly.
    if not args.no_preload and not args.worker:
        preload.start_background(region)

    # The place-name index for the destination search, rebuilt whenever the
    # map tiles are newer than it (about a minute, in the background).
    if not args.worker:
        from transitnav import places
        places.build_in_background(log=lambda m: print(m, flush=True))

    # Automated daily lock-free hot backup of live SQLite databases.
    if not args.worker:
        from transitnav import backup
        backup.start_daily_backup()

    url = "http://%s:%d/" % (args.host, args.port)
    print("Idavazhi is running at " + url)
    print("Map data (c) OpenStreetMap contributors. Press Ctrl+C to stop.")
    if not args.no_browser:
        webbrowser.open(url)
    # The cloud server's service manager stops the app with SIGTERM: treat it
    # like Ctrl+C, so it stops taking requests and closes cleanly.
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)

    last_reload_check = time.time()
    py_mtimes = {}
    if args.reload:
        import os
        root_dir = os.path.dirname(os.path.abspath(__file__))
        def scan_mtimes():
            m = {}
            for r, _, fns in os.walk(os.path.join(root_dir, "transitnav")):
                for fn in fns:
                    if fn.endswith(".py"):
                        p = os.path.join(r, fn)
                        try:
                            m[p] = os.path.getmtime(p)
                        except OSError:
                            pass
            return m
        py_mtimes = scan_mtimes()

    try:
        while True:
            time.sleep(0.5)
            if args.reload and time.time() - last_reload_check > 1.0:
                last_reload_check = time.time()
                cur = scan_mtimes()
                if any(cur.get(p) != mt for p, mt in py_mtimes.items()):
                    print("\n[Auto-reload] Code changed on disk. Restarting server...")
                    httpd.shutdown()
                    httpd.server_close()
                    import os
                    os.execv(sys.executable, [sys.executable] + sys.argv)
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
