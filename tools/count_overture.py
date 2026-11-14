import overturemaps
import shapely.wkb

bbox = (76.18, 9.88, 76.42, 10.12)
print("Querying Overture for Kochi/Ernakulam bbox:", bbox)
reader = overturemaps.record_batch_reader("place", bbox=bbox)
count = 0
for batch in reader:
    count += len(batch)

print("Total Overture places in Kochi/Ernakulam:", count)
