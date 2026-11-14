"""Simulation test for:
1. SQLite concurrency (Default journal mode vs WAL mode) under heavy concurrent reads and writes.
2. limits.py stability under rapid multi-threaded stress.
"""

import os
import sys
import time
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

# Add workspace to path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from transitnav import limits

TEST_DB_DEFAULT = os.path.join(ROOT, ".cache", "sim_test_default.db")
TEST_DB_WAL = os.path.join(ROOT, ".cache", "sim_test_wal.db")
os.makedirs(os.path.join(ROOT, ".cache"), exist_ok=True)


def setup_db(db_path, wal=False):
    if os.path.exists(db_path):
        try: os.remove(db_path)
        except Exception: pass
    conn = sqlite3.connect(db_path, timeout=1.0)
    if wal:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY,
            board_name TEXT,
            dep_min INTEGER,
            created REAL
        )
    """)
    for i in range(100):
        conn.execute("INSERT INTO reports (board_name, dep_min, created) VALUES (?, ?, ?)",
                     (f"Stop_{i}", i * 10, time.time()))
    conn.commit()
    conn.close()


def run_concurrency_sim(db_path, wal=False, num_workers=20, ops_per_worker=50):
    setup_db(db_path, wal=wal)
    
    lock_errors = 0
    success_ops = 0
    lock_stats = threading.Lock()

    def worker_task(worker_id):
        nonlocal lock_errors, success_ops
        is_writer = (worker_id % 2 == 0)
        
        for op_idx in range(ops_per_worker):
            try:
                # Short timeout to see if default mode blocks/times out
                timeout_s = 5.0 if wal else 0.5
                conn = sqlite3.connect(db_path, timeout=timeout_s)
                if wal:
                    conn.execute("PRAGMA busy_timeout = 5000;")
                    conn.execute("PRAGMA synchronous = NORMAL;")
                    
                if is_writer:
                    conn.execute("INSERT INTO reports (board_name, dep_min, created) VALUES (?, ?, ?)",
                                 (f"Stop_worker_{worker_id}_{op_idx}", op_idx, time.time()))
                    conn.commit()
                else:
                    cur = conn.execute("SELECT COUNT(*), AVG(dep_min) FROM reports WHERE dep_min > ?", (op_idx,))
                    cur.fetchall()
                conn.close()
                with lock_stats:
                    success_ops += 1
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    with lock_stats:
                        lock_errors += 1
                else:
                    raise

    threads = []
    start_t = time.time()
    with ThreadPoolExecutor(max_workers=num_workers) as pool:
        futures = [pool.submit(worker_task, i) for i in range(num_workers)]
        for f in futures:
            f.result()
    duration = time.time() - start_t

    return {
        "wal": wal,
        "total_ops": num_workers * ops_per_worker,
        "success_ops": success_ops,
        "lock_errors": lock_errors,
        "duration_s": round(duration, 3)
    }


def run_limits_stress_test(num_threads=30, requests_per_thread=100):
    limits.reset()
    total_calls = num_threads * requests_per_thread
    blocked_count = 0
    allowed_count = 0
    lock = threading.Lock()

    def client_task(thread_id):
        nonlocal blocked_count, allowed_count
        client_ip = f"192.168.1.{thread_id % 5}"  # 5 distinct simulated clients
        for _ in range(requests_per_thread):
            try:
                limits.hit("plan", client_ip)
                with lock: allowed_count += 1
            except limits.Limited:
                with lock: blocked_count += 1
            except Exception as e:
                print(f"CRASH / UNEXPECTED EXCEPTION: {e}")
                raise

    start_t = time.time()
    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(client_task, i) for i in range(num_threads)]
        for f in futures:
            f.result()
    duration = time.time() - start_t

    return {
        "total_calls": total_calls,
        "allowed_count": allowed_count,
        "blocked_cleanly": blocked_count,
        "crashed": False,
        "duration_s": round(duration, 3)
    }


if __name__ == "__main__":
    print("==================================================")
    print("SIMULATION 1: SQLite Concurrency Test (20 threads, 1000 ops)")
    print("==================================================")
    
    print("\n[A] Testing Default Journal Mode (DELETE/ROLLBACK)...")
    res_default = run_concurrency_sim(TEST_DB_DEFAULT, wal=False)
    print(f"  Operations: {res_default['total_ops']}")
    print(f"  Successful: {res_default['success_ops']}")
    print(f"  Database Locked Errors: {res_default['lock_errors']}")
    print(f"  Duration: {res_default['duration_s']}s")

    print("\n[B] Testing WAL Mode (Write-Ahead Logging + 5s busy_timeout)...")
    res_wal = run_concurrency_sim(TEST_DB_WAL, wal=True)
    print(f"  Operations: {res_wal['total_ops']}")
    print(f"  Successful: {res_wal['success_ops']}")
    print(f"  Database Locked Errors: {res_wal['lock_errors']}")
    print(f"  Duration: {res_wal['duration_s']}s")

    print("\n==================================================")
    print("SIMULATION 2: limits.py Stress Test (30 threads, 3000 rapid calls)")
    print("==================================================")
    res_limits = run_limits_stress_test()
    print(f"  Total API Calls: {res_limits['total_calls']}")
    print(f"  Allowed Under Quota: {res_limits['allowed_count']}")
    print(f"  Cleanly Throttled (HTTP 429): {res_limits['blocked_cleanly']}")
    print(f"  Python Crashes: {res_limits['crashed']}")
    print(f"  Duration: {res_limits['duration_s']}s")
