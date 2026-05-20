import csv
import json
import random
from datetime import datetime, timedelta

# ── file paths ──────────────────────────────────────────────
DATA_PATH    = "/Users/racheltan/2026 Projects/UNI/NPSC3000/EcoFeedback/sample_energy_data.csv"
ROOMS_PATH   = "/Users/racheltan/2026 Projects/UNI/NPSC3000/EcoFeedback/rooms.json"
MAPPING_PATH = "/Users/racheltan/2026 Projects/UNI/NPSC3000/EcoFeedback/device_mapping.json"
OUTPUT_PATH  = "/Users/racheltan/2026 Projects/UNI/NPSC3000/EcoFeedback/viz_data.json"

# ── load files ───────────────────────────────────────────────
with open(ROOMS_PATH)   as f: rooms   = json.load(f)
with open(MAPPING_PATH) as f: mapping = json.load(f)

# ── load CSV ─────────────────────────────────────────────────
with open(DATA_PATH) as f:
    reader    = csv.reader(f)
    headers   = next(reader)
    timestamps = headers[3:]
    rows      = list(reader)

# ── simulated occupancy profile ──────────────────────────────
# returns 0.0 - 1.0 based on time of day
def simulate_occupancy(room_id, hour, minute):
    # outside office hours - empty
    if hour < 8 or hour >= 18:
        return 0.0

    # lunch dip
    if hour == 12 or hour == 13:
        base = 0.2
    # morning and afternoon peak
    elif 9 <= hour <= 11 or 14 <= hour <= 16:
        base = 0.85
    else:
        base = 0.5

    # per room personality
    modifiers = {
        "room_kitchen":           0.3 if (hour == 9 or hour == 12 or hour == 15) else 0.05,
        "room_main_meeting_room": 0.9 if hour in [10, 14] else 0.1,
        "room_demo_area":         0.7 if 10 <= hour <= 16 else 0.1,
        "room_intern_office_a":   base,
        "room_intern_office_b":   base,
        "room_office":            base,
        "room_crux":              0.8 if 9 <= hour <= 17 else 0.0,
        "room_hydra":             0.7 if 9 <= hour <= 17 else 0.0,
        "room_pyxis":             0.7 if 9 <= hour <= 17 else 0.0,
        "room_hallway_a":         0.0,
        "room_hallways_b":        0.0,
    }

    occ = modifiers.get(room_id, base)
    # add small random noise so it doesn't look static
    occ += random.uniform(-0.05, 0.05)
    return round(max(0.0, min(1.0, occ)), 2)

# ── build per-timestamp per-room energy totals ────────────────
# structure: { timestamp: { room_id: kwh_total } }
energy_by_time = {}

for ts in timestamps:
    energy_by_time[ts] = {room["id"]: 0.0 for room in rooms}

for row in rows:
    if len(row) < 4:
        continue
    entity_id = row[0]
    room_id   = mapping.get(entity_id)
    if not room_id:
        continue  # unmapped device, skip

    for i, ts in enumerate(timestamps):
        try:
            val = float(row[3 + i])
        except:
            val = 0.0
        if ts in energy_by_time and room_id in energy_by_time[ts]:
            energy_by_time[ts][room_id] += round(val, 4)

# ── build final output frames ─────────────────────────────────
frames = []

for ts in timestamps:
    dt   = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    hour = dt.hour
    minute = dt.minute

    room_data = []
    for room in rooms:
        rid = room["id"]
        energy = energy_by_time[ts].get(rid, 0.0)
        occ    = simulate_occupancy(rid, hour, minute)

        room_data.append({
            "id":        rid,
            "label":     room["label"],
            "centreX":   room["centreX"],
            "centreY":   room["centreY"],
            "minX":      room["minX"],
            "maxX":      room["maxX"],
            "minY":      room["minY"],
            "maxY":      room["maxY"],
            "energy":    energy,
            "occupancy": occ,
        })

    frames.append({
        "timestamp": ts,
        "hour":      hour,
        "minute":    minute,
        "rooms":     room_data,
    })

# ── write output ──────────────────────────────────────────────
with open(OUTPUT_PATH, "w") as f:
    json.dump(frames, f, indent=2)

print(f"Done — {len(frames)} frames, {len(rooms)} rooms")
print(f"Saved to: {OUTPUT_PATH}")