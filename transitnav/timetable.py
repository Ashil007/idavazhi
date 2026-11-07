"""Indian Railways timetable, for long-distance train trips.

Track geometry says where rails run, not which trains use them. For trips
between cities what matters is a timetable: which trains call where, and
when. This module uses the open datameet dataset (CC0,
https://github.com/datameet/railways): about 5,200 trains, 9,000 stations
and every station each train passes, with times.

The data dates from 2016. Trains have been added, renumbered and retimed
since, and it has no running days - every train is treated as daily. Results
say so.

The three source files (~100 MB) are downloaded once and boiled down to a
compact index (`.cache/timetable/index.json.gz`, a few MB). In memory each
train is a list of stops (station code, arrival, departure, halts?) with
times in minutes from the train's departure day, so overnight runs simply
count past 1440.
"""

from __future__ import annotations

import gzip
import heapq
import json
import os
import threading
import time
import urllib.request

from . import overpass

SOURCE = "https://raw.githubusercontent.com/datameet/railways/master/%s.json"
FILES = ("stations", "trains", "schedules")
DIR = os.path.join(overpass.CACHE_DIR, "timetable")
INDEX = os.path.join(DIR, "index.json.gz")
FORMAT = 1

MIN_CHANGE_MIN = 40          # least time allowed to change trains
CHANGE_PENALTY_MIN = 120     # a change must save this much to beat a direct train
PER_STATION_KEEP = 12        # best arrivals/departures kept per station when joining


# ----------------------------------------------------------------- building

def _minutes(text):
    """"HH:MM:SS" -> minutes past midnight; None for "None" or blanks."""
    if not text or text == "None":
        return None
    try:
        h, m = text.split(":")[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def _download(log):
    os.makedirs(DIR, exist_ok=True)
    for name in FILES:
        target = os.path.join(DIR, name + ".json")
        if os.path.exists(target):
            continue
        log("  downloading the %s file of the Indian Railways timetable" % name)
        req = urllib.request.Request(SOURCE % name, headers={
            "User-Agent": overpass.USER_AGENT, "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
        with open(target + ".tmp", "wb") as fh:
            fh.write(raw)
        os.replace(target + ".tmp", target)


def _build(log):
    """Raw files -> compact index."""
    started = time.time()
    with open(os.path.join(DIR, "stations.json"), encoding="utf-8") as fh:
        raw_stations = json.load(fh)["features"]
    with open(os.path.join(DIR, "trains.json"), encoding="utf-8") as fh:
        raw_trains = json.load(fh)["features"]
    with open(os.path.join(DIR, "schedules.json"), encoding="utf-8") as fh:
        raw_rows = json.load(fh)

    stations = {}
    for f in raw_stations:
        p, g = f.get("properties") or {}, f.get("geometry")
        if p.get("code") and g and g.get("coordinates"):
            lon, lat = g["coordinates"][:2]
            stations[p["code"]] = [p.get("name") or p["code"], round(lat, 6), round(lon, 6)]

    info = {}
    for f in raw_trains:
        p = f.get("properties") or {}
        if p.get("number"):
            info[p["number"]] = p

    by_train: dict = {}
    for r in raw_rows:
        if r.get("train_number") and r.get("station_code"):
            by_train.setdefault(r["train_number"], []).append(r)

    trains = []
    dropped = 0
    for number, rows in by_train.items():
        rows.sort(key=lambda r: r["id"])
        stops, day, last = [], 0, None
        for idx, r in enumerate(rows):
            arr, dep = _minutes(r.get("arrival")), _minutes(r.get("departure"))
            out = []
            for t in (arr, dep):
                if t is None:
                    out.append(None)
                    continue
                # Times only go forward along a train's route; a time
                # earlier than the last one means midnight was crossed. The
                # file's own day numbers disagree with its order for some
                # trains, so they are not used.
                absolute = day * 1440 + t
                if last is not None and absolute < last - 1:
                    day += 1
                    absolute += 1440
                last = absolute
                out.append(absolute)
            arr_abs, dep_abs = out
            if arr_abs is None and dep_abs is None:
                stops.append([r["station_code"], None, None, 0])   # passed, untimed
                continue
            first, final = idx == 0, idx == len(rows) - 1
            # A train only *calls* where it stands: the first and last stop,
            # or where departure is later than arrival. Equal times mark a
            # station it runs through.
            halts = first or final or (arr_abs is not None and dep_abs is not None
                                       and dep_abs > arr_abs)
            stops.append([r["station_code"], arr_abs if arr_abs is not None else dep_abs,
                          dep_abs if dep_abs is not None else arr_abs, 1 if halts else 0])
        if sum(s[3] for s in stops) < 2:
            dropped += 1
            continue
        p = info.get(number, {})
        trains.append({"number": number, "name": p.get("name") or rows[0].get("train_name") or "",
                       "type": p.get("type") or "", "stops": stops})

    data = {"format": FORMAT, "built": time.time(), "source": SOURCE % "*",
            "stations": stations, "trains": trains}
    with gzip.open(INDEX + ".tmp", "wt", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(INDEX + ".tmp", INDEX)
    log("  built the timetable index: %s trains, %s stations with a location, in %.0f s"
        % (format(len(trains), ","), format(len(stations), ","), time.time() - started))
    # The raw files are only needed to build the index.
    for name in FILES:
        try:
            os.remove(os.path.join(DIR, name + ".json"))
        except OSError:
            pass
    return data


_ensure_lock = threading.Lock()


def ensure(log=print) -> bool:
    """Download and index the timetable if that has not been done. Returns
    True when the index is available.

    The start-up preload and a plan request can both get here; the lock
    makes the second wait for the first instead of downloading into, and
    deleting, the same files."""
    with _ensure_lock:
        if os.path.exists(INDEX):
            return True
        _download(log)
        _build(log)
        return os.path.exists(INDEX)


# ------------------------------------------------------------------ loading

class Timetable:
    def __init__(self, data):
        self.stations = data["stations"]            # code -> [name, lat, lon]
        # Left out: "special" trains (numbers starting 0), which ran for a
        # season, and ladies-only locals, which most riders cannot board.
        self.trains = [t for t in data["trains"]
                       if not t["number"].startswith("0") and "ladies" not in t["name"].lower()]
        # A train always calls at its first and last timed stop. Some
        # schedules begin or end with untimed signal-cabin rows, so "first"
        # and "last" row are not enough to find them.
        for train in self.trains:
            timed = [s for s in train["stops"] if s[1] is not None]
            if timed:
                timed[0][3] = timed[-1][3] = 1
        # code -> [(train index, stop index)] for every stop where a train calls
        self.calls: dict = {}
        for t, train in enumerate(self.trains):
            for i, stop in enumerate(train["stops"]):
                if stop[3]:
                    self.calls.setdefault(stop[0], []).append((t, i))

    def station(self, code):
        """{"id", "code", "name", "lat", "lon"} or None if it has no location."""
        s = self.stations.get(code)
        if not s:
            return None
        return {"id": "station/" + code, "code": code, "name": _title(s[0]),
                "lat": s[1], "lon": s[2]}

    def served(self):
        """Stations with a location where at least one train calls, each with
        `trains`: how many call there."""
        if not hasattr(self, "_served"):
            out = []
            for code, calls in self.calls.items():
                s = self.station(code)
                if s:
                    s["trains"] = len(calls)
                    out.append(s)
            self._served = out
        return self._served

    def train(self, t):
        tr = self.trains[t]
        return {"number": tr["number"], "name": _title(tr["name"]), "type": tr["type"]}

    def search(self, board: dict, alight: dict, limit: int = 4):
        """Fastest journeys from any `board` station to any `alight` one.

        `board` maps station code -> seconds to get there from the start,
        `alight` code -> seconds from there to the destination. Journeys use
        one train, or two with at least MIN_CHANGE_MIN to change. The wait for
        the first train is not counted: no departure time is asked for.

        Returns up to `limit` journeys, best first, each
        {"rides": [(train, board stop, alight stop), ...], "waits": [minutes],
         "seconds": door-to-door total}.
        """
        trains = self.trains
        found = []
        arrivals: dict = {}    # change station -> [(cost s, arrival min, t, i, k)]
        for b, access_s in board.items():
            for t, i in self.calls.get(b, ()):
                stops = trains[t]["stops"]
                dep = stops[i][2]
                for k in range(i + 1, len(stops)):
                    code, arr, _, halts = stops[k]
                    if not halts:
                        continue
                    ride_s = (arr - dep) * 60
                    if code in alight:
                        found.append((access_s + ride_s + alight[code], [(t, i, k)], []))
                    elif code not in board and code in self.stations:
                        # Changes only where the station is on the map: the
                        # rider must be told where it is, and the leg drawn.
                        arrivals.setdefault(code, []).append((access_s + ride_s, arr, t, i, k))

        departures: dict = {}  # change station -> [(cost s, departure min, t, l, j)]
        for a, egress_s in alight.items():
            for t, j in self.calls.get(a, ()):
                stops = trains[t]["stops"]
                arr = stops[j][1]
                for l in range(j - 1, -1, -1):
                    code, _, dep, halts = stops[l]
                    if halts and code not in alight and code in arrivals:
                        departures.setdefault(code, []).append(
                            (egress_s + (arr - dep) * 60, dep, t, l, j))

        for code, outs in departures.items():
            ins = heapq.nsmallest(PER_STATION_KEEP, arrivals[code])
            outs = heapq.nsmallest(PER_STATION_KEEP, outs)
            for cost_in, arr, t1, i, k in ins:
                for cost_out, dep, t2, l, j in outs:
                    if t1 == t2:
                        continue
                    wait = (dep - arr) % 1440
                    if wait < MIN_CHANGE_MIN:
                        wait += 1440      # missed it: the same train tomorrow
                    found.append((cost_in + wait * 60 + cost_out,
                                  [(t1, i, k), (t2, l, j)], [wait]))

        found.sort(key=lambda f: f[0] + (len(f[1]) - 1) * CHANGE_PENALTY_MIN * 60)
        # Each train appears in one option at most, so the alternatives are
        # real choices rather than the best one with a different feeder.
        out, used = [], set()
        for seconds, rides, waits in found:
            if any(r[0] in used for r in rides):
                continue
            used.update(r[0] for r in rides)
            out.append({"rides": rides, "waits": waits, "seconds": seconds})
            if len(out) >= limit:
                break
        return out


def _title(name: str) -> str:
    """"ERNAKULAM JN" -> "Ernakulam Jn", "TRIVANDRUM - DELHI Rajdhani" ->
    "Trivandrum - Delhi Rajdhani". Short capitals (SF, AC, JN in a mixed
    name) are left alone."""
    if not name:
        return name
    if name.isupper():
        return " ".join(w.capitalize() for w in name.split())
    return " ".join(w.capitalize() if w.isupper() and len(w) > 3 else w for w in name.split())


_loaded = None
_lock = threading.Lock()


def load():
    """The timetable, read once per process; None if not downloaded yet."""
    global _loaded
    with _lock:
        if _loaded is not None:
            return _loaded
        try:
            with gzip.open(INDEX, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if data.get("format") != FORMAT:
            return None
        _loaded = Timetable(data)
        return _loaded
