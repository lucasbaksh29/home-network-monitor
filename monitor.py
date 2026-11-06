"""mini-NOC: home network monitor — Sessions 1 + 4.

Pings every device listed in devices.json, records the result in
SQLite (readings.db), logs UP/DOWN changes to the events table,
and prints a simple status table.

Run it:
    python monitor.py            # one pass
    python monitor.py --watch    # repeat every 60 seconds until Ctrl+C
"""

import json
import platform
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "readings.db"
INTERVAL_SECONDS = 60


def load_devices():
    with open(HERE / "devices.json") as f:
        return json.load(f)["devices"]


def ping(host):
    """Ping a host once. Returns (up: bool, latency_ms: float | None)."""
    if platform.system() == "Windows":
        cmd = ["ping", "-n", "1", "-w", "1000", host]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", host]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, None
    if result.returncode != 0:
        return False, None
    text = result.stdout + result.stderr
    match = re.search(r"time[=<]([\d.]+)\s*ms", text)
    return True, float(match.group(1)) if match else None


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            device TEXT NOT NULL,
            host TEXT NOT NULL,
            up INTEGER NOT NULL,
            latency_ms REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            device TEXT NOT NULL,
            host TEXT NOT NULL,
            event TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


def last_statuses(conn):
    """Most recent UP/DOWN per device: {device: 0/1}."""
    rows = conn.execute("SELECT device, up FROM readings ORDER BY id DESC").fetchall()
    statuses = {}
    for device, up in rows:
        if device not in statuses:
            statuses[device] = up
    return statuses


def run_pass(conn):
    devices = load_devices()
    now = datetime.now().isoformat(timespec="seconds")
    previous = last_statuses(conn)
    events = []

    print(f"{'Device':<20} {'Host':<16} {'Status':<6} Latency")
    print("-" * 55)
    for device in devices:
        up, latency = ping(device["host"])
        conn.execute(
            "INSERT INTO readings (ts, device, host, up, latency_ms)"
            " VALUES (?, ?, ?, ?, ?)",
            (now, device["name"], device["host"], int(up), latency),
        )
        # Log a down/up event when a device changes state.
        prev = previous.get(device["name"])
        if prev is not None and prev != int(up):
            event = "down" if not up else "up"
            conn.execute(
                "INSERT INTO events (ts, device, host, event) VALUES (?, ?, ?, ?)",
                (now, device["name"], device["host"], event),
            )
            events.append((device["name"], event))
        status = "UP" if up else "DOWN"
        latency_text = f"{latency:.1f} ms" if latency is not None else "-"
        print(f"{device['name']:<20} {device['host']:<16} {status:<6} {latency_text}")
    conn.commit()
    for name, event in events:
        print(f"  EVENT: {name} went {event.upper()} at {now}")
    print(f"Saved {len(devices)} reading(s) to {DB_PATH.name}")


def main():
    conn = init_db()
    watch = "--watch" in sys.argv
    try:
        while True:
            run_pass(conn)
            if not watch:
                break
            print(f"\nNext pass in {INTERVAL_SECONDS}s (Ctrl+C to stop)\n")
            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
