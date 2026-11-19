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

`start` also lets the app send its own "hierarchy limits" (so bus routes
keep to the main roads, transitnav/router.py BUS_LANE_REACH_M): it turns
that on in the server's valhalla.json and restarts the server if needed.
"""

import json
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


def allow_hierarchy_limits():
    """Let requests set Valhalla's hierarchy limits (off by default). True if
    valhalla.json was changed, so the server must restart to use it."""
    path = os.path.join(DATA, "valhalla.json")
    try:
        with open(path, encoding="utf-8") as f:
            conf = json.load(f)
    except (OSError, ValueError):
        return False            # not written yet: the first build makes it
    limits = conf.setdefault("service_limits", {}).setdefault("hierarchy_limits", {})
    if limits.get("allow_modification") is True:
        return False
    limits["allow_modification"] = True
    with open(path + ".part", "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=2)
    os.replace(path + ".part", path)
    print("Allowed hierarchy limits in %s (bus routes keep to the main roads)." % path)
    return True


def wait_ready():
    for _ in range(180):
        if status(quiet=True):
            print("Routing server ready: http://127.0.0.1:%d" % PORT)
            return True
        time.sleep(10)
    print("Still not answering; see: docker logs %s" % NAME)
    return False


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
        if allow_hierarchy_limits() and running.startswith("Up"):
            docker("restart", NAME)
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
    if not wait_ready():
        return 1
    # The first build writes valhalla.json; the setting needs a restart.
    if allow_hierarchy_limits():
        docker("restart", NAME)
        if not wait_ready():
            return 1
    return 0


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
