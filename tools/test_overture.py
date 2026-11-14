import duckdb

con = duckdb.connect()
con.execute("""
INSTALL httpfs; LOAD httpfs;
INSTALL spatial; LOAD spatial;
SET s3_region='us-west-2';
""")

# Test querying Overture places
# Bounding box: Kochi / Ernakulam [min_lon, min_lat, max_lon, max_lat] = [76.20, 9.88, 76.45, 10.15]
query = """
SELECT 
    names.primary AS name,
    categories.primary AS category,
    ST_Y(ST_GeomFromWKB(geometry)) AS lat,
    ST_X(ST_GeomFromWKB(geometry)) AS lon
FROM read_parquet('s3://overturemaps-us-west-2/release/2024-10-23.0/theme=places/type=place/*', filename=true, hive_partitioning=1)
WHERE bbox.xmin BETWEEN 76.20 AND 76.45
  AND bbox.ymin BETWEEN 9.88 AND 10.15
LIMIT 10;
"""
try:
    print("Executing query...")
    df = con.execute(query).fetchdf()
    print("Found rows:")
    print(df)
except Exception as e:
    print("Error:", e)
