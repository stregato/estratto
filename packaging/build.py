from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], *, shell: bool = False) -> None:
    print("+", " ".join(cmd) if not shell else cmd[0], flush=True)
    subprocess.run(cmd if not shell else cmd[0], cwd=ROOT, check=True, shell=shell)


def host_platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    raise SystemExit(f"Unsupported host platform: {platform.platform()}")


def build_macos() -> None:
    if host_platform() != "macos":
        raise SystemExit("macOS builds must run on macOS.")
    run(["bash", "packaging/macos/build-app.sh"])


def build_linux() -> None:
    if host_platform() != "linux":
        raise SystemExit("Linux AppImage builds must run on Linux.")
    run(["bash", "packaging/linux/build-appimage.sh"])


def build_windows() -> None:
    if host_platform() != "windows":
        raise SystemExit("Windows installer builds must run on Windows.")
    run(
        [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "packaging\\windows\\build-installer.ps1",
        ]
    )


BUILDERS = {
    "macos": build_macos,
    "windows": build_windows,
    "linux": build_linux,
}


def build_all_supported() -> None:
    current = host_platform()
    skipped = [name for name in BUILDERS if name != current]
    BUILDERS[current]()
    print("", flush=True)
    print(f"Built host-native target: {current}", flush=True)
    for name in skipped:
        print(f"Skipped {name}: requires a native {name} build host.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Estratto desktop packages for macOS, Windows, or Linux."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="all",
        choices=["all", "macos", "windows", "linux"],
        help="Package target to build. 'all' builds the host-native target and reports the rest.",
    )
    args = parser.parse_args()

    if args.target == "all":
        build_all_supported()
        return

    BUILDERS[args.target]()


if __name__ == "__main__":
    main()
