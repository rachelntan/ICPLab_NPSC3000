"""
Home Assistant granular history downloader
---------------------------------------------------
Downloads raw recorder history (NOT long-term statistics) for a list of
entities, one day at a time, across a date range. This preserves the
granular resolution you see when you manually shrink the History /
Energy dashboard timeframe to a single day — the default "download"
button instead pulls pre-aggregated long-term statistics (hourly/daily),
which is why it looks coarser.

Works for both energy (kWh) and power (W) entities — just list whichever
entity_ids you want in ENTITY_IDS below.

Output: one wide-format CSV per day, named ENERGY_DDMMYYYY.csv (or
POWER_DDMMYYYY.csv if you change OUTPUT_PREFIX), matching your existing
pipeline's expected shape (entity_id, type, unit, then one column per
ISO timestamp).

Requirements: pip install requests pandas
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------
# CONFIG — edit these
# ---------------------------------------------------------------------

# curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI3MGM3MDA1Y2MxMWU0YjA4ODkzODliNDc5YTA2MmU0NiIsImlhdCI6MTc4NDcwMzY1MSwiZXhwIjoyMTAwMDYzNjUxfQ.Xk_AeprS01p11Mizj6DCWUoZu4ZdicYXD5fEvirvJDA" \
#     "http://homeassistant.taila277ca.ts.net:8123/api/history/period/2026-07-20T00:00:00Z?end_time=2026-07-21T00:00:00Z&filter_entity_id=sensor.heating_water_current_power"

HA_URL = "http://homeassistant.taila277ca.ts.net:8123"     # your HA base URL
HA_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI3MGM3MDA1Y2MxMWU0YjA4ODkzODliNDc5YTA2MmU0NiIsImlhdCI6MTc4NDcwMzY1MSwiZXhwIjoyMTAwMDYzNjUxfQ.Xk_AeprS01p11Mizj6DCWUoZu4ZdicYXD5fEvirvJDA"    # Profile > Security > Long-Lived Access Tokens

# List the entity_ids you want (power entities, energy entities, or both)
ENTITY_IDS = [
    "sensor.heating_water_current_power",   # <-- replace with your exact power entity_id
]

# first download: "2026-07-11" - "2026-07-21"
START_DATE = "2026-07-22"
END_DATE = "2026-08-01"
TZ = timezone.utc            # HA history API expects/returns UTC timestamps

OUTPUT_DIR = Path("HeatingWater")   # matches your existing folder convention
OUTPUT_PREFIX = "POWER"         # produces POWER_DDMMYYYY.csv; use "ENERGY" for energy entities

REQUEST_DELAY_SEC = 1.0    # be polite to the recorder DB between days
MAX_RETRIES = 3

# ---------------------------------------------------------------------


def daterange(start: str, end: str):
    """Yield each day (as a date object) from start to end, inclusive."""
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    cur = d0
    while cur <= d1:
        yield cur
        cur += timedelta(days=1)


def fetch_day_history(day, entity_ids):
    """
    Call HA's raw history endpoint for a single day window.
    Returns the parsed JSON: a list of lists of state-change dicts,
    one inner list per entity_id (order matches filter_entity_id).
    """
    start_dt = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
    end_dt = start_dt + timedelta(days=1)

    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()

    url = f"{HA_URL}/api/history/period/{start_iso}"
    params = {
        "end_time": end_iso,
        "filter_entity_id": ",".join(entity_ids),
        "minimal_response": "false",          # keep full state dicts (need attributes/unit)
        "no_attributes": "false",
        "significant_changes_only": "false",  # get every recorded point, not just "significant" jumps
    }
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        resp = requests.get(url, headers=headers, params=params, timeout=60)
        if resp.status_code == 200:
            return resp.json()
        print(f"  [warn] {day} attempt {attempt}: HTTP {resp.status_code} — {resp.text[:200]}")
        time.sleep(2 * attempt)
    raise RuntimeError(f"Failed to fetch history for {day} after {MAX_RETRIES} attempts")


def to_wide_dataframe(raw_json, entity_ids):
    """
    Convert HA's per-entity list-of-state-changes JSON into the wide
    format your existing pipeline expects:
        entity_id | type | unit | <timestamp1> | <timestamp2> | ...
    """
    rows = []
    for entity_id, states in zip(entity_ids, raw_json):
        if not states:
            # no data logged for this entity on this day — still emit an
            # empty row so downstream code sees a consistent entity set
            rows.append({"entity_id": entity_id, "type": "sensor", "unit": None})
            continue

        unit = states[0].get("attributes", {}).get("unit_of_measurement")
        row = {"entity_id": entity_id, "type": "sensor", "unit": unit}

        for s in states:
            ts = s.get("last_changed")
            val = s.get("state")
            # skip unknown/unavailable placeholder states
            if val in (None, "unknown", "unavailable"):
                continue
            row[ts] = val
        rows.append(row)

    df = pd.DataFrame(rows)
    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_days = list(daterange(START_DATE, END_DATE))
    print(f"Downloading {len(all_days)} day(s) for {len(ENTITY_IDS)} entities...")

    for i, day in enumerate(all_days, start=1):
        print(f"[{i}/{len(all_days)}] Fetching {day} ...")
        raw = fetch_day_history(day, ENTITY_IDS)
        print("RAW RESPONSE:", raw)
        df_wide = to_wide_dataframe(raw, ENTITY_IDS)

        fname = f"{OUTPUT_PREFIX}_{day.strftime('%d%m%Y')}.csv"
        out_path = OUTPUT_DIR / fname
        df_wide.to_csv(out_path, index=False)
        print(f"  saved {out_path}  ({df_wide.shape[0]} entities, {df_wide.shape[1]-3} timestamps)")

        time.sleep(REQUEST_DELAY_SEC)

    print("Done.")


if __name__ == "__main__":
    main()