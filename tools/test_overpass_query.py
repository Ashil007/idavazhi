import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from transitnav import overpass

ql = """
[out:json][timeout:30];
(
  node["shop"](10.00, 76.28, 10.05, 76.36);
  node["highway"="bus_stop"](10.00, 76.28, 10.05, 76.36);
  node["amenity"](10.00, 76.28, 10.05, 76.36);
  node["office"](10.00, 76.28, 10.05, 76.36);
);
out body;
"""

print("Querying Overpass...")
try:
    res = overpass.query(ql, timeout=35, use_cache=False)
    elements = res.get("elements", [])
    print(f"Overpass returned {len(elements)} elements.")
    named = [e for e in elements if e.get("tags", {}).get("name")]
    print(f"Named elements: {len(named)}")
    if named:
        print("Sample:", named[0].get("tags", {}).get("name"), named[0].get("lat"), named[0].get("lon"))
except Exception as e:
    print("Error querying Overpass:", e)
