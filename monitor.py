"""mini-NOC: home network monitor — Sessions 1 + 4 + 7 + 9.

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

Session 9 adds email alerts: when a pass logs any down/up or
port_down/port_up events, the monitor sends one email listing them,
using the settings in alerts.json (Gmail SMTP + app password).
alerts.json is local-only and gitignored — it holds the password,
so it must never be committed. Without alerts.json the monitor
just does not send mail. Run `python monitor.py --test-alert` to
send a test email and check the setup works.

Run it:
    python monitor.py            # one pass
    python monitor.py --watch    # repeat every 60 seconds until Ctrl+C
    python monitor.py --test-alert  # send a test email, then exit
"""

import json
import platform
import re
import smtplib
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "readings.db"
ALERTS_CONFIG = HERE / "alerts.json"
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


def load_alert_config():
    """Alert settings from alerts.json, or None if the file is absent.

    The file is local-only (gitignored) because it holds the Gmail
    app password. Without it the monitor just does not send mail.
    """
    if not ALERTS_CONFIG.exists():
        return None
    with open(ALERTS_CONFIG) as f:
        return json.load(f)


EVENT_LABELS = {
    "down": "went DOWN",
    "up": "came back",
    "port_down": "service went DOWN",
    "port_up": "service came back",
}


def send_alert(config, events, now, test=False):
    """Send one email listing the events from this pass.

    events: [(device, event)] where event is down/up/port_down/
    port_up. Returns True if sent, False if there was nothing to
    send or sending failed. A failed send never crashes the monitor.
    """
    if not events and not test:
        return False
    if test:
        subject = "mini-NOC test alert"
        body = (
            "This is a test email from your home network monitor.\n\n"
            "If you got this, your email alerts are working — the monitor "
            "will email you when a device goes down or comes back.\n\n"
            "Dashboard: http://localhost:5000"
        )
    else:
        lines = [
            f"- {name} {EVENT_LABELS.get(event, event)} at {now}"
            for name, event in events
        ]
        subject = (
            f"mini-NOC alert: {events[0][0]} "
            f"{EVENT_LABELS.get(events[0][1], events[0][1])}"
        )
        if len(events) > 1:
            subject = f"mini-NOC alert: {len(events)} device changes"
        body = (
            "Home network monitor events:\n\n"
            + "\n".join(lines)
            + "\n\nDashboard: http://localhost:5000"
        )
    msg = EmailMessage()
    msg["From"] = config["from"]
    msg["To"] = config["to"]
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(
            config.get("smtp_server", "smtp.gmail.com"),
            config.get("smtp_port", 587),
            timeout=15,
        ) as server:
            server.starttls()
            server.login(config["from"], config["password"])
            server.send_message(msg)
        print(f"  ALERT SENT: {subject}")
        return True
    except (OSError, smtplib.SMTPException) as exc:
        print(f"  ALERT FAILED: {exc}")
        return False


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


def run_pass(conn, alert_config=None):
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
    if alert_config is not None:
        send_alert(alert_config, events, now)
    print(f"Saved {len(devices)} reading(s) to {DB_PATH.name}")


def main():
    if "--test-alert" in sys.argv:
        config = load_alert_config()
        if config is None:
            print(
                "No alerts.json found. Copy alerts.example.json to"
                " alerts.json and fill in your Gmail app password first."
            )
            return
        send_alert(
            config, [], datetime.now().isoformat(timespec="seconds"), test=True
        )
        return
    conn = init_db()
    alert_config = load_alert_config()
    if alert_config is None:
        print("No alerts.json — email alerts off (see alerts.example.json).")
    watch = "--watch" in sys.argv
    try:
        while True:
            run_pass(conn, alert_config)
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
