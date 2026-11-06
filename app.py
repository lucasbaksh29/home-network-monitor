"""mini-NOC: home network monitor — Session 3: web dashboard.

Reads devices.json and readings.db and serves a live status page at
http://localhost:5000

Run it:
    pip install -r requirements.txt   # first time only (Flask)
    python app.py
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template

HERE = Path(__file__).parent
DB_PATH = HERE / "readings.db"

app = Flask(__name__)


def load_devices():
    with open(HERE / "devices.json") as f:
        return json.load(f)["devices"]


def latest_readings():
    """Most recent reading per device.

    Returns {device_name: {"host": ..., "ts": ..., "up": bool,
    "latency": float | None}}. Empty if no readings exist yet.
    """
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT device, host, ts, up, latency_ms FROM readings ORDER BY id DESC"
    ).fetchall()
    conn.close()
    latest = {}
    for device, host, ts, up, latency in rows:
        if device not in latest:
            latest[device] = {"host": host, "ts": ts, "up": bool(up), "latency": latency}
    return latest


def uptime(device, hours=24):
    """Share of UP readings for a device in the last `hours` hours.

    Returns a percentage (0-100), or None if there are no readings.
    """
    if not DB_PATH.exists():
        return None
    from datetime import timedelta

    conn = sqlite3.connect(DB_PATH)
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT up FROM readings WHERE device = ? AND ts >= ?", (device, since)
    ).fetchall()
    conn.close()
    if not rows:
        return None
    return sum(row[0] for row in rows) / len(rows) * 100


def recent_events(limit=10):
    """Latest rows from the events table, newest first.

    Returns [] if the table does not exist yet (monitor.py creates it
    on its first run).
    """
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone()
    if not table:
        conn.close()
        return []
    rows = conn.execute(
        "SELECT ts, device, host, event FROM events ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [
        {"ts": ts, "device": device, "host": host, "event": event}
        for ts, device, host, event in rows
    ]


@app.route("/")
def index():
    devices = load_devices()
    latest = latest_readings()
    status = []
    for device in devices:
        reading = latest.get(device["name"])
        status.append(
            {
                "name": device["name"],
                "host": device["host"],
                "up": reading["up"] if reading else None,
                "latency": reading["latency"] if reading else None,
                "last_seen": reading["ts"] if reading else None,
                "uptime": uptime(device["name"]),
            }
        )
    return render_template(
        "index.html",
        devices=status,
        events=recent_events(),
        now=datetime.now().strftime("%H:%M:%S"),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
