"""Turn the Kerala private bus schedule (the MVD's RTI reply, as JSON) into
bus times riders see in the app (data/timings.db, see transitnav/timings.py).

The JSON comes from https://github.com/amith-vp/Kerala-Private-Bus-Timing
(one file per RTO area: ernakulam.json, kottayam.json, ...). That repository
has no licence, so do not commit its files, and get the author's permission
before importing for real. Keep the downloaded files outside this repository.

The schedule has stop names but no map points, so each stop is looked up in
the app's place index (transitnav/places.py) near its RTO area. A route's
stops are picked together: each stop must be reachable from the one before
in the minutes the timetable gives, so "Medical College" lands at the one on
that bus's road. Stops with no believable match are skipped and listed, and
can be fixed by hand in an overrides CSV (name,lat,lon).

Each trip gives one time at every stop it leaves: board here, at this
minute, heading toward the next stop, for a bus going to the trip's last
stop. Times are stored as one rider's report would be, so riders can still
confirm them or flag them wrong.

    python tools/import_rti_bus_timings.py DATA_DIR              # dry run
    python tools/import_rti_bus_timings.py DATA_DIR --only ernakulam
    python tools/import_rti_bus_timings.py DATA_DIR --overrides fixes.csv
    python tools/import_rti_bus_timings.py DATA_DIR --write      # store
    python tools/import_rti_bus_timings.py DATA_DIR --write --replace

A dry run changes nothing; it prints what would be stored and writes the
matched and unmatched stops to CSV files (see --out). --write stores the
times; it refuses when an earlier import is already stored, unless
--replace is given, which removes only that earlier import's times first.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from transitnav import places, timings  # noqa: E402
from transitnav.geo import haversine  # noqa: E402

DEVICE = "import:mvd-rti"       # marks the imported rows in data/timings.db
SOURCE = "MVD list"             # shown with each time, so riders know where it came from

# The town each RTO file is named for; looked up in the place index and used
# to pick the right "Medical College" or "Bus Stand" among many.
AREAS = {
    "alappuzha": "Alappuzha", "attingal": "Attingal", "ernakulam": "Ernakulam",
    "idukki": "Thodupuzha", "kannur": "Kannur", "kottayam": "Kottayam",
    "kozhikkode": "Kozhikode", "malappuram": "Malappuram", "muvattupuzha": "Muvattupuzha",
    "palakkad-1": "Palakkad", "palakkad-2": "Palakkad", "pathanamthitta": "Pathanamthitta",
    "vadakara": "Vadakara", "wayanad": "Kalpetta",
}
AREA_KINDS = ("Town", "City", "Bus stand", "Station")

CANDIDATES = 6           # place matches tried per stop
AREA_REACH_KM = 80       # a first stop this far from its RTO town is not believed
MAX_KMH = 55             # straight-line speed between two stops; faster is a wrong match
SLACK_KM = 2.0           # added to the distance a bus could cover (stop points are rough)
GAP_COST = 30.0          # cost of leaving a stop unmatched in a route
ROUTE_MAX = 60           # timings.add() keeps a route label this long

TIME = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*([ap])\.?\s*m\.?\s*$", re.I)
REG = re.compile(r"^KL\s?\d{1,2}\s?[A-Z]{0,3}\s?\d{1,4}$")
# Words the timetable adds that the map rarely has in a name, and labels of
# circular services ("Vyttila Clockwise") that are not part of the stop's name.
NOISE = re.compile(r"\b(private|pvt|ksrtc|anti\s*clockwise|clockwise|circular)\b", re.I)
# Words that say what sort of place it is, not which one. A match must share
# the other words: "Fortkochi Bus Terminal" must not land on any "Bus Terminal".
GENERIC = {"bus", "stand", "station", "stop", "terminal", "jn", "junction", "jetty", "road",
           "gate", "hub", "mobility", "kavala", "town", "private", "new", "old", "stn"}
MIN_SHARE = 0.6          # share of a stop's own words a place's name must have
# Names the timetable writes as one word where the map has two.
SPELLINGS = {"fortkochi": "Fort Kochi", "medicalcollege": "Medical College",
             "kodungallore": "Kodungallur"}


def minutes(text):
    """'05:07 am' -> 307; None when it is not a time."""
    m = TIME.match(text or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 12 or mi > 59:
        return None
    h = h % 12 + (12 if m.group(3).lower() == "p" else 0)
    return h * 60 + mi


def tidy(name):
    """'ALUVA PRIVATE BUS STAND' -> 'Aluva Bus Stand'; 'VYTTILA JN.' -> 'Vyttila Jn'."""
    name = re.sub(r"[._]+", " ", str(name or ""))
    name = re.sub(r"\s*-\s*", " - ", name)
    name = " ".join(name.split())
    return name.title()


def queries(name):
    """Ways to look a timetable stop name up, best first.
    'CHITTETHUKARA BUS STOP- KAKKANAD' -> the whole name, then its parts."""
    full = NOISE.sub(" ", tidy(name))
    full = " ".join(SPELLINGS.get(w.lower(), w) for w in full.split())
    out = [full]
    if " - " in full:
        out += [p.strip() for p in full.split(" - ") if len(p.strip()) >= 3]
    return [q for i, q in enumerate(out) if q and q not in out[:i]]


def load(data_dir, only=None):
    """[(area key, bus dict), ...] from the JSON files in `data_dir`."""
    out = []
    for path in sorted(glob.glob(os.path.join(data_dir, "*.json"))):
        key = os.path.splitext(os.path.basename(path))[0].lower()
        if only and key not in only:
            continue
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for bus in data.get("busSchedules") or []:
            out.append((key, bus))
    return out


def read_overrides(path):
    """{tidy name lowercased: (lat, lon)} from a name,lat,lon CSV."""
    fixes = {}
    if not path:
        return fixes
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                fixes[tidy(row.get("name")).lower()] = (lat, lon)
    return fixes


class Finder:
    """Looks stop names up in the place index, once per name and area."""

    def __init__(self, search=places.search, overrides=None):
        self.search = search
        self.overrides = overrides or {}
        self.cache = {}
        self.areas = {}

    def area(self, key):
        """(lat, lon) of the town an RTO file is named for, or None."""
        if key not in self.areas:
            town = AREAS.get(key)
            point = None
            if town:
                hits = self.search(town, limit=10)
                best = [h for h in hits if h["name"].lower() == town.lower()
                        and h["detail"].split(" ·")[0] in AREA_KINDS] or hits[:1]
                if best:
                    point = (best[0]["lat"], best[0]["lon"])
            self.areas[key] = point
        return self.areas[key]

    def candidates(self, name, near):
        """[(lat, lon, cost), ...] for a stop name: hand fixes first, then the
        place index's matches near `near` (lower cost is a better match)."""
        fixed = self.overrides.get(tidy(name).lower())
        if fixed:
            return [(fixed[0], fixed[1], 0.0)]
        ck = (tidy(name).lower(), near)
        if ck not in self.cache:
            found, seen = [], set()
            for rank_q, q in enumerate(queries(name)):
                own = [w for w in re.findall(r"[a-z0-9]+", q.lower()) if w not in GENERIC]
                for rank, hit in enumerate(self.search(q, near=near, limit=CANDIDATES)):
                    if own and places._covered(own, hit["name"], hit.get("detail", "")) < MIN_SHARE:
                        continue                         # matched only "bus stand" or the like
                    spot = (round(hit["lat"], 4), round(hit["lon"], 4))
                    if spot in seen:
                        continue
                    seen.add(spot)
                    found.append((hit["lat"], hit["lon"], rank + 4.0 * rank_q))
            self.cache[ck] = found[:CANDIDATES]
        return self.cache[ck]


def place_route(stops, finder, area):
    """Map points for a route's stops, picked together.

    `stops` is [(name, minute), ...] in riding order. Returns a list of
    (lat, lon) or None per stop. Each step from one matched stop to the next
    must be coverable at MAX_KMH in the minutes between them; a stop that fits
    nowhere is left out (None) rather than guessed."""
    cands = [finder.candidates(name, area) for name, _ in stops]
    if area:
        cands[0] = [c for c in cands[0]
                    if haversine(area[0], area[1], c[0], c[1]) <= AREA_REACH_KM * 1000] or cands[0]
    # Best path over stops, where each stop is one of its candidates or left
    # out. A state is the last placed stop, (stop index, candidate index), or
    # None before any: what the next stop is checked against. Keeping states
    # apart (not merging every "left out" into one) means a skipped stop never
    # makes the route forget where it last was.
    # layer: {state: (cost, previous state, choice at this stop)}
    layers = []
    prev = {None: (0.0, None, None)}
    for i, (name, minute) in enumerate(stops):
        layer = {}

        def offer(state, cost, back, choice):
            if state not in layer or cost < layer[state][0]:
                layer[state] = (cost, back, choice)

        for state, (pcost, _, _) in prev.items():
            offer(state, pcost + GAP_COST, state, None)          # leave this stop out
            for c, (lat, lon, own) in enumerate(cands[i]):
                cost = pcost + own
                if state is not None:
                    j, pc = state
                    plat, plon, _ = cands[j][pc]
                    pmin = stops[j][1]
                    if minute is not None and pmin is not None:
                        gap = minute - pmin
                        if gap < -600:
                            gap += 1440                          # the trip runs past midnight
                        gap = max(1, gap)
                    else:
                        gap = 60
                    km = haversine(plat, plon, lat, lon) / 1000.0
                    if km > MAX_KMH * gap / 60.0 + SLACK_KM:
                        continue                                 # too far for the time given
                    if km < 0.05:
                        cost += 5                                # two stops on one spot is unlikely
                offer((i, c), cost, state, c)
        layers.append(layer)
        prev = layer
    # Walk back from the cheapest end.
    state = min(prev, key=lambda s: prev[s][0])
    out = [None] * len(stops)
    for i in range(len(stops) - 1, -1, -1):
        cost, back, choice = layers[i][state]
        if choice is not None:
            out[i] = (cands[i][choice][0], cands[i][choice][1])
        state = back
    return out


def trip_reports(bus, trip, points_by_name):
    """The times one trip gives: a report dict per stop it leaves."""
    stations = trip.get("stations") or []
    stops = []
    for s in stations:
        dep = minutes(s.get("departureTime")) if s.get("departureTime") else None
        dep = dep if dep is not None else minutes(s.get("arrivalTime"))
        stops.append((s.get("station") or "", dep, points_by_name.get(s.get("station") or "")))
    if len(stops) < 2:
        return []
    last = tidy(stops[-1][0])
    first = tidy(stops[0][0])
    reg = " ".join(str(bus.get("Vehicle Number") or "").split()).upper()
    head = ("%s · " % reg) if REG.match(reg) else ""
    tail = " (%s)" % SOURCE
    room = ROUTE_MAX - len(head) - len(tail)
    label = "%s - %s" % (first, last)
    if len(label) > room:                                # shorten the names, keep the source
        half = max(3, (room - 3) // 2)
        label = "%s - %s" % (first[:half].rstrip(), last[:room - 3 - half].rstrip())
    route = head + label + tail
    out = []
    for i, (name, dep, point) in enumerate(stops[:-1]):
        if point is None or dep is None:
            continue
        toward = next((p for _, _, p in stops[i + 1:]
                       if p is not None and haversine(point[0], point[1], p[0], p[1]) >= timings.MIN_TOWARD_M),
                      None)
        if toward is None:
            continue
        out.append({"board_name": tidy(name)[:80], "board_lat": point[0], "board_lon": point[1],
                    "toward_name": last[:80], "toward_lat": toward[0], "toward_lon": toward[1],
                    "dep_min": dep, "days": "daily", "operator": "private", "route": route[:ROUTE_MAX]})
    return out


def build(buses, finder, log=print):
    """(reports, matched {name: (lat, lon, area)}, unmatched {name: count})."""
    reports, matched, unmatched = [], {}, {}
    skipped_buses = 0
    for n, (area_key, bus) in enumerate(buses, 1):
        reg = str(bus.get("Vehicle Number") or "").upper()
        trips = bus.get("schedule") or []
        if "CANCEL" in reg or not trips:
            skipped_buses += 1
            continue
        area = finder.area(area_key)
        # Place the stops once per bus, using its longest trip's times.
        longest = max(trips, key=lambda t: len(t.get("stations") or []))
        sts = longest.get("stations") or []
        order = []
        for s in sts:
            dep = minutes(s.get("departureTime"))
            order.append((s.get("station") or "", dep if dep is not None else minutes(s.get("arrivalTime"))))
        points = place_route(order, finder, area) if order else []
        by_name = {}
        for (name, _), p in zip(order, points):
            if p is None:
                unmatched[tidy(name)] = unmatched.get(tidy(name), 0) + 1
            else:
                by_name[name] = p
                matched.setdefault(tidy(name), (p[0], p[1], area_key))
        for trip in trips:
            reports += trip_reports(bus, trip, by_name)
        if n % 500 == 0:
            log("  %d of %d buses, %d times so far" % (n, len(buses), len(reports)))
    if skipped_buses:
        log("  skipped %d buses marked cancelled or with no trips" % skipped_buses)
    return reports, matched, unmatched


def store(reports, replace=False, db_path=None):
    """Write the reports to data/timings.db in one transaction. Returns the
    number stored. Refuses when an earlier import is there, unless `replace`."""
    if db_path:
        timings.DB_PATH = db_path
    db = timings._connect()
    try:
        old = db.execute("SELECT COUNT(*) FROM reports WHERE device=?", (DEVICE,)).fetchone()[0]
        if old and not replace:
            raise SystemExit("%d imported times are already stored. Run again with --replace "
                             "to swap them for this import." % old)
        now = time.time()
        with db:
            if old:
                db.execute("DELETE FROM votes WHERE report_id IN "
                           "(SELECT id FROM reports WHERE device=?)", (DEVICE,))
                db.execute("DELETE FROM reports WHERE device=?", (DEVICE,))
            for r in reports:
                cur = db.execute(
                    "INSERT INTO reports (board_name, board_lat, board_lon, toward_name, toward_lat,"
                    " toward_lon, dep_min, days, operator, route, device, created)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["board_name"], r["board_lat"], r["board_lon"], r["toward_name"],
                     r["toward_lat"], r["toward_lon"], r["dep_min"], r["days"], r["operator"],
                     r["route"], DEVICE, now))
                # Counted like one rider telling the time; riders can still flag it.
                db.execute("INSERT OR REPLACE INTO votes VALUES (?,?,1)", (cur.lastrowid, DEVICE))
        return len(reports)
    finally:
        db.close()


def write_csvs(out_dir, matched, unmatched):
    os.makedirs(out_dir, exist_ok=True)
    m_path = os.path.join(out_dir, "rti_stops_matched.csv")
    u_path = os.path.join(out_dir, "rti_stops_unmatched.csv")
    with open(m_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "lat", "lon", "area"])
        for name in sorted(matched):
            lat, lon, area = matched[name]
            w.writerow([name, "%.6f" % lat, "%.6f" % lon, area])
    with open(u_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "lat", "lon", "times_seen"])
        for name, count in sorted(unmatched.items(), key=lambda kv: -kv[1]):
            w.writerow([name, "", "", count])
    return m_path, u_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data_dir", help="folder with the downloaded JSON files (keep it outside the repo)")
    ap.add_argument("--only", nargs="*", help="RTO files to use, e.g. ernakulam muvattupuzha")
    ap.add_argument("--overrides", help="CSV of name,lat,lon for stops placed by hand")
    ap.add_argument("--out", default=os.path.join(ROOT, ".cache", "rti_import"),
                    help="where the matched/unmatched stop CSVs go (default .cache/rti_import)")
    ap.add_argument("--write", action="store_true", help="store the times in data/timings.db")
    ap.add_argument("--replace", action="store_true", help="with --write: swap an earlier import")
    args = ap.parse_args(argv)

    if not os.path.exists(places.DB):
        raise SystemExit("The place index is missing (%s). Start the app once so it builds it." % places.DB)
    only = {o.lower() for o in args.only} if args.only else None
    buses = load(args.data_dir, only)
    if not buses:
        raise SystemExit("No buses found in %s." % args.data_dir)
    print("Read %d buses from %s" % (len(buses), args.data_dir))
    finder = Finder(overrides=read_overrides(args.overrides))
    reports, matched, unmatched = build(buses, finder)
    m_path, u_path = write_csvs(args.out, matched, unmatched)
    print("Stops placed on the map: %d names; not placed: %d names (%d sightings)"
          % (len(matched), len(unmatched), sum(unmatched.values())))
    print("Times ready: %d" % len(reports))
    print("Matched stops:   %s" % m_path)
    print("Unmatched stops: %s  (fill in lat,lon and pass it as --overrides)" % u_path)
    if not args.write:
        print("Dry run: nothing was stored. Add --write to store these times.")
        return 0
    n = store(reports, replace=args.replace)
    print("Stored %d times in %s" % (n, timings.DB_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
