#!/usr/bin/env bash
# Set up (or update) Idavazhi on a fresh Ubuntu server - an Oracle Cloud "Always
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
WORKER_BASE=8010        # extra copies listen on 8011, 8012, ... (vazhi-worker@8011)

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
[ -n "${SKIP_SYSTEM:-}" ] || pkgs="$pkgs caddy iptables-persistent unattended-upgrades"
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
log "Setting up the Idavazhi service"
# One copy of the app per processor core (2 to 4): Python does its work on
# one core per process, so more copies answer more people at once. The main
# copy (port 8001) also downloads map data and builds the place index; the
# others only answer requests.
WORKERS=$(nproc)
[ "$WORKERS" -lt 2 ] && WORKERS=2
[ "$WORKERS" -gt 4 ] && WORKERS=4
unit_body() {
  cat <<EOF
User=$USER_NAME
WorkingDirectory=$APP
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
# Kerala time for dates and logs (the server's clock runs on UTC).
Environment=TZ=Asia/Kolkata
Environment=VAZHI_MAX_PLANS=2
# Limits: no new privileges, read-only system, writes only to the app's folder.
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$APP
MemoryMax=4G
EOF
}
cat > /etc/systemd/system/vazhi.service <<EOF
[Unit]
Description=Idavazhi journey planner (main)
After=network-online.target docker.service
Wants=network-online.target

[Service]
ExecStart=$PY serve.py --host 127.0.0.1 --port $PORT --no-browser
$(unit_body)

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/vazhi-worker@.service <<EOF
[Unit]
Description=Idavazhi journey planner (extra copy on port %i)
After=vazhi.service

[Service]
ExecStart=$PY serve.py --host 127.0.0.1 --port %i --no-browser --worker
$(unit_body)

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable vazhi >/dev/null
systemctl restart vazhi
UPSTREAMS="127.0.0.1:$PORT"
# Stop extra copies left from a bigger machine, then start this one's.
for unit in $(systemctl list-units --all --plain --no-legend 'vazhi-worker@*' | awk '{print $1}'); do
  systemctl disable --now "$unit" >/dev/null 2>&1 || true
done
for n in $(seq 1 $((WORKERS - 1))); do
  port=$((WORKER_BASE + n))
  systemctl enable "vazhi-worker@$port" >/dev/null
  systemctl restart "vazhi-worker@$port"
  UPSTREAMS="$UPSTREAMS 127.0.0.1:$port"
done

log "Setting up HTTPS for $DOMAIN"
cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
	encode gzip
	# HTTPS only, from now on, for this site (the browser remembers).
	header Strict-Transport-Security "max-age=31536000"
	header -Server
	# Nothing Idavazhi accepts is anywhere near this big.
	@big header_regexp Content-Length ^[0-9]{6,}$
	respond @big "Request too large" 413
	request_body {
		max_size 64KB
	}
	reverse_proxy $UPSTREAMS {
		# Each visitor to the copy with the fewest open requests; a copy
		# that stops answering is skipped until it is healthy again.
		lb_policy least_conn
		health_uri /api/health
		health_interval 15s
		fail_duration 30s
		# Plans stream their progress; pass it on as it comes.
		flush_interval -1
		# A fresh local connection per request: the app's simple server does not
		# cope with connections being reused (error answers went missing).
		transport http {
			keepalive off
		}
	}
}
EOF
systemctl enable caddy >/dev/null
systemctl restart caddy

for _ in $(seq 1 30); do
  curl -fsS -o /dev/null "http://127.0.0.1:$PORT/" && break
  sleep 2
done
log "Idavazhi is up ($WORKERS copies): https://$DOMAIN/app"
echo "(The HTTPS certificate can take a minute on the very first visit.)"

# Backups: riders' data (bus times, stop names) every night at 3:30, the
# last 14 days kept in /var/backups/vazhi.
log "Setting up nightly backups"
mkdir -p /var/backups/vazhi
chmod 700 /var/backups/vazhi
cat > /usr/local/bin/vazhi-backup <<EOF
#!/bin/sh
set -e
umask 077                     # riders' data: readable by root only
day=\$(date +%Y-%m-%d)
for db in $APP/data/*.db; do
  [ -f "\$db" ] || continue
  $PY -c 'import sqlite3, sys; d = sqlite3.connect(sys.argv[2]); sqlite3.connect(sys.argv[1]).backup(d); d.close()' "\$db" "/var/backups/vazhi/\$(basename "\$db" .db)-\$day.db"
done
find /var/backups/vazhi -name '*.db' -mtime +14 -delete
EOF
chmod 755 /usr/local/bin/vazhi-backup
echo "30 3 * * * root TZ=Asia/Kolkata /usr/local/bin/vazhi-backup" > /etc/cron.d/vazhi-backup

# 7. Route service --------------------------------------------------------------
# Last, because its first build is the slow part; the app already works and
# uses the public route servers until this one answers.
if [ -z "${SKIP_DOCKER:-}" ]; then
  log "Starting the route service (the first build takes 10-30 minutes)"
  if (cd "$APP" && "$PY" tools/routing_server.py start); then
    systemctl restart vazhi 'vazhi-worker@*' 
    log "All done: https://$DOMAIN/app"
  else
    echo "The route service is still building in Docker; it is picked up by itself."
  fi
fi
