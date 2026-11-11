"""Copy Vazhi to your cloud server and set it up (or update it).

    python deploy/push.py <server-ip> --key C:\\path\\to\\ssh-key.key

The first run takes 30-60 minutes (the server builds the Kerala map and its
route service); later runs only replace the code and take a minute. Uses the
ssh and scp that come with Windows 10/11. See deploy/README.md for creating
the server.

Only the app's own files are sent: what git tracks or would track, plus the map's font
files. Your local map data and riders' data (data/) stay on this PC.
"""

import argparse
import getpass
import io
import os
import shutil
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = "data/vazhi.env"
REMOTE_USER = "ubuntu"
SAFE_KEY = os.path.join(os.path.expanduser("~"), ".ssh", "vazhi-cloud.key")


def files_to_send():
    tracked = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT, check=True,
                             capture_output=True).stdout.decode("utf-8").split("\0")
    out = [f for f in tracked if f and os.path.isfile(os.path.join(ROOT, f))]
    # The map's letters (web/fonts/**.pbf) are ignored by git but needed.
    fonts = os.path.join(ROOT, "web", "fonts")
    for base, _, names in os.walk(fonts):
        for n in names:
            if n.endswith(".pbf"):
                out.append(os.path.relpath(os.path.join(base, n), ROOT).replace("\\", "/"))
    # The owner's private settings (Telegram bot, UPI ID), kept out of git.
    if os.path.isfile(os.path.join(ROOT, SETTINGS)):
        out.append(SETTINGS)
    return sorted(set(out))


def build_archive(path):
    names = files_to_send()
    with tarfile.open(path, "w:gz") as tar:
        for rel in names:
            full = os.path.join(ROOT, rel)
            if rel == SETTINGS:
                info = tar.gettarinfo(full, rel)
                info.mode = 0o600          # bot token and the like: the app's user only
                with open(full, "rb") as fh:
                    tar.addfile(info, fh)
            elif rel.endswith(".sh") or rel.endswith(".py"):
                # Windows line endings break shell scripts on Linux.
                data = open(full, "rb").read().replace(b"\r\n", b"\n")
                info = tar.gettarinfo(full, rel)
                info.size = len(data)
                info.mode = 0o755 if rel.endswith(".sh") else 0o644
                tar.addfile(info, io.BytesIO(data))
            else:
                tar.add(full, rel)
    return len(names)


def protect_key(key):
    """ssh refuses a key other Windows users could read; keep a private copy."""
    os.makedirs(os.path.dirname(SAFE_KEY), exist_ok=True)
    if os.path.abspath(key) != os.path.abspath(SAFE_KEY):
        shutil.copyfile(key, SAFE_KEY)
    if os.name == "nt":
        user = getpass.getuser()
        subprocess.run(["icacls", SAFE_KEY, "/inheritance:r", "/grant:r", "%s:R" % user],
                       check=True, capture_output=True)
    else:
        os.chmod(SAFE_KEY, 0o600)
    return SAFE_KEY


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ip", help="the server's public IP address")
    ap.add_argument("--key", default=SAFE_KEY,
                    help="the private SSH key you downloaded when creating the server")
    ap.add_argument("--domain", default=None,
                    help="your own domain pointed at the server (default: <ip>.sslip.io)")
    args = ap.parse_args()

    if not os.path.isfile(args.key):
        print("SSH key not found: %s" % args.key)
        return 2
    key = protect_key(args.key)
    domain = args.domain or "%s.sslip.io" % args.ip.replace(".", "-")
    host = "%s@%s" % (REMOTE_USER, args.ip)
    ssh_opts = ["-i", key, "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ServerAliveInterval=30"]

    archive = os.path.join(ROOT, ".cache", "vazhi-deploy.tar.gz")
    os.makedirs(os.path.dirname(archive), exist_ok=True)
    count = build_archive(archive)
    print("Packed %d files (%.1f MB)." % (count, os.path.getsize(archive) / 1e6))

    setup = os.path.join(ROOT, "deploy", "setup.sh")
    setup_lf = archive + ".setup.sh"
    with open(setup, "rb") as src, open(setup_lf, "wb") as dst:
        dst.write(src.read().replace(b"\r\n", b"\n"))

    print("Copying to %s ..." % args.ip, flush=True)
    subprocess.run(["scp", *ssh_opts, archive, "%s:/tmp/vazhi.tar.gz" % host], check=True)
    subprocess.run(["scp", *ssh_opts, setup_lf, "%s:/tmp/vazhi-setup.sh" % host], check=True)
    print("Setting up the server (the first time takes 30-60 minutes) ...", flush=True)
    code = subprocess.run(["ssh", *ssh_opts, host,
                           "sudo DOMAIN=%s bash /tmp/vazhi-setup.sh /tmp/vazhi.tar.gz" % domain]
                          ).returncode
    if code == 0:
        print("\nShare this link: https://%s/app" % domain)
    return code


if __name__ == "__main__":
    sys.exit(main())
