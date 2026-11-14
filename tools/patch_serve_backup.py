with open('serve.py', 'r', encoding='utf-8') as f:
    text = f.read()

target = """    # The place-name index for the destination search, rebuilt whenever the
    # map tiles are newer than it (about a minute, in the background).
    if not args.worker:
        from transitnav import places
        places.build_in_background(log=lambda m: print(m, flush=True))"""

replacement = """    # The place-name index for the destination search, rebuilt whenever the
    # map tiles are newer than it (about a minute, in the background).
    if not args.worker:
        from transitnav import places
        places.build_in_background(log=lambda m: print(m, flush=True))

    # Automated daily lock-free hot backup of live SQLite databases.
    if not args.worker:
        from transitnav import backup
        backup.start_daily_backup()"""

if target in text:
    text = text.replace(target, replacement)
    with open('serve.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print("serve.py updated with automated daily backup")
else:
    print("Target not found in serve.py")
