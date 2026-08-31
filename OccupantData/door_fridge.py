#!/usr/bin/env python3
"""
door_fridge.py
Prints OPEN→CLOSED and CLOSED→OPEN transitions for the fridge MT20
door sensor over the last 24 hours, with duration in each state.

Requirements: pip install requests

Usage:
    python door_fridge.py
"""

import sys
import requests
from datetime import datetime, timedelta, timezone

# ── Config ─────────────────────────────────────────────────────────────────
MERAKI_API_KEY = "852c64663d82a8db0dc40202bd11200986a3d8e1"
MT20_MAC       = "a8:46:9d:3d:fd:fe"

MERAKI_BASE_URL = "https://api.meraki.com/api/v1"
MERAKI_HEADERS  = {
    "X-Cisco-Meraki-API-Key": MERAKI_API_KEY,
    "Content-Type": "application/json",
    "Accept":        "application/json",
}

# ── API helpers ─────────────────────────────────────────────────────────────

def meraki_get(path, params=None):
    url = f"{MERAKI_BASE_URL}{path}"
    r = requests.get(url, headers=MERAKI_HEADERS, params=params, timeout=15)
    if not r.ok:
        raise requests.HTTPError(
            f"{r.status_code} {r.reason}  body={r.text!r}",
            response=r,
        )
    return r.json()


def normalise_mac(mac: str) -> str:
    return mac.lower().replace("-", ":").replace(".", ":")


def discover_sensor(mac: str):
    """Return (serial, model, org_id, org_name) for a sensor by MAC address."""
    target = normalise_mac(mac)
    print("Querying Meraki organisations...")
    orgs = meraki_get("/organizations")
    for org in orgs:
        try:
            devices = meraki_get(f"/organizations/{org['id']}/devices")
        except Exception:
            continue
        for dev in devices:
            if normalise_mac(dev.get("mac", "")) == target:
                return dev["serial"], dev.get("model", "MT"), org["id"], org["name"]
    return None, None, None, None


# ── Door readings ────────────────────────────────────────────────────────────

def get_door_readings_24h(org_id: str, serial: str) -> list:
    """
    Fetch all door state readings for the last 24 hours, handling pagination.
    Returns a list of reading dicts sorted oldest → newest.

    Note: /sensor/readings/history returns a flat list of reading objects
    (each item has serial, network, ts, metric, door fields at the top level),
    unlike /sensor/readings/latest which nests them under a 'readings' key.
    Results are newest-first; paginate via endingBefore.
    """
    now   = datetime.now(timezone.utc)
    start = now - timedelta(hours=24)

    params = {
        "serials[]": serial,
        "metrics[]": "door",
        "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endTime":   now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "perPage":   1000,
    }

    all_readings = []
    while True:
        data = meraki_get(
            f"/organizations/{org_id}/sensor/readings/history", params=params
        )
        if not data:
            break
        # Each item in the flat list is a reading object directly
        all_readings.extend(data)
        if len(data) < 1000:
            break
        # Results are newest-first; page backwards using the oldest ts returned
        params = dict(params)
        params["endingBefore"] = data[-1]["ts"]

    return sorted(all_readings, key=lambda r: r["ts"])


# ── Formatting ───────────────────────────────────────────────────────────────

def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    elif s < 3600:
        m, s = divmod(s, 60)
        return f"{m}m {s:02d}s"
    else:
        h, rem = divmod(s, 3600)
        m, s   = divmod(rem, 60)
        return f"{h}h {m:02d}m {s:02d}s"


# ── Rendering ────────────────────────────────────────────────────────────────

W = 72

def display_transitions(serial: str, model: str, org_name: str, readings: list):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Detect all state changes ─────────────────────────────────────────────
    transitions = []
    prev = None
    for reading in readings:
        is_open = reading.get("door", {}).get("open")
        if is_open is None:
            continue
        if prev is not None and prev["open"] != is_open:
            transitions.append({
                "ts":       reading["ts"],
                "from":     prev["open"],
                "to":       is_open,
                "duration": (
                    datetime.fromisoformat(reading["ts"].replace("Z", "+00:00")) -
                    datetime.fromisoformat(prev["ts"].replace("Z", "+00:00"))
                ).total_seconds(),
            })
        prev = {"open": is_open, "ts": reading["ts"]}

    # ── Current state ────────────────────────────────────────────────────────
    current_state = None
    current_since = None
    if prev is not None:
        current_state = "OPEN" if prev["open"] else "CLOSED"
        ts_dt = datetime.fromisoformat(prev["ts"].replace("Z", "+00:00"))
        current_since = ts_dt.astimezone().replace(tzinfo=None).strftime("%H:%M:%S")

    # ── Print ────────────────────────────────────────────────────────────────
    print()
    print("=" * W)
    print(f"  {model} Door Sensor — Fridge — Last 24 Hours".center(W))
    print(f"  Serial: {serial}   Org: {org_name}".center(W))
    print("=" * W)
    print(f"  {'Time':<21}  {'Transition':<20}  Duration in prev state")
    print("  " + "-" * (W - 2))

    if not transitions:
        print("  No transitions recorded in the last 24 hours.")
    else:
        for t in transitions:
            ts_dt    = datetime.fromisoformat(t["ts"].replace("Z", "+00:00"))
            ts_local = ts_dt.astimezone().replace(tzinfo=None)
            time_str = ts_local.strftime("%Y-%m-%d %H:%M:%S")
            arrow    = "OPEN  →  CLOSED" if t["from"] else "CLOSED  →  OPEN "
            dur_str  = fmt_duration(t["duration"])
            print(f"  {time_str:<21}  {arrow:<20}  {dur_str}")

    print("  " + "-" * (W - 2))
    print(f"  {len(transitions)} transition(s) in the last 24 hours")
    if current_state:
        print(f"  Current state : {current_state}  (since {current_since})")
    print("=" * W)
    print(f"  Fetched: {now_str}".center(W))
    print("=" * W)
    print()


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    serial, model, org_id, org_name = discover_sensor(MT20_MAC)

    if not serial:
        print("ERROR: MT20 sensor not found. Check MT20_MAC and API key.")
        sys.exit(1)

    print(f"Found: {model}  serial={serial}  org={org_name}")

    print("Fetching 24-hour door readings...")
    try:
        readings = get_door_readings_24h(org_id, serial)
    except requests.HTTPError as e:
        print(f"ERROR fetching door readings: {e}")
        sys.exit(1)

    if not readings:
        print("No door readings returned for the last 24 hours.")
        sys.exit(0)

    print(f"  {len(readings)} reading(s) fetched.")
    display_transitions(serial, model, org_name, readings)


if __name__ == "__main__":
    main()