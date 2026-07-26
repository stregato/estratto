#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/../.."

python3 -m pip install -r packaging/requirements-build.txt
python3 -m PyInstaller packaging/pyinstaller/Estratto.spec --noconfirm --clean

APPDIR="dist/AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications"
cp -R dist/Estratto/* "$APPDIR/usr/bin/"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/Estratto" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/estratto.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Estratto
Exec=Estratto
Categories=Office;
EOF
cp "$APPDIR/estratto.desktop" "$APPDIR/usr/share/applications/estratto.desktop"

if [ ! -x "./appimagetool-x86_64.AppImage" ]; then
  echo "Download appimagetool-x86_64.AppImage into the repo root first."
  exit 1
fi

ARCH=x86_64 ./appimagetool-x86_64.AppImage "$APPDIR" "dist/Estratto-x86_64.AppImage"
echo "Built dist/Estratto-x86_64.AppImage"
