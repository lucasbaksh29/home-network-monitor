"""mini-NOC: home network monitor — Session 2: device discovery.

Sweeps the local /24 subnet with parallel pings, compares the results
with the ARP table, and (with --save) writes newly found devices into
devices.json.

Run it:
    python discover.py          # just show what is live
    python discover.py --save   # also add new devices to devices.json
"""

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from monitor import ping

HERE = Path(__file__).parent


def load_devices():
    with open(HERE / "devices.json") as f:
        return json.load(f)["devices"]


def subnet_prefix():
    """First three octets of the router's address from devices.json."""
    devices = load_devices()
    router = next((d for d in devices if d["name"].lower() == "router"), devices[0])
    return ".".join(router["host"].split(".")[:3])


def sweep(prefix, workers=50):
    """Ping every address in prefix.1-254 in parallel.

    Returns {host: latency_ms} for the hosts that answered.
    One address at a time would take ~4 minutes; 50 workers
    brings it down to a few seconds.
    """
    hosts = [f"{prefix}.{i}" for i in range(1, 255)]
    live = {}

    def check(host):
        up, latency = ping(host)
        return host, up, latency

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for host, up, latency in pool.map(check, hosts):
            if up:
                live[host] = latency
    return live


def arp_table():
    """Parse `arp -a` into {ip: mac}.

    ARP is the table your laptop keeps of devices it has already
    talked to on the local network — a second source of truth to
    compare against the sweep.
    """
    try:
        result = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    entries = {}
    for match in re.finditer(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:-]{17})", result.stdout):
        entries[match.group(1)] = match.group(2)
    return entries


def by_last_octet(host):
    return int(host.split(".")[-1])


def main():
    save = "--save" in sys.argv
    devices = load_devices()
    known = {d["host"]: d["name"] for d in devices}
    prefix = subnet_prefix()

    print(f"Sweeping {prefix}.1-254 ...")
    live = sweep(prefix)
    arp = arp_table()

    print(f"\n{len(live)} host(s) responding to ping:")
    for host in sorted(live, key=by_last_octet):
        name = known.get(host, "")
        latency = f"{live[host]:.1f} ms" if live[host] is not None else "-"
        print(f"  {host:<16} {name:<12} {latency}")

    print(f"\n{len(arp)} entr(ies) in the ARP table:")
    for host in sorted(arp, key=by_last_octet):
        print(f"  {host:<16} {arp[host]}")

    in_arp_not_ping = sorted(set(arp) - set(live), key=by_last_octet)
    if in_arp_not_ping:
        print("\nIn ARP but not answering ping (asleep, or blocking ICMP):")
        for host in in_arp_not_ping:
            print(f"  {host}")

    if save:
        new = sorted((h for h in live if h not in known), key=by_last_octet)
        if new:
            for host in new:
                devices.append({"name": f"device-{host.replace('.', '-')}", "host": host})
            with open(HERE / "devices.json", "w") as f:
                json.dump({"devices": devices}, f, indent=2)
            print(f"\nAdded {len(new)} new device(s) to devices.json")
        else:
            print("\nNo new devices to add.")
    else:
        print("\nRun with --save to add new devices to devices.json.")


if __name__ == "__main__":
    main()
