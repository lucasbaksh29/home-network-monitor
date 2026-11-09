"""mini-NOC: home network monitor — Session 3 + 6 + 7: web dashboard.

Reads devices.json and readings.db and serves a live status page at
http://localhost:5000

Session 6 adds an Internet section: devices in devices.json with
"group": "internet" (public DNS servers) are shown separately from
the home network, with an overall Internet UP / DEGRADED / DOWN
banner, so you can tell "the internet is down" apart from "a device
in my house is down" at a glance.

Session 7 adds a Port column: devices with a "port" in devices.json
show whether that TCP service answered (e.g. the router admin page
on port 80), and port_down / port_up events appear in Recent events.

Session 8 adds a latency history chart: a /api/history endpoint
returns recent latency readings for a device, and the dashboard
draws them as a line chart (Chart.js) with a device picker and a
time range, so a flaky connection shows up before it drops.

Run it:
    pip install -r requirements.txt   # first time only (Flask)
    python app.py
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request

HERE = Path(__file__).parent
DB_PATH = HERE / "readings.db"

app = Flask(__name__)


def load_devices():
    with open(HERE / "devices.json") as f:
        return json.load(f)["devices"]


def latest_readings():
    """Most recent reading per device.

    Returns {device_name: {"host": ..., "ts": ..., "up": bool,
    "latency": float | None, "port": int | None,
    "port_up": bool | None}}. Empty if no readings exist yet.
    """
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT device, host, ts, up, latency_ms, port, port_up"
        " FROM readings ORDER BY id DESC"
    ).fetchall()
    conn.close()
    latest = {}
    for device, host, ts, up, latency, port, port_up in rows:
        if device not in latest:
            latest[device] = {
                "host": host,
                "ts": ts,
                "up": bool(up),
                "latency": latency,
                "port": port,
                "port_up": None if port_up is None else bool(port_up),
            }
    return latest


def uptime(device, hours=24):
    """Share of UP readings for a device in the last `hours` hours.

    Returns a percentage (0-100), or None if there are no readings.
    """
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT up FROM readings WHERE device = ? AND ts >= ?", (device, since)
    ).fetchall()
    conn.close()
    if not rows:
        return None
    return sum(row[0] for row in rows) / len(rows) * 100


def internet_status(internet):
    """Overall internet state from the internet hosts.

    Returns {"label": ..., "cls": ...} where cls is a pill class:
    "up" (all reachable), "warn" (some reachable), "down" (none),
    "none" (no readings yet).
    """
    readings = [d for d in internet if d["up"] is not None]
    if not readings:
        return {"label": "NO DATA", "cls": "none"}
    up = sum(1 for d in readings if d["up"])
    if up == len(readings):
        return {"label": "UP", "cls": "up"}
    if up > 0:
        return {"label": f"DEGRADED — {up} of {len(readings)} reachable", "cls": "warn"}
    return {"label": "DOWN — check your connection", "cls": "down"}


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
                # Devices without a "group" are home devices.
                "group": device.get("group", "home"),
                "up": reading["up"] if reading else None,
                "latency": reading["latency"] if reading else None,
                "port": device.get("port"),
                "port_up": reading["port_up"] if reading else None,
                "last_seen": reading["ts"] if reading else None,
                "uptime": uptime(device["name"]),
            }
        )
    internet = [d for d in status if d["group"] == "internet"]
    home = [d for d in status if d["group"] != "internet"]
    return render_template(
        "index.html",
        internet=internet,
        home=home,
        inet=internet_status(internet),
        events=recent_events(),
        now=datetime.now().strftime("%H:%M:%S"),
    )


@app.route("/api/history")
def history():
    """Latency history for one device, as JSON.

    Query params: device (name), hours (default 24, clamped 1-168).
    Returns [{"ts": ..., "latency": ...}] in time order, most recent
    1000 readings at most. Only UP readings with a latency are
    included — a device that is down has no latency to plot.
    """
    device = request.args.get("device", "")
    try:
        hours = float(request.args.get("hours", "24"))
    except ValueError:
        hours = 24
    hours = max(1, min(hours, 168))
    if not DB_PATH.exists():
        return jsonify([])
    conn = sqlite3.connect(DB_PATH)
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT ts, latency_ms FROM ("
        "  SELECT ts, latency_ms FROM readings"
        "  WHERE device = ? AND ts >= ? AND up = 1 AND latency_ms IS NOT NULL"
        "  ORDER BY ts DESC LIMIT 1000"
        ") ORDER BY ts ASC",
        (device, since),
    ).fetchall()
    conn.close()
    return jsonify([{"ts": ts, "latency": latency} for ts, latency in rows])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
