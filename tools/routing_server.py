"""Run your own street-routing server (Valhalla) for Kerala, in Docker.

    python tools/routing_server.py start     # first run builds routing tiles (~10-20 min)
    python tools/routing_server.py status
    python tools/routing_server.py stop

The app (transitnav/router.py) asks this server first, on port 8002, and
falls back to the public servers for places it does not cover. It needs
Docker Desktop running and the Kerala extract in .cache/osm (the app's
tools/build_map.py or this script downloads it).

The container restarts with Docker, so after the first build it is simply
there whenever Docker Desktop is running.
"""

import os
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PBF = os.path.join(ROOT, ".cache", "osm", "kerala-latest.osm.pbf")
PBF_URL = "https://download.openstreetmap.fr/extracts/asia/india/kerala-latest.osm.pbf"
DATA = os.path.join(ROOT, ".cache", "valhalla")
NAME = "transitnav-valhalla"
IMAGE = "ghcr.io/valhalla/valhalla-scripted:latest"
PORT = 8002


def docker(*args, check=True, capture=False):
    return subprocess.run(["docker", *args], check=check, text=True,
                          capture_output=capture)


def ensure_pbf():
    if os.path.exists(PBF):
        return
    os.makedirs(os.path.dirname(PBF), exist_ok=True)
    print("Downloading the Kerala OpenStreetMap extract (~180 MB)...", flush=True)
    urllib.request.urlretrieve(PBF_URL, PBF + ".part")
    os.replace(PBF + ".part", PBF)


def start():
    ensure_pbf()
    os.makedirs(DATA, exist_ok=True)
    target = os.path.join(DATA, os.path.basename(PBF))
    if not os.path.exists(target) or os.path.getmtime(target) < os.path.getmtime(PBF):
        shutil.copy2(PBF, target)            # Valhalla builds from what is in its folder
    running = docker("ps", "-a", "--filter", "name=^/%s$" % NAME, "--format", "{{.Status}}",
                     capture=True).stdout.strip()
    if running:
        print("Container exists (%s); starting it." % running)
        docker("start", NAME)
    else:
        docker("run", "-d", "--name", NAME, "--restart", "unless-stopped",
               "-p", "127.0.0.1:%d:8002" % PORT,
               "-v", "%s:/custom_files" % DATA,
               "-e", "serve_tiles=True", "-e", "use_tiles_ignore_pbf=True",
               "-e", "build_admins=True", "-e", "build_elevation=False",
               "-e", "build_time_zones=False", "-e", "server_threads=4",
               IMAGE)
    print("Valhalla is building or loading its tiles. Waiting for it to answer on "
          "port %d (the first build takes 10-20 minutes)..." % PORT, flush=True)
    for _ in range(180):
        if status(quiet=True):
            print("Routing server ready: http://127.0.0.1:%d" % PORT)
            return 0
        time.sleep(10)
    print("Still not answering; see: docker logs %s" % NAME)
    return 1


def status(quiet=False):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/status" % PORT, timeout=3) as r:
            ok = r.status == 200
    except Exception:  # noqa: BLE001
        ok = False
    if not quiet:
        print("running" if ok else "not running")
    return ok


def stop():
    docker("stop", NAME, check=False)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    sys.exit({"start": start, "stop": stop}.get(cmd, lambda: 0 if status() else 1)())
