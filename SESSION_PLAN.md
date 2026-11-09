# Home Network Monitor (mini-NOC) — Session Plan

Five morning sessions (9 a.m.–1 p.m. each). One goal per session.
If a session runs short, stop at its done-when check — don't bleed
into the next one.

## Session 1 — Setup + first ping monitor

Goal: the monitor runs and records real readings for your network.

- Install Python 3.11+ on the laptop, check with `python --version`
- Copy the project files to a folder on the laptop, open a terminal there
- `git init` and make a first commit (so the work is tracked from day one)
- Find your real device IPs: `ipconfig` on Windows — Default Gateway
  is the router; your laptop's IPv4 address is in the same output;
  your phone's IP is in its Wi-Fi settings
- Edit `devices.json` with the real IPs (router, laptop, phone, anything
  else on the network)
- Run `python monitor.py` — it pings each device and saves the results
  to `readings.db`
- Check the database has rows: `python -c "import sqlite3; print(sqlite3.connect('readings.db').execute('SELECT * FROM readings').fetchall())"`

Done when: `monitor.py` prints UP/DOWN for your router plus at least
two other devices, and `readings.db` has a row for each.

## Session 2 — Device discovery

Goal: the monitor finds devices on its own instead of a hand-written list.

- Write a ping sweep: loop over your subnet (e.g. 192.168.1.1 to
  192.168.1.254), ping each address, keep the ones that answer
- Add the responding devices to `devices.json` automatically
- Learn what the ARP table is: `arp -a` shows devices your laptop has
  already talked to — compare it with your sweep results
- Handle the slow part: a full sweep one address at a time is slow, so
  run the pings in parallel (threads) and time the difference

Done when: running the sweep finds your router, laptop, and phone
without you typing any IPs, and the sweep finishes in under a minute.

## Session 3 — Web dashboard

Goal: live status in the browser.

- Install Flask: `pip install -r requirements.txt`
- Build a small Flask app (`app.py`) with one page: a table of devices,
  current status, last-seen time, and latest latency
- The page reads from `readings.db` — no new storage needed
- Add auto-refresh so the page updates itself (a meta refresh tag is
  enough to start)
- Run it and open http://localhost:5000 in the browser

Done when: the dashboard shows every device with a live UP/DOWN
status and the latest latency, refreshing on its own.

## Session 4 — History + alerts

Goal: the monitor remembers, and tells you when something breaks.

- Uptime %: for each device, the share of readings that were UP over
  the last 24 hours — show it on the dashboard
- Latency history: store readings over time and show a simple chart or
  table of latency per device (even a plain list of the last 20
  readings counts)
- Down-events: when a device flips from UP to DOWN, write an event
  row to a new `events` table (device, time, "went down" / "came back")
- Show recent events on the dashboard
- Run the monitor on a schedule (every 60 seconds) so readings
  accumulate while the laptop is on

Done when: the dashboard shows uptime % per device and a list of
recent down-events, with at least one real event recorded.

## Session 5 — Portfolio-ready

Goal: someone hiring can look at this and understand it in two minutes.

- Rewrite the README: what it is, what it monitors, how to run it,
  a screenshot of the dashboard
- Take 2–3 screenshots (dashboard, terminal output, a down-event)
- Clean up the code: remove dead code, make sure everything runs from
  a fresh copy with only the README steps
- Push it to GitHub as a public repo with a clear name, e.g.
  `home-network-monitor`
- Write a short "what I learned" section in the README: networking
  (ICMP, ARP, subnets), monitoring (uptime, latency, alerting),
  SQLite, Flask

Done when: the repo is public, the README explains it with
screenshots, and a stranger could clone it and run Session 1.

---

## Extensions (Sessions 6–9) — added Nov 8, 2026

The five sessions above are done and the project is public on GitHub.
Four upgrades, built one at a time in this order:

- **Session 6 — Internet health check.** Ping two public DNS servers
  (8.8.8.8, 1.1.1.1) every pass. Dashboard gets an Internet section
  with an UP / DEGRADED / DOWN banner, so internet-down is easy to
  tell apart from device-down. (`app.py` + `templates/index.html`;
  `monitor.py` unchanged.)
- **Session 7 — Port checks.** Check TCP ports as well as ping, so the
  dashboard shows whether a *service* is up (router admin page,
  printer, game server), not just whether the device answers.
- **Session 8 — Latency graphs.** A latency-over-time chart per device
  on the dashboard, so a flaky connection shows up before it drops.
- **Session 9 — Email alerts.** The watch loop sends an email when a
  device goes down or comes back, so the dashboard does not have to
  be watched.
