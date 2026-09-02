#!/usr/bin/env bash
# Install the Phase 1 watcher on a Raspberry Pi (or any systemd Linux).
#
# Run this ON the Pi, from the project directory.
#
#   chmod +x deploy/install-pi.sh && ./deploy/install-pi.sh
#
# NOT A CRON JOB, deliberately. watcher.run() owns three timers at different
# frequencies -- poll 60 s, health 5 min, canary 1 h. A per-minute cron would
# fire `--once`, which runs all three every minute: 1,440 canary rows a day
# instead of 24, written into the very heartbeat table used to detect gaps.
# systemd also supervises; cron does not notice when the job stops.
set -euo pipefail

APP_USER="${SUDO_USER:-$USER}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DB_DIR="${HOME}/.local/share/hew"

echo "user      : ${APP_USER}"
echo "project   : ${APP_DIR}"
echo "database  : ${DB_DIR}/hew.db"

command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }
python3 - <<'PY'
import sys
assert sys.version_info >= (3, 9), f"need python 3.9+, have {sys.version}"
print(f"python    : {sys.version.split()[0]}  (zero third-party deps required)")
PY

mkdir -p "${DB_DIR}"

# Sanity: the watcher must start and complete one real cycle before we
# install a unit that will restart it forever.
echo
echo "-- pre-flight: one live cycle --"
cd "${APP_DIR}"
PYTHONPATH="${APP_DIR}" python3 -m hew.watcher --once --db "${DB_DIR}/preflight.db"
rm -f "${DB_DIR}/preflight.db"
echo "-- pre-flight OK --"
echo

UNIT=/etc/systemd/system/hew-watcher.service
sudo tee "${UNIT}" >/dev/null <<UNITEOF
[Unit]
Description=Himalayan Early Warning - Phase 1 catalogue watcher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=PYTHONPATH=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -m hew.watcher --db ${DB_DIR}/hew.db
Restart=always
RestartSec=10
# Phase 1 MEASURES. It does not warn. Do not add --allow-dispatch here
# without an institutional owner and a human gate (see CONSTRAINTS.md).
Nice=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNITEOF

# Status page: a SEPARATE unit on purpose. It is read-only and must never be
# able to slow or crash the watcher. If it dies, detection continues.
STATUS_UNIT=/etc/systemd/system/hew-status.service
sudo tee "${STATUS_UNIT}" >/dev/null <<STATUSEOF
[Unit]
Description=Himalayan Early Warning - read-only status page
After=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=PYTHONPATH=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -m hew.status --db ${DB_DIR}/hew.db --port 8080
Restart=always
RestartSec=10
Nice=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
STATUSEOF

sudo systemctl daemon-reload
sudo systemctl enable --now hew-watcher hew-status
sleep 3
sudo systemctl --no-pager --lines=12 status hew-watcher || true
echo
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "status page:  http://${IP:-<pi-ip>}:8080/"

cat <<'DONE'

installed.

  dashboard          http://<pi-ip>:8080/       (read-only, LAN only)
  machine health     http://<pi-ip>:8080/health  (200 ok / 503 stale)
  follow the log     journalctl -u hew-watcher -f
  is it healthy      journalctl -u hew-watcher | grep -E 'HEALTH|CANARY' | tail
  stop               sudo systemctl stop hew-watcher hew-status
  restart count      systemctl show hew-watcher -p NRestarts

A restart count climbing over days means the Pi is browning out, not that
the software is broken. Check `vcgencmd get_throttled` before blaming the
watcher -- 0x0 is clean, bit 16 means undervoltage has occurred.
DONE
