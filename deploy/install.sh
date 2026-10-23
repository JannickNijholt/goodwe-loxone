#!/usr/bin/env bash
# Install or update the GoodWe → Modbus bridge on Debian/Ubuntu (e.g. a Proxmox LXC).
# Run as root from a checkout of this repository:  ./deploy/install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/goodwe-modbus
SERVICE_USER=goodwe-modbus

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo $0)" >&2
  exit 1
fi

echo "==> Installing system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv >/dev/null

python3 - <<'PY'
import sys
if sys.version_info < (3, 9):
    sys.exit(f"Python 3.9+ required, found {sys.version.split()[0]}")
PY

echo "==> Creating service user"
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"

echo "==> Installing the bridge into $PREFIX"
mkdir -p "$PREFIX"
[[ -d "$PREFIX/venv" ]] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q --upgrade "$SRC"

echo "==> Installing systemd service"
[[ -f /etc/goodwe-modbus.env ]] || install -m 0644 "$SRC/deploy/goodwe-modbus.env" /etc/goodwe-modbus.env
install -m 0644 "$SRC/deploy/goodwe-modbus.service" /etc/systemd/system/goodwe-modbus.service
systemctl daemon-reload
systemctl enable goodwe-modbus >/dev/null

if ! grep -qE '^GOODWE_HOST=.+' /etc/goodwe-modbus.env; then
  echo
  echo "Set GOODWE_HOST (the inverter IP) in /etc/goodwe-modbus.env, then: systemctl restart goodwe-modbus"
  exit 0
fi
systemctl restart goodwe-modbus
sleep 3
systemctl --no-pager --lines=5 status goodwe-modbus || true
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PORT="$(grep -E '^GOODWE_WEB_PORT=' /etc/goodwe-modbus.env | cut -d= -f2 || true)"
echo
echo "Done. Status page: http://${IP:-<this-host>}:${PORT:-8080}"
