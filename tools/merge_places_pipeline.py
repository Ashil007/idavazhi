"""
Fetch POIs from Overture Maps (Option 2) and OpenStreetMap (Option 3),
merge common coordinates, deduplicate, and export both to a spreadsheet (CSV)
and the Vazhi places database.
"""

import csv
import math
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import overturemaps
import shapely.wkb

try:
    import osmium
    HAS_OSMIUM = True
except ImportError:
    HAS_OSMIUM = False

# Bounding box for Kochi / Ernakulam & surrounding areas
# (min_lon, min_lat, max_lon, max_lat)
BBOX = (76.18, 9.88, 76.42, 10.12)
SAME_METRES = 35.0  # Threshold to merge duplicate entities

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0)**2
    return 2.0 * R * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

def norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())

def map_category(cat_str):
    c = str(cat_str or "").lower()
    if any(k in c for k in ["food", "restaurant", "eatery", "cafe", "bakery", "dining", "bar"]):
        return "food"
    if any(k in c for k in ["clinic", "hospital", "health", "dental", "medical", "doctor", "pharmacy"]):
        return "health"
    if any(k in c for k in ["school", "college", "university", "education", "learning", "institute"]):
        return "school"
    if any(k in c for k in ["office", "corporate", "firm", "agency", "bank", "financial", "consult"]):
        return "office"
    if any(k in c for k in ["worship", "temple", "church", "mosque", "masjid"]):
        return "worship"
    if any(k in c for k in ["shop", "store", "market", "retail", "service", "distributor", "commercial"]):
        return "shop"
    return "other"

def fetch_overture(bbox):
    print(f"1. Fetching Overture Maps POIs for bbox {bbox}...")
    reader = overturemaps.record_batch_reader("place", bbox=bbox)
    places = []
    total = 0
    for batch in reader:
        for row in batch.to_pylist():
            total += 1
            name_obj = row.get("names") or {}
            name = name_obj.get("primary") or name_obj.get("common")
            if not name or len(name.strip()) < 2:
                continue
            name = name.strip()
            geom_raw = row.get("geometry")
            if not geom_raw:
                continue
            geom = shapely.wkb.loads(geom_raw)
            lat, lon = geom.y, geom.x
            cat = row.get("basic_category") or (row.get("taxonomy") or {}).get("primary") or "place"
            kind = map_category(cat)
            
            # Extract address / locality if available
            addrs = row.get("addresses") or []
            addr_str = addrs[0].get("freeform") if addrs else ""
            
            places.append({
                "name": name,
                "norm": norm(name),
                "kind": kind,
                "category": cat,
                "lat": lat,
                "lon": lon,
                "address": addr_str,
                "source": "overture"
            })
    print(f"   Retrieved {len(places)} valid places from Overture (total records checked: {total}).")
    return places

def fetch_osm_pbf(bbox):
    print("2. Extracting OpenStreetMap POIs & Bus Stops from local Kerala OSM data...")
    pbf_path = os.path.join(ROOT, ".cache", "osm", "kerala-latest.osm.pbf")
    if not HAS_OSMIUM or not os.path.exists(pbf_path):
        print("   Local OSM PBF not available or osmium not installed, skipping PBF extract.")
        return []
    
    min_lon, min_lat, max_lon, max_lat = bbox
    places = []

    class POIHandler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.count = 0

        def check(self, tags, lat, lon):
            if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                return
            name = tags.get("name") or tags.get("name:en")
            if not name:
                return
            t = {k: v for k, v in tags}
            kind = "other"
            cat = "osm_place"
            if t.get("highway") == "bus_stop":
                kind = "other"
                cat = "bus_stop"
            elif t.get("shop"):
                kind = "shop"
                cat = t.get("shop")
            elif t.get("amenity"):
                kind = map_category(t.get("amenity"))
                cat = t.get("amenity")
            elif t.get("office"):
                kind = "office"
                cat = t.get("office")
            else:
                return
            
            places.append({
                "name": name.strip(),
                "norm": norm(name),
                "kind": kind,
                "category": cat,
                "lat": lat,
                "lon": lon,
                "address": tags.get("addr:street") or "",
                "source": "osm"
            })
            self.count += 1

        def node(self, n):
            if n.tags:
                self.check(n.tags, n.location.lat, n.location.lon)

    handler = POIHandler()
    handler.apply_file(pbf_path, locations=True)
    print(f"   Extracted {len(places)} POIs and bus stops from OpenStreetMap.")
    return places

def merge_and_deduplicate(all_places):
    print("3. Merging common coordinates and deduplicating...")
    # Spatial hashing: cell size ~ 100m
    grid = {}
    merged = []
    
    for p in all_places:
        cell_y = int(p["lat"] * 1000)
        cell_x = int(p["lon"] * 1000)
        
        found_match = False
        # Check neighboring cells
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                cell_key = (cell_y + dy, cell_x + dx)
                for existing in grid.get(cell_key, []):
                    # Check physical distance
                    dist = haversine(p["lat"], p["lon"], existing["lat"], existing["lon"])
                    if dist <= SAME_METRES:
                        # Check name similarity (exact norm or one contains the other)
                        n1, n2 = p["norm"], existing["norm"]
                        if n1 and n2 and (n1 == n2 or n1 in n2 or n2 in n1):
                            found_match = True
                            # Merge: prefer longer name, preserve richer address, tag merged source
                            if len(p["name"]) > len(existing["name"]):
                                existing["name"] = p["name"]
                                existing["norm"] = p["norm"]
                            if not existing.get("address") and p.get("address"):
                                existing["address"] = p.get("address")
                            if p["source"] not in existing["source"]:
                                existing["source"] += "+" + p["source"]
                            break
                if found_match:
                    break
        
        if not found_match:
            grid.setdefault((cell_y, cell_x), []).append(p)
            merged.append(p)
            
    print(f"   Total before merge: {len(all_places)}, Total after coordinate & name merge: {len(merged)}")
    return merged

def export_csv(places, csv_path):
    print(f"4. Writing spreadsheet export to {csv_path}...")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Category", "Vazhi_Kind", "Latitude", "Longitude", "Address", "Source"])
        for p in places:
            writer.writerow([
                p["name"],
                p.get("category", ""),
                p.get("kind", "other"),
                f"{p['lat']:.6f}",
                f"{p['lon']:.6f}",
                p.get("address", ""),
                p.get("source", "")
            ])
    print(f"   Successfully wrote {len(places)} places to CSV.")

def import_to_vazhi_db(places, db_path):
    print(f"5. Importing into Vazhi database {db_path}...")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    db = sqlite3.connect(db_path)
    cur = db.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS places (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, norm TEXT NOT NULL, kind TEXT NOT NULL,
        lat REAL NOT NULL, lon REAL NOT NULL, device TEXT NOT NULL, created REAL NOT NULL)""")
    
    # Read existing entries so we do not duplicate or delete anything
    existing_norms = set(r[0] for r in cur.execute("SELECT norm FROM places").fetchall())
    print(f"   Existing database records: {len(existing_norms)}")
    
    to_insert = []
    now = time.time()
    for p in places:
        n = p["norm"]
        if n in existing_norms:
            continue
        existing_norms.add(n)
        to_insert.append((
            p["name"],
            n,
            p.get("kind", "other"),
            p["lat"],
            p["lon"],
            "batch_import",
            now
        ))
    
    if to_insert:
        cur.executemany(
            "INSERT INTO places (name, norm, kind, lat, lon, device, created) VALUES (?,?,?,?,?,?,?)",
            to_insert
        )
        db.commit()
    
    # Ensure index on name and norm for lightning-fast search
    cur.execute("CREATE INDEX IF NOT EXISTS idx_places_norm ON places (norm)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_places_name ON places (name)")
    db.commit()
    
    total_count = cur.execute("SELECT count(*) FROM places").fetchone()[0]
    db.close()
    print(f"   Added {len(to_insert)} new unique places. Total database places: {total_count}")

def main():
    print("=== Vazhi POI Harvester & Coordinate Merger ===")
    overture_places = fetch_overture(BBOX)
    osm_places = fetch_osm_pbf(BBOX)
    combined = overture_places + osm_places
    
    merged = merge_and_deduplicate(combined)
    
    # Export CSV spreadsheet
    csv_path = os.path.join(ROOT, "data", "merged_places_kochi.csv")
    export_csv(merged, csv_path)
    
    # Import into places.db
    db_path = os.path.join(ROOT, "data", "places.db")
    import_to_vazhi_db(merged, db_path)
    print("=== Complete! ===")

if __name__ == "__main__":
    main()
