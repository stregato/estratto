#!/usr/bin/env bash
# Installs Estratto on a Raspberry Pi (or any Debian-based Linux box) as a systemd service.
# Run from the repo root: bash deploy/install.sh
set -euo pipefail

ESTRATTO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ESTRATTO_USER="${SUDO_USER:-$USER}"
SERVICE_MODE="${1:-web}"  # "web" (default) or "listen" for the headless-only unit

echo "Installing Estratto from ${ESTRATTO_DIR} for user ${ESTRATTO_USER}"

if ! command -v python3 >/dev/null; then
  echo "python3 not found. On Raspberry Pi OS: sudo apt update && sudo apt install -y python3 python3-venv python3-pip" >&2
  exit 1
fi

PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "Using system python3 (${PY_VERSION}). Raspberry Pi OS Bookworm ships 3.11, which is well-tested here."

python3 -m venv "${ESTRATTO_DIR}/venv"
"${ESTRATTO_DIR}/venv/bin/pip" install --upgrade pip
"${ESTRATTO_DIR}/venv/bin/pip" install -r "${ESTRATTO_DIR}/requirements.txt"

mkdir -p "${ESTRATTO_DIR}/staging" "${ESTRATTO_DIR}/needs-review"

if [ ! -f "${ESTRATTO_DIR}/config.yaml" ]; then
  echo "No config.yaml found -- copy and edit the example before starting the service." >&2
fi

UNIT_NAME="estratto-${SERVICE_MODE}.service"
UNIT_TEMPLATE="${ESTRATTO_DIR}/deploy/${UNIT_NAME}.template"
if [ ! -f "${UNIT_TEMPLATE}" ]; then
  echo "Unknown service mode '${SERVICE_MODE}'. Use 'web' or 'listen'." >&2
  exit 1
fi

sed \
  -e "s#__ESTRATTO_DIR__#${ESTRATTO_DIR}#g" \
  -e "s#__ESTRATTO_USER__#${ESTRATTO_USER}#g" \
  "${UNIT_TEMPLATE}" | sudo tee "/etc/systemd/system/${UNIT_NAME}" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable "${UNIT_NAME}"

echo
echo "Installed. Next steps:"
echo "  1. Edit ${ESTRATTO_DIR}/config.yaml with your real values (or do it via the web UI after first start)."
echo "  2. sudo systemctl start ${UNIT_NAME}"
echo "  3. sudo journalctl -u ${UNIT_NAME} -f"
if [ "${SERVICE_MODE}" = "web" ]; then
  echo "  4. Open http://<pi-ip>:8000 and log into Telegram from the Login tab (first run only)."
fi
