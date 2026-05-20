import csv 

path = "/Users/racheltan/2026 Projects/UNI/NPSC3000/EcoFeedback/sample_energy_data.csv"

with open(path, "r") as f:
    reader = csv.reader(f)
    headers = next(reader)
    timestamps = headers[3:]  # everything after entity_id, type, unit
    
    print(f"Total devices: sum of rows")
    print(f"Time range: {timestamps[0]}  →  {timestamps[-1]}")
    print(f"Interval: 5 minutes")
    print(f"Total readings per device: {len(timestamps)}")
    print()
    print("=" * 60)
    
    for i, row in enumerate(reader):
        if len(row) < 4:
            continue
            
        entity_id = row[0]
        unit = row[2]
        values = []
        
        for v in row[3:]:
            try:
                values.append(float(v))
            except:
                pass
        
        if not values:
            continue
        
        # Check if cumulative or instantaneous
        diffs = [values[i+1] - values[i] for i in range(len(values)-1)]
        always_increasing = all(d >= -0.001 for d in diffs)
        big_jumps = any(abs(d) > 1.0 for d in diffs)
        
        kind = "CUMULATIVE (odometer)" if always_increasing else "INSTANTANEOUS (live watts)"
        
        print(f"Device:  {entity_id}")
        print(f"Unit:    {unit}")
        print(f"Type:    {kind}")
        print(f"Min:     {min(values):.4f}")
        print(f"Max:     {max(values):.4f}")
        print(f"Latest:  {values[-1]:.4f}")
        if always_increasing:
            print(f"Total used this period: {values[-1] - values[0]:.4f} {unit}")
        print()