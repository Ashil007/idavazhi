"""Manual CLI trigger to back up all Vazhi SQLite databases."""

import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from transitnav import backup

if __name__ == "__main__":
    print("Starting hot backup of Vazhi databases...")
    backed_up = backup.backup_all(log=print)
    if backed_up:
        print(f"\nAll done! Successfully created {len(backed_up)} database backups in data/backups/")
    else:
        print("\nNo active databases found to back up.")
