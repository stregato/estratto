#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

host_platform() {
  case "$(uname -s)" in
    Darwin)
      echo "macos"
      ;;
    Linux)
      echo "linux"
      ;;
    MINGW*|MSYS*|CYGWIN*)
      echo "windows"
      ;;
    *)
      echo "unsupported"
      ;;
  esac
}

run_macos() {
  if [ "$(host_platform)" != "macos" ]; then
    echo "macOS builds must run on macOS."
    exit 1
  fi
  bash packaging/macos/build-app.sh
}

run_linux() {
  if [ "$(host_platform)" != "linux" ]; then
    echo "Linux AppImage builds must run on Linux."
    exit 1
  fi
  bash packaging/linux/build-appimage.sh
}

run_windows() {
  if [ "$(host_platform)" != "windows" ]; then
    echo "Windows installer builds must run on Windows."
    exit 1
  fi
  powershell -ExecutionPolicy Bypass -File packaging\\windows\\build-installer.ps1
}

run_all() {
  local current
  current="$(host_platform)"
  case "$current" in
    macos)
      run_macos
      echo
      echo "Built host-native target: macos"
      echo "Skipped windows: requires a native windows build host."
      echo "Skipped linux: requires a native linux build host."
      ;;
    linux)
      run_linux
      echo
      echo "Built host-native target: linux"
      echo "Skipped macos: requires a native macos build host."
      echo "Skipped windows: requires a native windows build host."
      ;;
    windows)
      run_windows
      echo
      echo "Built host-native target: windows"
      echo "Skipped macos: requires a native macos build host."
      echo "Skipped linux: requires a native linux build host."
      ;;
    *)
      echo "Unsupported host platform: $(uname -s)"
      exit 1
      ;;
  esac
}

TARGET="${1:-all}"

case "$TARGET" in
  all)
    run_all
    ;;
  macos)
    run_macos
    ;;
  linux)
    run_linux
    ;;
  windows)
    run_windows
    ;;
  *)
    echo "Usage: ./packaging/build.sh [all|macos|windows|linux]"
    exit 1
    ;;
esac
