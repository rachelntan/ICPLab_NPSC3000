#!/usr/bin/env python3
"""
MV12W_people_updated.py
Terminal display of people count over a given date range from a Meraki
MV12W camera, plus current MT10 temperature and humidity.

Requirements: pip install requests
MV Sense must be enabled on the camera in the Meraki dashboard.

Usage:
    # Default: last 24 hours (same as before)
    python MV12W_people_updated.py

    # Explicit date range (local time, "YYYY-MM-DD" or "YYYY-MM-DD HH:MM")
    python MV12W_people_updated.py --start 2026-06-01 --end 2026-06-08

    # Look back N hours from now (or from --end if given)
    python MV12W_people_updated.py --hours 72

    # Custom output CSV
    python MV12W_people_updated.py --start 2026-06-01 --end 2026-06-08 --out june_week1.csv

Use: 
python MV12W_people_updated.py --start 2026-07-11 --end 2026-08-01
"""

import sys
import argparse
import requests
from datetime import datetime, timedelta

# ── Config ─────────────────────────────────────────────────────────────────
MERAKI_API_KEY   = "852c64663d82a8db0dc40202bd11200986a3d8e1"
MERAKI_MV12W_MAC = "34:56:fe:a3:a7:d6"
MERAKI_MT10_MAC  = "a8:46:9d:ff:ca:ee"

MERAKI_BASE_URL = "https://api.meraki.com/api/v1"
MERAKI_HEADERS  = {
    "X-Cisco-Meraki-API-Key": MERAKI_API_KEY,
    "Content-Type": "application/json",
    "Accept":        "application/json",
}

# Meraki hard limit per zone history call is 50400 s; use 12 h chunks to stay safe
CHUNK_SECONDS = 43200   # 12 hours

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


def discover_devices(mv_mac: str, mt10_mac: str):
    """
    Walk orgs → devices in a single pass, returning:
      (mv_serial, mv_model, mt10_serial, org_id, org_name)
    Any unfound device is returned as None.
    """
    mv_target   = normalise_mac(mv_mac)
    mt10_target = normalise_mac(mt10_mac)
    mv_serial = mv_model = mt10_serial = org_id = org_name = None

    print("Querying Meraki organisations...")
    orgs = meraki_get("/organizations")
    for org in orgs:
        try:
            devices = meraki_get(f"/organizations/{org['id']}/devices")
        except Exception:
            continue
        for dev in devices:
            dev_mac = normalise_mac(dev.get("mac", ""))
            if dev_mac == mv_target and mv_serial is None:
                mv_serial = dev["serial"]
                mv_model  = dev.get("model", "MV")
                org_id    = org["id"]
                org_name  = org["name"]
            if dev_mac == mt10_target and mt10_serial is None:
                mt10_serial = dev["serial"]
                if org_id is None:
                    org_id   = org["id"]
                    org_name = org["name"]
        if mv_serial and mt10_serial:
            break   # found both, no need to keep scanning

    return mv_serial, mv_model, mt10_serial, org_id, org_name


# ── MT10 sensor ─────────────────────────────────────────────────────────────

def get_mt10_readings(org_id: str, mt10_serial: str):
    """Return (temperature_celsius, humidity_pct) from the MT10's latest reading."""
    data = meraki_get(
        f"/organizations/{org_id}/sensor/readings/latest",
        params={"serials[]": mt10_serial},
    )
    temp = humidity = None
    for sensor in data:
        if sensor.get("serial") == mt10_serial:
            for reading in sensor.get("readings", []):
                metric = reading.get("metric")
                if metric == "temperature":
                    temp = reading["temperature"]["celsius"]
                elif metric == "humidity":
                    humidity = reading["humidity"]["relativePercentage"]
    return temp, humidity


# ── MV12W zone analytics ────────────────────────────────────────────────────

def list_zones(serial: str):
    return meraki_get(f"/devices/{serial}/camera/analytics/zones")


def zone_id_of(z: dict):
    """Handle Meraki's inconsistent key naming across firmware versions."""
    return z.get("zoneId") or z.get("id") or z.get("zone_id")


def chunk_ranges(t0: int, t1: int, max_span: int = CHUNK_SECONDS):
    """Split [t0, t1) into a list of (a, b) tuples each <= max_span seconds."""
    ranges = []
    cur = t0
    while cur < t1:
        nxt = min(cur + max_span, t1)
        ranges.append((cur, nxt))
        cur = nxt
    return ranges


def get_zone_history_range(serial: str, zone_id, t0: int, t1: int):
    """
    Fetch zone history for an arbitrary [t0, t1) range, transparently
    chunking into <=12h API calls (Meraki caps a single request at 50400s).
    """
    ranges = chunk_ranges(t0, t1)
    all_rows = []
    for i, (a, b) in enumerate(ranges, 1):
        a_str = datetime.fromtimestamp(a).strftime("%Y-%m-%d %H:%M")
        b_str = datetime.fromtimestamp(b).strftime("%Y-%m-%d %H:%M")
        print(f"  Fetching chunk {i}/{len(ranges)}: {a_str} → {b_str}")
        part = meraki_get(
            f"/devices/{serial}/camera/analytics/zones/{zone_id}/history",
            params={"t0": a, "t1": b},
        ) or []
        all_rows.extend(part)
    return all_rows


# ── Rendering ───────────────────────────────────────────────────────────────

W = 72
BAR_WIDTH = 28

def bar(value: float, max_value: float) -> str:
    if max_value <= 0:
        return "░" * BAR_WIDTH
    filled = round((value / max_value) * BAR_WIDTH)
    return "█" * max(0, min(filled, BAR_WIDTH)) + "░" * (BAR_WIDTH - max(0, min(filled, BAR_WIDTH)))


def print_sensor_header(mt10_serial, temp, humidity, org_name):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print()
    print("=" * W)
    print("  MT10 Environmental Sensor".center(W))
    print(f"  Org: {org_name}   Serial: {mt10_serial}".center(W))
    print("=" * W)
    temp_str = f"{temp:.1f} °C"    if temp     is not None else "N/A"
    hum_str  = f"{humidity:.0f} %RH" if humidity is not None else "N/A"
    print(f"  Temperature : {temp_str}")
    print(f"  Humidity    : {hum_str}")
    print(f"  Fetched     : {ts}")
    print("=" * W)
    print()


def display_people(serial: str, org_name: str, model: str, zone_label: str,
                    history: list, range_label: str):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * W)
    print(f"  {model} People Count — {range_label}  [{zone_label}]".center(W))
    print(f"  Serial: {serial}   Org: {org_name}".center(W))
    print("=" * W)
    print(f"  {'Period':<16}  {'Entrances':>10}  {'Avg Occ':>8}  Chart")
    print("  " + "-" * (W - 2))

    max_entrances = max((r.get("entrances", 0) for r in history), default=0) or 1
    total_entrances = 0

    for row in history:
        entrances = row.get("entrances", 0)
        avg_count = row.get("averageCount", 0.0)
        total_entrances += entrances

        if entrances == 0 and avg_count == 0.0:
            continue

        start_ts = row.get("startTs", "")
        end_ts   = row.get("endTs", "")
        start_dt = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
        end_dt   = (datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
                    if end_ts else start_dt + timedelta(hours=1))

        start_local = start_dt.astimezone().replace(tzinfo=None)
        end_local   = end_dt.astimezone().replace(tzinfo=None)

        period = f"{start_local.strftime('%d/%m %H:%M')}–{end_local.strftime('%H:%M')}"
        print(f"  {period:<16}  {entrances:>10}  {avg_count:>7.1f}  {bar(entrances, max_entrances)}")

    print("  " + "-" * (W - 2))
    print(f"  {'TOTAL':<16}  {total_entrances:>10}")
    print("=" * W)
    print(f"  Fetched: {now_str}".center(W))
    print("=" * W)
    print()

# ── Logging ────────────────────────────────────────────────────────────────────

import csv, os

def log_to_csv(history, temp, humidity, filename="occupancy_log.csv"):
    fieldnames = ["timestamp_start", "timestamp_end", "entrances", "avg_occupancy", "temp_c", "humidity_pct"]
    file_exists = os.path.isfile(filename)

    with open(filename, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for row in history:
            if row.get("entrances", 0) == 0 and row.get("averageCount", 0) == 0:
                continue
            writer.writerow({
                "timestamp_start": row.get("startTs", ""),
                "timestamp_end":   row.get("endTs", ""),
                "entrances":       row.get("entrances", 0),
                "avg_occupancy":   row.get("averageCount", 0.0),
                "temp_c":          temp,
                "humidity_pct":    humidity,
            })

# ── Date range parsing ──────────────────────────────────────────────────────

def parse_local_datetime(s: str) -> datetime:
    """Accepts 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM[:SS]' in local time."""
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Unrecognised date/time: {s!r}. Use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'."
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Pull Meraki MV12W people count + MT10 readings for a date range."
    )
    parser.add_argument("--start", type=parse_local_datetime,
                         help="Range start, local time, e.g. 2026-06-01 or '2026-06-01 08:00'")
    parser.add_argument("--end", type=parse_local_datetime,
                         help="Range end, local time. Defaults to now.")
    parser.add_argument("--hours", type=float, default=24,
                         help="If --start not given, look back this many hours from --end (default 24, i.e. old behaviour)")
    parser.add_argument("--out", default="occupancy_log.csv",
                         help="CSV output filename (default occupancy_log.csv)")
    args = parser.parse_args()

    end_dt = args.end or datetime.now()
    start_dt = args.start or (end_dt - timedelta(hours=args.hours))

    if start_dt >= end_dt:
        parser.error("--start must be before --end")

    return start_dt, end_dt, args.out


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    start_dt, end_dt, out_csv = parse_args()
    t0 = int(start_dt.timestamp())
    t1 = int(end_dt.timestamp())
    range_label = f"{start_dt.strftime('%Y-%m-%d %H:%M')} → {end_dt.strftime('%Y-%m-%d %H:%M')}"

    temp = humidity = None  # populated below if MT10 is found

    # ── Discover devices ────────────────────────────────────────────────────
    mv_serial, mv_model, mt10_serial, org_id, org_name = discover_devices(
        MERAKI_MV12W_MAC, MERAKI_MT10_MAC
    )

    if not mv_serial:
        print("ERROR: MV12W camera not found. Check MERAKI_MV12W_MAC and API key.")
        sys.exit(1)

    print(f"Found: {mv_model}  serial={mv_serial}  org={org_name}")
    if mt10_serial:
        print(f"Found: MT10   serial={mt10_serial}")
    else:
        print("WARNING: MT10 not found — sensor readings will be skipped.")

    # ── MT10 sensor readings (current snapshot only — see note below) ──────
    if mt10_serial and org_id:
        print("Fetching MT10 sensor readings...")
        try:
            temp, humidity = get_mt10_readings(org_id, mt10_serial)
            print_sensor_header(mt10_serial, temp, humidity, org_name)
        except requests.HTTPError as e:
            print(f"WARNING: MT10 fetch failed ({e}) — continuing.\n")

    # ── Zone discovery ──────────────────────────────────────────────────────
    print("Listing MV12W analytics zones...")
    try:
        zones = list_zones(mv_serial)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"ERROR {status} from zones endpoint: {e}")
        if status == 400:
            print("  → Ensure MV Sense is licensed and enabled on this camera.")
        sys.exit(1)

    if not zones:
        print("No analytics zones returned — MV Sense may not be active.")
        sys.exit(1)

    # Prefer the whole-frame occupancy zone, fall back to first zone
    zone = next((z for z in zones if z.get("type") == "occupancy"), zones[0])
    zone_id    = zone_id_of(zone)
    zone_label = zone.get("label") or zone.get("name") or zone.get("type") or str(zone_id)
    print(f"  Using zone {zone_id} ({zone_label})")

    if zone_id is None:
        print(f"ERROR: Cannot determine zone ID: {zone}")
        sys.exit(1)

    # ── Zone history over requested range ───────────────────────────────────
    n_chunks = len(chunk_ranges(t0, t1))
    print(f"Fetching zone analytics for {range_label} ({n_chunks} × ≤12h calls)...")
    try:
        history = get_zone_history_range(mv_serial, zone_id, t0, t1)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"ERROR {status} from zone history endpoint: {e}")
        sys.exit(1)

    if not history:
        print(f"No data returned for {range_label}.")
        sys.exit(0)

    display_people(mv_serial, org_name, mv_model, zone_label, history, range_label)
    log_to_csv(history, temp, humidity, filename=out_csv)
    print(f"Logged to {out_csv}")

if __name__ == "__main__":
    main()