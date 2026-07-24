#!/usr/bin/env sh
set -eu

mkdir -p /data/staging

if [ ! -f "${ESTRATTO_CONFIG_PATH}" ]; then
  cp /app/config.yaml "${ESTRATTO_CONFIG_PATH}"
fi

exec python -m estratto.main -c "${ESTRATTO_CONFIG_PATH}" "$@"
