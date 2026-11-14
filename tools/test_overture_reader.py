import overturemaps
import shapely.wkb
import json

bbox = (76.28, 10.00, 76.36, 10.05)  # Focused on Thrikkakara / Kakkanad / Edappally
print("Connecting to Overture Maps for bbox:", bbox)
reader = overturemaps.record_batch_reader("place", bbox=bbox)
count = 0
for batch in reader:
    count += len(batch)
    print(f"Batch size: {len(batch)}, total so far: {count}")
    pylist = batch.to_pylist()
    if pylist:
        sample = pylist[0]
        print("Sample keys:", list(sample.keys()))
        print("Sample name:", sample.get("names"))
        print("Sample categories:", sample.get("categories"))
        geom = shapely.wkb.loads(sample.get("geometry"))
        print("Sample coords:", geom.x, geom.y)
    break
print("Done test.")
