#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/../.."

python3 -m pip install -r packaging/requirements-build.txt
python3 -m PyInstaller packaging/pyinstaller/Estratto.spec --noconfirm --clean

APP_PATH="dist/Estratto.app"
if [ -d "$APP_PATH" ]; then
  mkdir -p dist/macos
  ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "dist/macos/Estratto-macOS.zip"
  echo "Built $APP_PATH"
  echo "Zipped dist/macos/Estratto-macOS.zip"
else
  echo "Expected $APP_PATH was not created"
  exit 1
fi
