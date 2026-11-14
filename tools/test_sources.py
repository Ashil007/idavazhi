import duckdb

con = duckdb.connect()
print("DuckDB version:", duckdb.__version__)
con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
print("Extensions loaded successfully.")
