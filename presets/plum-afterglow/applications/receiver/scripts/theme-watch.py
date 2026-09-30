#!/usr/bin/env python3
"""Keep Eww's CSS synchronized after a Cinnamon theme switch."""
import fcntl
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
SYNC = ROOT / "config" / "scripts" / "theme-sync.py"
EWW = ROOT / "bin" / "eww"
CONFIG = ROOT / "config"
LOCK = ROOT / "state" / "theme-watch.lock"


def theme_name():
    result = subprocess.run(
        ["gsettings", "get", "org.cinnamon.desktop.interface", "gtk-theme"],
        capture_output=True,
        text=True,
        check=False,
        timeout=3,
    )
    return result.stdout.strip().strip("'")


def sync_and_reload():
    subprocess.run([sys.executable, str(SYNC)], check=False, timeout=6)
    subprocess.run([str(EWW), "-c", str(CONFIG), "reload"], check=False, timeout=10)


def main():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        current = theme_name()
        while True:
            time.sleep(2)
            changed = theme_name()
            if changed and changed != current:
                current = changed
                sync_and_reload()


if __name__ == "__main__":
    main()
