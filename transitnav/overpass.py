"""Overpass API client: query building, mirror failover and a disk cache.

Overpass is a shared free service. We are deliberately frugal with it: every
response is cached on disk keyed by the query text, mirrors are rotated on
failure, and requests are spaced out by a small politeness delay.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .config import TILE_TTL_S
from .geo import corridor_polygon, haversine, point_to_segment_m

# Full-planet mirrors only. A regional instance such as overpass.osm.ch
# (Switzerland) answers queries elsewhere with a clean, empty result that is
# indistinguishable from "nothing is mapped here".
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# A mirror that is down does not refuse the connection - it accepts it and
# never answers, so every request pays the full read timeout. Once one fails we
# sideline it for a while, which is the difference between a plan that takes
# seconds and one that takes minutes.
MIRROR_COOLDOWN_S = 300
MAX_ATTEMPT_S = 120       # no single mirror may hold a request longer than this
_mirror_down: dict = {}
_mirror_lock = threading.Lock()


def _mirror_available(url: str) -> bool:
    with _mirror_lock:
        return time.time() >= _mirror_down.get(url, 0.0)


def _mark_mirror_down(url: str, strike: bool = True) -> None:
    """Sideline a mirror. `strike` also shortens its future timeouts, which is
    right for one that stopped answering but wrong for one that answered
    "busy": that mirror is alive, and may be the only one that is."""
    with _mirror_lock:
        _mirror_down[url] = time.time() + MIRROR_COOLDOWN_S
        if strike:
            _mirror_strikes[url] = _mirror_strikes.get(url, 0) + 1


def _mark_mirror_up(url: str) -> None:
    with _mirror_lock:
        _mirror_down.pop(url, None)
        _mirror_strikes.pop(url, None)


_mirror_strikes: dict = {}
# A mirror that has failed before gets a shorter leash. Not too short: under
# load even a trivial query can take ~30 s, and a real one several times that.
STRUCK_ATTEMPT_S = 60
BUSY_RETRIES = 2          # extra tries per query for mirrors that answered "busy"
BUSY_BACKOFF_S = 5.0      # pause before a busy retry, growing with each one


def _ordered_endpoints():
    """Healthy mirrors only. A sidelined mirror is tried again only once every
    mirror is sidelined - otherwise one dead mirror costs a full read timeout on
    every query that falls through to it."""
    healthy = [e for e in ENDPOINTS if _mirror_available(e)]
    return healthy or list(ENDPOINTS)


def _attempt_timeout(url: str, timeout: float, cap: float | None = None) -> float:
    with _mirror_lock:
        struck = _mirror_strikes.get(url, 0) > 0
    if cap is not None:      # a deliberately big download (preload) may run longer
        return min(timeout, cap)
    return min(timeout, STRUCK_ATTEMPT_S if struck else MAX_ATTEMPT_S)


def probe_mirrors(timeout: float = 8.0) -> dict:
    """Ping every mirror in parallel with a trivial query and sideline the dead
    ones before any real request can get stuck on them. Returns url->seconds
    (or None for a failure)."""
    q = urllib.parse.urlencode(
        {"data": "[out:json][timeout:5];node(51.5,-0.13,51.5001,-0.1299);out ids;"}
    ).encode("utf-8")
    results = {}

    def ping(url):
        t0 = time.time()
        try:
            _http_post(url, q, timeout)
            results[url] = time.time() - t0
            _mark_mirror_up(url)
        except Exception:  # noqa: BLE001
            results[url] = None
            # Sideline it, but no strike: missing an 8-second ping usually
            # means "busy", and a strike would cut every real query to that
            # mirror short - even when it is the only one still answering.
            _mark_mirror_down(url, strike=False)

    threads = [threading.Thread(target=ping, args=(u,), daemon=True) for u in ENDPOINTS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 2)
    return results

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "vazhi-transit/1.0 (local journey planner; stdlib urllib)"

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "overpass"
)
CACHE_TTL_S = 7 * 24 * 3600

_throttle_lock = threading.Lock()
_last_call = [0.0]
MIN_INTERVAL_S = 1.0
NL = chr(10)  # newline, spelled this way to keep query builders escape-free


class OverpassError(RuntimeError):
    """Raised when every mirror fails or returns something unusable."""


def _cache_path(key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, digest + ".json.gz")


# An empty answer is usually genuine (a tile with no metro in it) but can be an
# overloaded mirror timing out silently, so it is trusted for a day, not a month.
EMPTY_TTL_S = 24 * 3600


def _cache_get(key: str, ttl_s: float | None = CACHE_TTL_S):
    """Cached response for `key`, or None. `ttl_s=None` accepts any age."""
    path = _cache_path(key)
    try:
        age = time.time() - os.path.getmtime(path)
        if ttl_s is not None and age > ttl_s:
            return None
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        # An empty tile cut from a larger download (see preload_tiles) is
        # known to be empty, not a dropped query, so it keeps the full TTL.
        if (ttl_s is not None and not data.get("elements")
                and not data.get("complete") and age > EMPTY_TTL_S):
            return None
        return data
    except (OSError, ValueError):
        return None


def is_cached(ql: str, ttl_s: float | None = CACHE_TTL_S) -> bool:
    return _cache_get(ql, ttl_s) is not None


def _cache_put(key: str, value) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(key)
    # Each writer its own temp file: a preload and a plan may store the same
    # tile at once, and sharing one temp file would interleave their bytes.
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(value, fh)
        os.replace(tmp, path)
    except OSError:
        pass  # a cache miss is never fatal


def _throttle() -> None:
    with _throttle_lock:
        wait = MIN_INTERVAL_S - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


def _http_post(url: str, data: bytes, timeout: float) -> bytes:
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw


_inflight: dict = {}
_inflight_lock = threading.Lock()


def query(
    ql: str,
    timeout: float = 240.0,
    use_cache: bool = True,
    allow_empty: bool = False,
    deadline_s: float | None = None,
    ttl_s: float = CACHE_TTL_S,
    attempt_s: float | None = None,
    prefer: str | None = None,
) -> dict:
    """Run an Overpass QL query and return the parsed JSON response.

    Three things keep this fast and gentle on the shared service:
      * a fresh enough cached copy is returned without asking Overpass,
      * a query already being downloaded - by a background prefetch or by
        an earlier click on Plan - is waited for rather than sent again, and
      * if every mirror fails, an expired cached copy beats no answer.
    """
    if not use_cache:
        return _query_live(ql, timeout, False, allow_empty, deadline_s, attempt_s, prefer)
    cached = _cache_get(ql, ttl_s)
    if cached is not None:
        return cached

    with _inflight_lock:
        event = _inflight.get(ql)
        owner = event is None
        if owner:
            event = _inflight[ql] = threading.Event()
    if not owner:
        event.wait(timeout)
        cached = _cache_get(ql, ttl_s)
        if cached is not None:
            return cached
        # The other download failed or returned nothing cacheable; try ourselves.
    try:
        return _query_live(ql, timeout, True, allow_empty, deadline_s, attempt_s, prefer)
    except OverpassError:
        stale = _cache_get(ql, None)
        if stale is not None:
            return stale
        raise
    finally:
        if owner:
            with _inflight_lock:
                _inflight.pop(ql, None)
            event.set()


def _query_live(ql, timeout, use_cache, allow_empty, deadline_s, attempt_s=None,
                prefer=None) -> dict:
    payload = urllib.parse.urlencode({"data": ql}).encode("utf-8")
    errors = []
    last_empty = None
    started = time.time()
    healthy = [e for e in ENDPOINTS if _mirror_available(e)]
    # Parallel downloads each start on a different mirror (`prefer`), so
    # they share the work instead of all queueing at the first one.
    if prefer in healthy:
        healthy.remove(prefer)
        healthy.insert(0, prefer)
    sidelined = [e for e in ENDPOINTS if e not in healthy]
    queue = healthy + sidelined
    busy_retries = BUSY_RETRIES
    attempt = -1
    while queue:
        endpoint = queue.pop(0)
        attempt += 1
        # Sidelined mirrors are a last resort for when nothing answered at all.
        # If a healthy mirror did answer (even with nothing), a dead one will
        # not do better - it would only add its timeout to the user's wait.
        if endpoint in sidelined and last_empty is not None:
            break
        # `deadline_s` caps the whole mirror-rotation, not each attempt, so a
        # caller that can degrade gracefully (walking legs) is never left
        # waiting on four slow mirrors in series.
        spent = time.time() - started
        if deadline_s is not None and spent >= deadline_s:
            errors.append("gave up after %.0fs" % spent)
            break
        per_call = _attempt_timeout(endpoint, timeout, attempt_s)
        if deadline_s is not None:
            per_call = max(5.0, min(per_call, deadline_s - spent))
        try:
            _throttle()
            raw = _http_post(endpoint, payload, per_call)
            data = json.loads(raw.decode("utf-8", "replace"))
            if "elements" not in data:
                raise OverpassError(endpoint + ": response had no 'elements'")
            remark = data.get("remark") or ""
            if "runtime error" in remark:
                # A timeout or memory abort still answers 200, carrying whatever
                # partial elements were ready. Routing over a truncated network
                # gives wrong paths, so never use or cache it.
                errors.append("%s: %s" % (endpoint, " ".join(remark.split())[:200]))
                continue
            if not data["elements"] and not allow_empty:
                time.sleep(1.5)
                _throttle()
                retry = json.loads(
                    _http_post(endpoint, payload, per_call).decode("utf-8", "replace")
                )
                if retry.get("elements"):
                    data = retry
            if not data["elements"] and not allow_empty:
                # An overloaded mirror can answer a slow query with an empty
                # element list and no remark at all. Caching that would poison
                # the area forever, so treat it as a failure and try the next
                # mirror. If every mirror agrees, we accept the empty answer.
                last_empty = data
                errors.append(endpoint + ": empty result (possible silent timeout)")
                continue
            _mark_mirror_up(endpoint)
            if use_cache:
                _cache_put(ql, data)
            return data
        except urllib.error.HTTPError as exc:
            # Overpass explains query syntax errors in the 400 body; without it
            # a malformed query is indistinguishable from a dead mirror.
            detail = ""
            try:
                body = exc.read()
                if exc.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)   # we asked for gzip, errors included
                body = body.decode("utf-8", "replace")
                if "</strong>" in body:
                    body = body.split("error:", 1)[-1]
                body = re.sub(r"<[^>]*>", " ", body)   # error pages are HTML
                detail = " " + " ".join(body.split())[:300]
            except Exception:  # noqa: BLE001
                pass
            errors.append("%s: HTTP %s%s" % (endpoint, exc.code, detail))
            # 429/504 mean the mirror is busy, not broken: sideline it so the
            # next query starts elsewhere. It is alive, though - often the only
            # mirror that is - so it goes to the back of the queue for another
            # try once the others have had their turn.
            if exc.code in (429, 504):
                _mark_mirror_down(endpoint, strike=False)
                if busy_retries > 0:
                    busy_retries -= 1
                    queue.append(endpoint)
                    time.sleep(BUSY_BACKOFF_S * (BUSY_RETRIES - busy_retries))
                else:
                    time.sleep(min(3.0, 1.0 * (attempt + 1)))
        except Exception as exc:  # noqa: BLE001 - surface any transport failure
            # A timeout or connection failure means this mirror is unhealthy.
            _mark_mirror_down(endpoint)
            errors.append("%s: %s: %s" % (endpoint, type(exc).__name__, exc))

    if last_empty is not None:
        return last_empty  # every mirror says this area really is empty

    raise OverpassError(
        "No Overpass mirror answered: OpenStreetMap's query service is overloaded "
        "or unreachable. Try again in a minute - anything already downloaded is "
        "cached, so a retry picks up where this left off."
        + NL
        + "  "
        + (NL + "  ").join(errors)
    )


# --------------------------------------------------------------- query builders


def _fmt_bbox(bbox) -> str:
    return "{:.6f},{:.6f},{:.6f},{:.6f}".format(*bbox)


def area_clause(area) -> str:
    """Render a search area for Overpass.

    `area` is either a (south, west, north, east) bbox tuple or a polygon
    already formatted as "lat lon lat lon ..." by geo.polygon_to_overpass.
    """
    if isinstance(area, str):
        return 'poly:"%s"' % area
    return _fmt_bbox(area)


def build_network_query(areas, filters, excludes=(), timeout: int = 120,
                        oneway_keys=None) -> str:
    """Ways matching `filters` across one or more areas, minus `excludes`.

    `areas` is a bbox tuple, a `poly:` string, or a list of either - several
    neighbourhoods can share one request. Two Overpass rules shape the output:

      * `["key"!~"..."]` only matches elements that already carry the key, so a
        negative condition must be subtracted from the union instead, and
      * the difference operator takes exactly one subtrahend, so every
        exclusion goes inside a single nested union.

    `out skel qt` keeps the payload small: way node-id lists and node
    coordinates, no tags, which is all the router needs. A network that obeys
    one-way streets passes `oneway_keys`; the tags of just the ways that can be
    one-way are then appended, rather than every tag of every way.
    """
    if isinstance(areas, str) or isinstance(areas[0], (int, float)):
        areas = [areas]

    positive, negative = [], []
    for area in areas:
        box = area_clause(area)
        positive += ["way(%s)%s;" % (box, f) for f in filters]
        negative += ["way(%s)%s;" % (box, f) for f in excludes]

    def block(clauses, indent):
        pad = " " * indent
        return (NL).join(pad + c for c in clauses)

    if negative:
        union = (
            "(" + NL
            + "  (" + NL + block(positive, 4) + NL + "  );" + NL
            + "  - (" + NL + block(negative, 4) + NL + "  );" + NL
            + ");"
        )
    else:
        union = "(" + NL + block(positive, 2) + NL + ");"

    if not oneway_keys:
        return (
            "[out:json][timeout:%d];" % timeout + NL
            + union + NL
            + "(._;>;);" + NL
            + "out skel qt;"
        )

    # Inside a union each statement overwrites the default set, so the network
    # is parked in a named set that both tag filters can read.
    key_re = "^(junction|%s)$" % "|".join(oneway_keys)
    return (
        "[out:json][timeout:%d];" % timeout + NL
        + union + NL
        + "(._;>;)->.net;" + NL
        + ".net out skel qt;" + NL
        + "(" + NL
        + '  way.net[~"%s"~"."];' % key_re + NL
        + '  way.net["highway"="motorway"];' + NL
        + ");" + NL
        + "out tags qt;"
    )


def build_stops_in_bbox_query(bbox, filters, timeout: int = 90) -> str:
    """Stops of one mode inside a bbox or polygon. `nwr` + `out center`
    handles stations mapped as areas or relations as well as simple nodes."""
    box = area_clause(bbox)
    parts = "\n  ".join(f"nwr({box}){f};" for f in filters)
    return f"[out:json][timeout:{timeout}];\n(\n  {parts}\n);\nout center tags;"


# ------------------------------------------------------------------ tiles
#
# A query for "the area around this trip" is different text for every trip,
# so it is never found in the cache. A fixed grid of tiles gives every trip in
# a city the same handful of queries: the first trip downloads them, every
# later one - and every background prefetch - reads them from disk.


def tile_bbox(tile, deg: float):
    iy, ix = tile
    return (iy * deg, ix * deg, (iy + 1) * deg, (ix + 1) * deg)


def tiles_for_bbox(bbox, deg: float):
    south, west, north, east = bbox
    return [(iy, ix)
            for iy in range(math.floor(south / deg), math.floor(north / deg) + 1)
            for ix in range(math.floor(west / deg), math.floor(east / deg) + 1)]


def tiles_for_corridor(a, b, buffer_m: float, deg: float):
    """Tiles that overlap the corridor of `buffer_m` either side of a->b."""
    corners = corridor_polygon(a, b, buffer_m)
    lats = [p[0] for p in corners]
    lons = [p[1] for p in corners]
    reach = buffer_m + haversine(0.0, 0.0, deg / 2.0, deg / 2.0)  # + half a tile diagonal
    kept = []
    for tile in tiles_for_bbox((min(lats), min(lons), max(lats), max(lons)), deg):
        s, w, n, e = tile_bbox(tile, deg)
        dist, _ = point_to_segment_m((s + n) / 2.0, (w + e) / 2.0, a[0], a[1], b[0], b[1])
        if dist <= reach:
            kept.append(tile)
    return kept


MAX_TILES_PER_QUERY = 150


def query_tiles(tiles, deg: float, build, report=None, what="map data") -> dict:
    """Run `build(bbox)` for every tile (two at a time, which Overpass allows
    per client) and merge the answers into one response."""
    if len(tiles) > MAX_TILES_PER_QUERY:
        # Near the poles a band a few km wide spans dozens of degrees of
        # longitude; nobody's trip needs that many tiles.
        raise OverpassError(
            "That trip would need %d map tiles of data, far more than any real "
            "trip. Pick points closer together." % len(tiles))
    qls = [build(tile_bbox(t, deg)) for t in tiles]
    missing = sum(not is_cached(q, TILE_TTL_S) for q in qls)
    if report is not None:
        if missing:
            report("download", "Downloading %s for %d of %d map tile%s (first time in "
                               "this area - later trips here load from disk)"
                   % (what, missing, len(qls), "" if len(qls) == 1 else "s"))
        else:
            report("download", "Loaded %s from the local cache" % what)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(
            lambda q: query(q, allow_empty=True, ttl_s=TILE_TTL_S), qls))
    seen, elements = set(), []
    for data in answers:
        for el in data.get("elements", ()):
            # A one-way network lists a way twice (geometry, then tags), so
            # the key keeps those apart while dropping copies from neighbours.
            key = (el.get("type"), el.get("id"), "nodes" in el, "tags" in el)
            if key not in seen:
                seen.add(key)
                elements.append(el)
    return {"elements": elements}


# ---------------------------------------------------------------- preloading
#
# Downloading a region tile by tile costs one Overpass round trip per tile,
# and on a loaded day each one can take minutes. A whole region in one request
# is far cheaper for the service and for us; the answer is then cut into the
# same per-tile cache files that trip planning reads, so planning never knows
# the difference.

PRELOAD_TIMEOUT_S = 300   # Overpass [timeout:] for one big request


def _tile_of(lat: float, lon: float, deg: float):
    return (math.floor(lat / deg), math.floor(lon / deg))


def split_by_tile(data: dict, deg: float, kind: str) -> dict:
    """Cut one response into {tile: [elements]}. `kind` is "stops" (each
    element sits in the tile holding its point) or "network" (each way goes
    to every tile it has a node in, with all of its nodes, as a tile query
    would return it)."""
    out: dict = {}
    elements = data.get("elements", ())
    if kind == "stops":
        for el in elements:
            p = el if "lat" in el else el.get("center")
            if p:
                out.setdefault(_tile_of(p["lat"], p["lon"], deg), []).append(el)
        return out
    nodes = {el["id"]: el for el in elements if el.get("type") == "node"}
    tile_nodes: dict = {}
    for el in elements:
        if el.get("type") != "way":
            continue
        refs = [nodes[r] for r in el.get("nodes", ()) if r in nodes]
        for tile in {_tile_of(n["lat"], n["lon"], deg) for n in refs}:
            out.setdefault(tile, []).append(el)
            bucket = tile_nodes.setdefault(tile, {})
            for n in refs:
                bucket[n["id"]] = n
    return {t: list(tile_nodes[t].values()) + ways for t, ways in out.items()}


def preload_tiles(tiles, deg: float, build, build_big, kind: str, progress=None,
                  max_block: int | None = None, workers: int = 1) -> int:
    """Fill the tile cache for `tiles` in as few requests as possible.

    `build(bbox)` must be the exact query the planner sends for one tile (the
    cache is keyed by its text); `build_big(bbox)` is the same query with a
    longer timeout, sent for a block of tiles. A block the service refuses is
    halved and retried, down to single tiles. `progress(n_tiles_done)` is
    called as blocks finish. `max_block` caps the first requests at that
    many tiles a side, for areas too big to ask for at once (a whole
    country). `workers` blocks are downloaded at once, spread over the
    mirrors that answer. Returns how many tiles could not be fetched.
    """
    tiles = [t for t in tiles if not is_cached(build(tile_bbox(t, deg)), TILE_TTL_S)]
    if not tiles:
        return 0
    failed = [0]
    count_lock = threading.Lock()
    local = threading.local()

    def fill(block):
        ys = [t[0] for t in block]
        xs = [t[1] for t in block]
        rect = [(iy, ix) for iy in range(min(ys), max(ys) + 1)
                for ix in range(min(xs), max(xs) + 1)]
        bbox = (min(ys) * deg, min(xs) * deg, (max(ys) + 1) * deg, (max(xs) + 1) * deg)
        qls = {t: build(tile_bbox(t, deg)) for t in rect}
        # Trips planned meanwhile wait for this download instead of
        # sending the same tiles to Overpass a second time.
        owned = []
        with _inflight_lock:
            for ql in qls.values():
                if ql not in _inflight:
                    _inflight[ql] = threading.Event()
                    owned.append(ql)
        try:
            data = query(build_big(bbox), timeout=PRELOAD_TIMEOUT_S + 60,
                         use_cache=False, allow_empty=True,
                         attempt_s=PRELOAD_TIMEOUT_S + 60,
                         prefer=getattr(local, "mirror", None))
            parts = split_by_tile(data, deg, kind)
            for tile, ql in qls.items():
                _cache_put(ql, {"elements": parts.get(tile, []), "complete": True})
            ok = True
        except OverpassError as exc:
            ok = False
            reason = str(exc).splitlines()[0]
        finally:
            with _inflight_lock:
                for ql in owned:
                    _inflight.pop(ql, None).set()
        if ok:
            if progress:
                progress(len(block))
            return
        if len(block) == 1:
            with count_lock:
                failed[0] += 1
            print("  preload: could not fetch tile %s (%s)" % (block[0], reason), flush=True)
            if progress:
                progress(1)
            return
        # Halve along the longer side and try each half.
        axis = 0 if (max(ys) - min(ys)) >= (max(xs) - min(xs)) else 1
        mid = (min(ys) + max(ys)) // 2 if axis == 0 else (min(xs) + max(xs)) // 2
        for half in ([t for t in block if t[axis] <= mid], [t for t in block if t[axis] > mid]):
            if half:
                fill(half)

    if max_block:
        groups: dict = {}
        for t in tiles:
            groups.setdefault((t[0] // max_block, t[1] // max_block), []).append(t)
        blocks = list(groups.values())
    else:
        blocks = [tiles]
    if workers <= 1 or len(blocks) == 1:
        for block in blocks:
            fill(block)
        return failed[0]

    def worker_fill(job):
        i, block = job
        live = [e for e in ENDPOINTS if _mirror_available(e)] or ENDPOINTS
        local.mirror = live[i % len(live)]
        fill(block)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(worker_fill, enumerate(blocks)))
    return failed[0]


# ------------------------------------------------------------------- parsing


def way_direction(tags: dict, oneway_keys) -> int:
    """+1 if a way may only be travelled in node order, -1 only against it,
    0 both ways. `oneway_keys` runs most specific first, so a vehicle
    exemption such as `oneway:motorcycle=no` beats a plain `oneway=yes`."""
    for key in oneway_keys:
        value = tags.get(key)
        if value is not None:
            value = value.strip().lower()
            if value in ("yes", "true", "1"):
                return 1
            if value in ("-1", "reverse"):
                return -1
            return 0   # "no", and time-dependent values such as "reversible"
    # One-way by definition even when untagged.
    if tags.get("junction") in ("roundabout", "circular") or tags.get("highway") == "motorway":
        return 1
    return 0


def parse_network(data: dict, oneway_keys=None):
    """Split an Overpass response into (nodes{id:(lat,lon)}, ways[[node ids]],
    directions[+1|0|-1 per way]). Directions are all 0 unless `oneway_keys` is
    given and the query was built with the same keys."""
    nodes: dict[int, tuple[float, float]] = {}
    ways: list[list[int]] = []
    way_ids: list[int] = []
    tags: dict[int, dict] = {}
    for el in data.get("elements", ()):
        kind = el.get("type")
        if kind == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])
        elif kind == "way":
            # A one-way query lists such ways twice: once with node refs, once
            # with tags only.
            if el.get("tags"):
                tags[el["id"]] = el["tags"]
            refs = el.get("nodes")
            if refs and len(refs) > 1:
                ways.append(refs)
                way_ids.append(el["id"])
    if oneway_keys:
        directions = [way_direction(tags.get(i, {}), oneway_keys) for i in way_ids]
    else:
        directions = [0] * len(ways)
    return nodes, ways, directions


# Lifecycle tags for a stop that is planned, being built or closed. Such
# stations are mapped ahead of time (Kochi's InfoPark station, for one), but
# no track reaches them yet, so a route that boards or alights there fails.
_NOT_YET = ("proposed", "construction", "planned")
_GONE = ("disused", "abandoned", "razed", "demolished")


def _in_service(tags: dict) -> bool:
    """False for a stop that is not open: proposed, under construction,
    closed, or with an opening date still in the future."""
    if tags.get("railway") in _NOT_YET or tags.get("public_transport") in _NOT_YET:
        return False
    for key in _NOT_YET + _GONE:
        value = tags.get(key)
        if value and value != "no":
            return False
        if any(k.startswith(key + ":") for k in tags):   # e.g. disused:railway=station
            if not any(k in tags for k in ("railway", "highway", "public_transport")):
                return False
    opening = (tags.get("opening_date") or "").strip()
    # ISO dates ("2026", "2026-12", "2026-12-01") compare correctly as text.
    if re.match(r"^\d{4}(-\d{2}){0,2}$", opening):
        if opening > time.strftime("%Y-%m-%d")[:len(opening)]:
            return False
    return True


# Words a stop's name does not need: the app already says what kind of stop
# it is ("Walk to Valarpadam", not "Walk to Valarpadam Railway Station").
_REDUNDANT_SUFFIX = re.compile(
    r"\s+(railway station|railway stn\.?|rly\.? station|metro station|bus stop)$", re.I)


def clean_name(tags: dict):
    """A readable stop name from OSM tags, or None.

    * "Thrissur;Canara Bank" - several names in one tag: the first is the stop.
    * a name only in Malayalam, Hindi... - the English one if there is one.
    * "Valarpadam Railway Station" -> "Valarpadam".
    * "aluva" -> "Aluva".
    """
    name = tags.get("name") or ""
    english = tags.get("name:en") or ""
    if english and not any("a" <= c.lower() <= "z" for c in name):
        name = english            # main name is in another script
    name = name.split(";")[0].strip() or english.split(";")[0].strip()
    if not name:
        name = (tags.get("ref") or tags.get("local_ref") or "").split(";")[0].strip()
    name = " ".join(name.split())
    trimmed = _REDUNDANT_SUFFIX.sub("", name).strip()
    if trimmed:
        name = trimmed
    if name and name == name.lower():
        name = name.title()
    return name or None


def parse_stops(data: dict):
    """Normalise stop elements into dicts with a guaranteed lat/lon."""
    out = []
    seen = set()
    for el in data.get("elements", ()):
        if "lat" in el and "lon" in el:
            lat, lon = el["lat"], el["lon"]
        elif "center" in el:
            lat, lon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        key = (el.get("type"), el.get("id"))
        if key in seen:
            continue
        seen.add(key)
        tags = el.get("tags") or {}
        if not _in_service(tags):
            continue
        etype = el.get("type") or "node"
        out.append(
            {
                "id": "{}/{}".format(etype, el.get("id")),
                "osm_type": etype,
                "osm_id": el.get("id"),
                "lat": lat,
                "lon": lon,
                "name": clean_name(tags),
                # A bus stand or station is where long-distance buses call.
                "kind": "station" if (tags.get("amenity") == "bus_station"
                                      or tags.get("public_transport") == "station"
                                      or tags.get("railway") == "station") else "stop",
                "operator": tags.get("operator"),
                "network": tags.get("network"),
                "ref": tags.get("ref"),
            }
        )
    return out


def geocode(text: str, limit: int = 6):
    """Forward-geocode a place name through Nominatim."""
    params = urllib.parse.urlencode(
        {"q": text, "format": "jsonv2", "limit": str(limit), "addressdetails": "0"}
    )
    req = urllib.request.Request(
        NOMINATIM + "?" + params,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
    )
    _throttle()
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    results = json.loads(raw.decode("utf-8", "replace"))
    return [
        {
            "name": r.get("display_name"),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "kind": r.get("type"),
        }
        for r in results
    ]
