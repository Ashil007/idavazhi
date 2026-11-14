"""Check active users and system status on the running Vazhi server.

Usage:
    python tools/active_users.py [--port 8001]
    python tools/active_users.py --url http://your-server-ip:8001
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def check_status(base_url: str):
    url = base_url.rstrip("/") + "/api/status"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Vazhi-Admin/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"[-] Could not connect to Vazhi server at {url}: {e}")
        print("    Make sure the server is running (e.g. 'python serve.py --port 8001')")
        return 1

    active_5m = data.get("active_users_5m", 0)
    active_1h = data.get("active_users_1h", 0)
    preload = data.get("preload", {})

    print("=" * 55)
    print(" VAZHI (TRANSITNAV) - LIVE USER & SYSTEM STATUS")
    print("=" * 55)
    print(f" Target Server      : {base_url}")
    print(f" Active Users (5m)  : {active_5m} unique visitor(s)")
    print(f" Active Users (1h)  : {active_1h} unique visitor(s)")
    print("-" * 55)
    
    stage = preload.get("stage", "idle")
    region = preload.get("region", "kerala")
    total = preload.get("total", 0)
    done = preload.get("done", 0)
    pct = f"{int(done / total * 100)}%" if total else "100%"
    
    print(f" Data Preload State : {stage.capitalize()} ({region})")
    print(f" Station Cache      : {done}/{total} files cached ({pct})")
    print("=" * 55)
    print("Note: Vazhi is privacy-first (no accounts or tracking cookies).")
    print("Active users are measured from rolling in-memory request windows.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="View active Vazhi users and server status")
    parser.add_argument("--port", type=int, default=8001, help="Server port (default: 8001)")
    parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    parser.add_argument("--url", default=None, help="Full server URL (e.g. http://127.0.0.1:8001)")
    args = parser.parse_args()

    base_url = args.url or f"http://{args.host}:{args.port}"
    return check_status(base_url)


if __name__ == "__main__":
    sys.exit(main())
