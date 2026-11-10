#!/usr/bin/env bash
# Set up (or update) Vazhi on a fresh Ubuntu server - an Oracle Cloud "Always
# Free" machine, for example. deploy/push.py copies the app here and runs it:
#
#     sudo DOMAIN=1-2-3-4.sslip.io bash setup.sh /tmp/vazhi.tar.gz
#
# What it does, every step safe to run again:
#   1. a swap file, so building the map fits on a small machine too
#   2. Python, Docker and Caddy (the web server that gets the free HTTPS
#      certificate - phones only allow location and alarms on https pages)
#   3. opens ports 80 and 443 in the machine's own firewall
#   4. unpacks the app into /opt/vazhi (map data and riders' data are kept)
#   5. first time only: builds the Kerala map tiles from OpenStreetMap
#   6. runs the app as a service that starts with the machine, behind Caddy
#   7. starts the route service (Valhalla, in Docker); its first build takes
#      10-30 minutes, and the app uses the public route servers until then
#
# For testing in a plain container: SKIP_DOCKER=1 SKIP_SYSTEM=1.
set -euo pipefail

ARCHIVE="${1:-/tmp/vazhi.tar.gz}"
APP=/opt/vazhi
USER_NAME=vazhi
ENV=/opt/vazhi-env      # Python with pyosmium (Ubuntu's own is too old)
PY=$ENV/bin/python3
PORT=8001

log() { printf '\n==> %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this with sudo." >&2
  exit 1
fi
if [ -z "${DOMAIN:-}" ]; then
  ip=$(curl -fsS --max-time 10 https://api.ipify.org || true)
  [ -n "$ip" ] || { echo "Set DOMAIN=... (could not find this machine's public IP)." >&2; exit 1; }
  DOMAIN="$(echo "$ip" | tr . -).sslip.io"
fi
export DEBIAN_FRONTEND=noninteractive

# 1. Swap ---------------------------------------------------------------------
if [ -z "${SKIP_SYSTEM:-}" ] && ! swapon --show | grep -q .; then
  log "Adding 4 GB of swap"
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# 2. Packages -------------------------------------------------------------------
log "Installing Python, Docker and Caddy"
apt-get update -q
pkgs="python3 python3-venv python3-shapely python3-pil curl ca-certificates"
[ -n "${SKIP_DOCKER:-}" ] || pkgs="$pkgs docker.io"
[ -n "${SKIP_SYSTEM:-}" ] || pkgs="$pkgs caddy iptables-persistent"
# shellcheck disable=SC2086
apt-get install -y -q $pkgs

if [ ! -x "$PY" ]; then
  python3 -m venv --system-site-packages "$ENV"
fi
"$PY" -m pip install -q --upgrade "osmium>=3.7"

# 3. Firewall -------------------------------------------------------------------
# Oracle's Ubuntu images block everything but SSH in iptables, on top of the
# cloud's own security list (see deploy/README.md for that part).
if [ -z "${SKIP_SYSTEM:-}" ]; then
  log "Opening ports 80 and 443"
  for p in 80 443; do
    iptables -C INPUT -p tcp --dport "$p" -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null ||
      iptables -I INPUT 1 -p tcp --dport "$p" -m conntrack --ctstate NEW -j ACCEPT
  done
  netfilter-persistent save >/dev/null
fi

# 4. The app --------------------------------------------------------------------
log "Unpacking the app into $APP"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$APP"
# Code is replaced; .cache (map data) and data/ (riders' bus times and stop
# names) are not in the archive, so they survive an update.
find "$APP" -mindepth 1 -maxdepth 1 ! -name .cache ! -name data -exec rm -rf {} +
tar -xzf "$ARCHIVE" -C "$APP"
mkdir -p "$APP/.cache" "$APP/data"

# 5. Map tiles and route service (first time only) ------------------------------
if [ -z "${SKIP_DOCKER:-}" ]; then
  systemctl enable --now docker >/dev/null 2>&1 || true
  if ! ls "$APP"/.cache/map/kerala*.mbtiles >/dev/null 2>&1; then
    log "Building the Kerala map (downloads ~180 MB, then a few minutes)"
    (cd "$APP" && "$PY" tools/build_map.py)
  fi
fi
chown -R "$USER_NAME:$USER_NAME" "$APP"

if [ -n "${SKIP_SYSTEM:-}" ]; then
  log "Done (test mode: no service or web server set up)"
  exit 0
fi

# 6. Service and web server -----------------------------------------------------
log "Setting up the Vazhi service"
cat > /etc/systemd/system/vazhi.service <<EOF
[Unit]
Description=Vazhi journey planner
After=network-online.target docker.service
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP
ExecStart=$PY serve.py --host 127.0.0.1 --port $PORT --no-browser
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable vazhi >/dev/null
systemctl restart vazhi

log "Setting up HTTPS for $DOMAIN"
cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:$PORT {
		# Plans stream their progress; pass it on as it comes.
		flush_interval -1
	}
}
EOF
systemctl enable caddy >/dev/null
systemctl restart caddy

for _ in $(seq 1 30); do
  curl -fsS -o /dev/null "http://127.0.0.1:$PORT/" && break
  sleep 2
done
log "Vazhi is up: https://$DOMAIN/app"
echo "(The HTTPS certificate can take a minute on the very first visit.)"

# 7. Route service --------------------------------------------------------------
# Last, because its first build is the slow part; the app already works and
# uses the public route servers until this one answers.
if [ -z "${SKIP_DOCKER:-}" ]; then
  log "Starting the route service (the first build takes 10-30 minutes)"
  if (cd "$APP" && "$PY" tools/routing_server.py start); then
    systemctl restart vazhi
    log "All done: https://$DOMAIN/app"
  else
    echo "The route service is still building in Docker; it is picked up by itself."
  fi
fi
