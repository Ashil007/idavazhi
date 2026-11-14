import overturemaps
import shapely.wkb

bbox = (76.28, 10.02, 76.35, 10.05)  # Thrikkakara / Kakkanad
reader = overturemaps.record_batch_reader("place", bbox=bbox)
categories = set()
samples = []
for batch in reader:
    for row in batch.to_pylist():
        cat = row.get("basic_category") or (row.get("taxonomy") or {}).get("primary")
        if cat:
            categories.add(str(cat))
        if len(samples) < 5:
            samples.append({
                "name": (row.get("names") or {}).get("primary"),
                "cat": cat,
                "addr": row.get("addresses"),
                "geom": shapely.wkb.loads(row.get("geometry"))
            })
    break

print("Categories count:", len(categories))
print("Sample categories:", list(categories)[:15])
for s in samples:
    print(f"{s['name']} | {s['cat']} | {s['geom'].y:.5f},{s['geom'].x:.5f}")
