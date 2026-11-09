"""mini-NOC: home network monitor — Sessions 1 + 4 + 7.

Pings every device listed in devices.json, records the result in
SQLite (readings.db), logs UP/DOWN changes to the events table,
and prints a simple status table.

Session 7 adds TCP port checks: a device can list a "port" in
devices.json (e.g. the router's admin page on port 80). When the
device answers a ping, the monitor also tries a TCP connect to that
port, so the dashboard can show whether the *service* is up, not
just whether the device is alive. Port changes are logged as
port_down / port_up events. A port is only checked while the device
is up — if the ping fails, the port state is unknown, not down.

Run it:
    python monitor.py            # one pass
    python monitor.py --watch    # repeat every 60 seconds until Ctrl+C
"""

import json
import platform
import re
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "readings.db"
INTERVAL_SECONDS = 60
PORT_TIMEOUT_SECONDS = 2


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


def check_port(host, port, timeout=PORT_TIMEOUT_SECONDS):
    """Try a TCP connect to host:port. Returns True if it connects."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            device TEXT NOT NULL,
            host TEXT NOT NULL,
            up INTEGER NOT NULL,
            latency_ms REAL,
            port INTEGER,
            port_up INTEGER
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
    # Session 7: add the port columns to a database created before
    # they existed. Fresh databases already have them (see above).
    cols = [row[1] for row in conn.execute("PRAGMA table_info(readings)")]
    if "port" not in cols:
        conn.execute("ALTER TABLE readings ADD COLUMN port INTEGER")
    if "port_up" not in cols:
        conn.execute("ALTER TABLE readings ADD COLUMN port_up INTEGER")
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


def last_port_statuses(conn):
    """Most recent port state per device: {device: 0/1}.

    Only covers readings where a port was actually checked.
    """
    rows = conn.execute(
        "SELECT device, port_up FROM readings"
        " WHERE port_up IS NOT NULL ORDER BY id DESC"
    ).fetchall()
    statuses = {}
    for device, port_up in rows:
        if device not in statuses:
            statuses[device] = port_up
    return statuses


def run_pass(conn):
    devices = load_devices()
    now = datetime.now().isoformat(timespec="seconds")
    previous = last_statuses(conn)
    prev_ports = last_port_statuses(conn)
    events = []

    print(f"{'Device':<20} {'Host':<16} {'Status':<6} {'Port':<10} Latency")
    print("-" * 65)
    for device in devices:
        up, latency = ping(device["host"])
        port = device.get("port")
        # Only check the port while the device answers a ping.
        port_up = check_port(device["host"], port) if (up and port) else None
        conn.execute(
            "INSERT INTO readings (ts, device, host, up, latency_ms, port, port_up)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now, device["name"], device["host"], int(up), latency, port,
             None if port_up is None else int(port_up)),
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
        # Log a port event when a checked service changes state.
        prev_port = prev_ports.get(device["name"])
        if (
            port is not None
            and port_up is not None
            and prev_port is not None
            and prev_port != int(port_up)
        ):
            event = "port_down" if not port_up else "port_up"
            conn.execute(
                "INSERT INTO events (ts, device, host, event) VALUES (?, ?, ?, ?)",
                (now, device["name"], device["host"], event),
            )
            events.append((device["name"], event))
        status = "UP" if up else "DOWN"
        latency_text = f"{latency:.1f} ms" if latency is not None else "-"
        if port is None:
            port_text = "-"
        elif port_up is None:
            port_text = f"{port}: ?"
        else:
            port_text = f"{port}: {'UP' if port_up else 'DOWN'}"
        print(f"{device['name']:<20} {device['host']:<16} {status:<6} {port_text:<10} {latency_text}")
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
