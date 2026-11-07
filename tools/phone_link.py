"""Open the running app on your phone through a temporary HTTPS link.

    python tools/phone_link.py

Phones only allow location, notifications and the trip alarm on secure
(https) pages, so http://<this-pc>:8001 is not enough. This starts a
Cloudflare quick tunnel to the app (which must already be running on port
8001) and prints a link like https://some-words.trycloudflare.com - open it
on the phone. Press Ctrl+C to close the link.

Privacy: while it runs, anyone who has the link can open the app (it cannot
be guessed, but do not post it publicly). Each run gets a new link.

The first run downloads Cloudflare's official cloudflared (~55 MB, Windows)
into .cache/tools and checks it against the checksum Cloudflare published.
"""

import hashlib
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, ".cache", "tools")
EXE = os.path.join(TOOLS, "cloudflared.exe")
VERSION = "2026.10.7"
URL = ("https://github.com/cloudflare/cloudflared/releases/download/%s/"
       "cloudflared-windows-amd64.exe" % VERSION)
SHA256 = "2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343"
PORT = 8001


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_cloudflared():
    if os.path.exists(EXE) and sha256(EXE) == SHA256:
        return True
    if sys.platform != "win32":
        print("On this system, install cloudflared from Cloudflare and run:\n"
              "  cloudflared tunnel --url http://localhost:%d" % PORT)
        return False
    os.makedirs(TOOLS, exist_ok=True)
    print("Downloading Cloudflare's cloudflared %s (~55 MB)..." % VERSION, flush=True)
    urllib.request.urlretrieve(URL, EXE + ".part")
    if sha256(EXE + ".part") != SHA256:
        os.remove(EXE + ".part")
        print("The download does not match Cloudflare's published checksum; not running it.")
        return False
    os.replace(EXE + ".part", EXE)
    return True


def main():
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/api/modes" % PORT, timeout=3)
    except Exception:  # noqa: BLE001
        print("The app is not running on port %d. Start it first:  python serve.py --port %d"
              % (PORT, PORT))
        return 1
    if not ensure_cloudflared():
        return 1
    print("Starting the secure link. Look for the https://....trycloudflare.com line below,")
    print("open it on your phone, and press Ctrl+C here to close the link.\n", flush=True)
    try:
        return subprocess.call([EXE, "tunnel", "--no-autoupdate", "--url",
                                "http://localhost:%d" % PORT])
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
