# Patch SQLite connections to enable WAL mode, busy_timeout=5000, and synchronous=NORMAL

files_to_patch = [
    ('transitnav/timings.py', 'data/timings.db'),
    ('transitnav/userplaces.py', 'data/places.db'),
    ('transitnav/stopnames.py', 'data/stops.db'),
    ('transitnav/feedback.py', 'data/feedback.db')
]

for file_path, db_name in files_to_patch:
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find db = sqlite3.connect(DB_PATH, timeout=10)
    target = 'db = sqlite3.connect(DB_PATH, timeout=10)'
    repl = ('db = sqlite3.connect(DB_PATH, timeout=15)\n'
            '    db.execute("PRAGMA journal_mode = WAL;")\n'
            '    db.execute("PRAGMA busy_timeout = 5000;")\n'
            '    db.execute("PRAGMA synchronous = NORMAL;")')

    if target in content:
        content = content.replace(target, repl)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Successfully enabled WAL in {file_path}")
    elif 'PRAGMA journal_mode = WAL' in content:
        print(f"WAL already enabled in {file_path}")
    else:
        print(f"Target pattern not found in {file_path}")
