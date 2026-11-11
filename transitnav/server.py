"""A dependency-free HTTP server: static files, a JSON API and an SSE feed.

Everything runs on the standard library so the app starts with nothing more
than a Python install.
"""

from __future__ import annotations

import gzip
import hmac
import json
import math
import mimetypes
import os
import posixpath
import threading
import time
import traceback
import urllib.parse
from email.utils import formatdate, parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import (basemap, feedback, limits, locate, overpass, places, preload, router,
               settings, contact, livetrains, stopnames, tickets, timings, userplaces)
from .config import ACCESS_MODES, MODES
from .planner import PlanError, plan, prefetch

GZIP_MIN = 64 * 1024
# Largest JSON body accepted (riders' reports, the scripted /api/plan).
MAX_BODY = 4096
MAX_QUERY = 120          # characters in a place search

# Sent with every response. The policy allows only Vazhi's own scripts (the
# map libraries are in web/vendor), Google's web fonts, the blob: worker the
# map draws with and the blob: alarm siren (alerts.js builds the sound in the
# page), and Razorpay Checkout for bus tickets (its script and payment
# window); nothing may frame the app (clickjacking).
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://checkout.razorpay.com https://cdn.razorpay.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob: https://cdn.razorpay.com",
    "connect-src 'self' https://api.razorpay.com https://lumberjack.razorpay.com",
    "frame-src https://api.razorpay.com https://checkout.razorpay.com",
    "media-src 'self' blob:",
    "worker-src 'self' blob:",
    "child-src blob:",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=(), payment=()",
    # allow-popups: card payments may open the bank's page in a popup
    # that Razorpay's window must keep talking to.
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
}
OOPS = "Something went wrong on our side. Please try again."
mimetypes.add_type("application/geo+json", ".geojson")
_gz_cache = {}          # path -> (mtime, gzipped bytes)
_search_cache = {}      # (query, full, near) -> (time, answer)
SEARCH_CACHE_S = 600
SEARCH_CACHE_MAX = 3000
_gz_lock = threading.Lock()


def _gzipped(path: str, body: bytes) -> bytes:
    """`body` gzipped, compressed once per version of the file."""
    mtime = os.path.getmtime(path)
    with _gz_lock:
        hit = _gz_cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
    data = gzip.compress(body, 6)
    with _gz_lock:
        _gz_cache[path] = (mtime, data)
    return data

NL = chr(10)

# Not in every system's MIME table; browsers want this type for the app manifest.
mimetypes.add_type("application/manifest+json", ".webmanifest")



def _byte_range(header, size):
    """(start, end) for a single "bytes=a-b" range, None for no range (send
    it all), "bad" for a range outside the file."""
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    first, _, last = header[6:].strip().partition("-")
    try:
        if first == "":                       # "bytes=-500": the last 500 bytes
            n = int(last)
            if n <= 0:
                return "bad"
            return max(0, size - n), size - 1
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return None
    if start >= size or end < start:
        return "bad"
    return start, min(end, size - 1)

class ClientGone(Exception):
    """The browser closed the stream before the plan finished."""


WEB_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def _prefetch(point, mode):
    """Warm the tile cache around a point the user just placed. A failure here
    costs nothing: the plan itself will simply download what it needs."""
    try:
        prefetch(point, mode)
    except Exception:  # noqa: BLE001
        pass
    finally:
        limits.prefetch_slots.release()


def _point(lat, lon):
    """(lat, lon) as finite floats on the globe, or PlanError."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise PlanError("Missing or malformed coordinates in the request.") from None
    # float() happily parses "nan" and "inf", which no map position is.
    if not (math.isfinite(lat) and math.isfinite(lon)
            and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise PlanError("Coordinates must be a latitude between -90 and 90 and a "
                        "longitude between -180 and 180.")
    return lat, lon


def _log_plan(mode, access, outcome, started):
    """One line per trip plan: what kind, how it ended, how long it took
    (never where from or to)."""
    print("%s plan %s/%s %s %.1fs" % (time.strftime("%Y-%m-%d %H:%M:%S"), mode, access,
                                       outcome, time.time() - started), flush=True)


def _check_modes(mode, access):
    if mode not in MODES:
        raise PlanError("Unknown travel mode.")
    if access not in ACCESS_MODES:
        raise PlanError("Unknown way of reaching the stop.")


def _coord(params, lat_key, lon_key):
    try:
        return _point(params[lat_key][0], params[lon_key][0])
    except (KeyError, IndexError):
        raise PlanError("Missing or malformed coordinates in the request.") from None


class Handler(BaseHTTPRequestHandler):
    server_version = "Vazhi"
    sys_version = ""                 # do not advertise the Python version
    protocol_version = "HTTP/1.1"
    timeout = 60                     # a silent connection is dropped after a minute

    # Set once a response has begun, so error handling never tries to write a
    # second set of headers down a stream that is already open (or gone).
    started = False

    # Logs: one line per trip plan, contribution or failed request - not per
    # search keystroke, map tile or file, which under load cost more to print
    # than to answer. No addresses or coordinates are logged.
    LOGGED = ("/api/plan", "/api/timings", "/api/stops/rename", "/api/stops/add", "/api/places/add",
              "/api/stops/answer", "/api/feedback", "/api/ticket/order", "/api/ticket/paid")

    def log_request(self, code="-", size="-"):
        path = (self.path or "").split("?")[0]
        try:
            failed = int(code) >= 400
        except (TypeError, ValueError):
            failed = False
        if failed or (path in self.LOGGED and self.command == "POST"):
            print("%s %s %s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), self.command, path, code),
                  flush=True)

    def log_message(self, fmt, *args):
        print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), fmt % args), flush=True)

    # ------------------------------------------------------------- plumbing

    def end_headers(self):
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        super().end_headers()

    def _send(self, code, body: bytes, ctype: str, extra=None, cache="no-store"):
        self.started = True
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _limit(self, kind):
        """Count this request against the visitor's limit; False (after
        answering 429) when they are over it."""
        try:
            limits.hit(kind, self._client())
            return True
        except limits.Limited as exc:
            self._send(429, json.dumps({"error": str(exc)}).encode("utf-8"),
                       "application/json; charset=utf-8",
                       {"Retry-After": str(exc.retry_after)})
            return False

    def _same_site(self):
        """A post must come from Vazhi's own pages: JSON (which a form on
        another site cannot send without the browser asking first), and, when
        the browser names the page it came from, this site (CSRF)."""
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True        # scripts and curl; browsers send Origin with every post
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        return urllib.parse.urlparse(origin).netloc == host

    def _body(self):
        """The request's JSON object, at most MAX_BODY bytes; ValueError if not."""
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY:
            raise ValueError("too large")
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(body, dict):
            raise ValueError("not an object")
        return body

    def _json(self, code, payload):
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _redirect(self, where: str):
        self._send(302, b"", "text/plain; charset=utf-8", {"Location": where})

    def _static(self, path: str):
        rel = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
        if rel in ("", "."):
            rel = "index.html"
        full = os.path.normpath(os.path.join(WEB_ROOT, rel))
        # commonpath, not a string prefix: "web-old" starts with "web" too.
        inside = os.path.commonpath([full, WEB_ROOT]) == WEB_ROOT
        if inside and rel.startswith("fonts/") and rel.endswith(".pbf") and not os.path.isfile(full):
            # Map letters come in blocks of 256 characters; only the blocks
            # Kerala's names use are kept (web/fonts/README.md). Any other
            # block is answered as empty, so the map leaves those letters out
            # instead of failing the whole label.
            self._send(200, b"", "application/x-protobuf")
            return
        if not inside or not os.path.isfile(full):
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        # Files keep their names between versions, so browsers keep them but
        # check back each time; an unchanged file costs a tiny 304 answer.
        mtime = int(os.path.getmtime(full))
        since = self.headers.get("If-Modified-Since")
        if since:
            try:
                if int(parsedate_to_datetime(since).timestamp()) >= mtime:
                    self.started = True
                    self.send_response(304)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Last-Modified", formatdate(mtime, usegmt=True))
                    self.end_headers()
                    return
            except (TypeError, ValueError, OverflowError):
                pass
        with open(full, "rb") as fh:
            body = fh.read()
        extra = {"Last-Modified": formatdate(mtime, usegmt=True)}
        if rel.startswith("icons/"):
            # Razorpay's payment window loads the Vazhi logo from its own site.
            extra["Access-Control-Allow-Origin"] = "*"
        if ctype.startswith(("video/", "audio/")):
            # Video players (Safari above all) ask for pieces: "Range: bytes=0-1".
            extra["Accept-Ranges"] = "bytes"
            part = _byte_range(self.headers.get("Range"), len(body))
            if part == "bad":
                self._send(416, b"", ctype, {"Content-Range": "bytes */%d" % len(body)})
                return
            if part:
                start, end = part
                extra["Content-Range"] = "bytes %d-%d/%d" % (start, end, len(body))
                self._send(206, body[start:end + 1], ctype, extra, cache="no-cache")
                return
        # Big map data (the 1.8 MB stop list) goes over the phone's data
        # connection gzipped: about an eighth of the size.
        if len(body) > GZIP_MIN and "gzip" in (self.headers.get("Accept-Encoding") or "")                 and (ctype.startswith("text/") or "json" in ctype or rel.endswith(".geojson")):
            body = _gzipped(full, body)
            extra.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        self._send(200, body, ctype, extra, cache="no-cache")

    def _inbox(self):
        """Riders' messages, ratings and tickets for the owner's Messages app
        (tools/inbox.py), which may run on another computer. Only with the
        password in VAZHI_INBOX_TOKEN; without one set, there is no inbox."""
        token = settings.get("VAZHI_INBOX_TOKEN")
        if len(token) < 24:
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        if not self._limit("inbox"):
            return
        given = (self.headers.get("Authorization") or "")
        given = given[7:].strip() if given[:7].lower() == "bearer " else ""
        if not hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8")):
            self._json(401, {"error": "Wrong password."})
            return
        self._json(200, feedback.inbox())

    def _policy(self, filename):
        """Privacy, terms, refunds and contact, with the owner's contact
        details from data/vazhi.env filled in (contact.py)."""
        with open(os.path.join(WEB_ROOT, filename), encoding="utf-8") as fh:
            page = contact.fill(fh.read())
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", cache="no-cache")

    def _search(self, params):
        """Places for the destination box, as the user types.

        The local index of every named place in the Kerala map answers at
        once. The online search (Nominatim) is asked only when the local
        one finds little - a place outside Kerala, or before the index is
        built - or when the user presses Enter (`full=1`)."""
        q = (params.get("q") or [""])[0].strip()[:MAX_QUERY]
        if len(q) < 2:
            self._json(200, {"results": []})
            return
        near = None
        try:
            near = (float(params["lat"][0]), float(params["lon"][0]))
            if not (math.isfinite(near[0]) and math.isfinite(near[1])):
                near = None
        except (KeyError, IndexError, ValueError):
            near = None
        full = (params.get("full") or ["0"])[0] == "1"
        # Many riders type the same first letters near the same places: keep
        # recent answers (keyed to about a kilometre) for ten minutes.
        key = (q.lower(), full, near and (round(near[0], 2), round(near[1], 2)))
        hit = _search_cache.get(key)
        if hit and time.time() - hit[0] < SEARCH_CACHE_S:
            self._json(200, hit[1])
            return
        results = places.search(q, near=near, limit=8)
        # Places riders added (userplaces.py): an exact start of the name first.
        added = userplaces.search(q, near=near)
        results = [r for r in added if r["name"].lower().startswith(q.lower())] + results +                   [r for r in added if not r["name"].lower().startswith(q.lower())]
        if len(results) < 3 or full:
            try:
                online = overpass.geocode(places.clean_query(q)[0] or q, limit=6, near=near)
            except Exception:  # noqa: BLE001 - offline: the local results stand
                online = []
            for r in online:
                # Skip what the local index already listed at about the same spot.
                if not any(r["name"].lower() == l["name"].lower()
                           and abs(r["lat"] - l["lat"]) < 0.01 and abs(r["lon"] - l["lon"]) < 0.01
                           for l in results):
                    results.append(r)
        answer = {"results": results[:10]}
        with _gz_lock:
            if len(_search_cache) >= SEARCH_CACHE_MAX:
                _search_cache.pop(next(iter(_search_cache)))
            _search_cache[key] = (time.time(), answer)
        self._json(200, answer)

    def _tile(self, route):
        """/tiles/{z}/{x}/{y}.pbf from the local map (see basemap.py)."""
        try:
            z, x, y = (int(v) for v in route[len("/tiles/"):].rsplit(".", 1)[0].split("/"))
        except ValueError:
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        data = basemap.tile(z, x, y)
        if data is None:
            self._send(204, b"", "application/x-protobuf")   # empty sea or outside the map
            return
        extra = {"Cache-Control": "public, max-age=86400"}
        if data[:2] == bytes((0x1F, 0x8B)):   # gzip magic number
            extra["Content-Encoding"] = "gzip"
        self.started = True
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", str(len(data)))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    # --------------------------------------------------------------- routes

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route, params = parsed.path, urllib.parse.parse_qs(parsed.query)
        try:
            if route == "/api/modes":
                self._json(200, {"modes": [
                    {"id": k, "label": v["label"], "color": v["color"],
                     "stop_noun": v["stop_noun"], "max_trip_m": v.get("long_trip_m", v["max_trip_m"])}
                    for k, v in MODES.items()],
                    "access": [{"id": k, "label": v["label"], "speed_kmh": v["speed_kmh"]}
                               for k, v in ACCESS_MODES.items()]})
            elif route == "/api/search":
                if self._limit("search"):
                    self._search(params)
            elif route == "/api/plan":
                self._plan_stream(params)
            elif route == "/api/health":
                # For uptime monitors: is the app answering, and are its parts there?
                self._json(200, {"ok": True, "map": basemap.newest_build() is not None,
                                 "route_service": router.local_available(),
                                 "time": int(time.time())})
            elif route == "/api/support":
                # What "Buy me a bus ticket" can offer (UPI ID, payment link).
                self._json(200, feedback.support())
            elif route == "/api/mapinfo":
                self._json(200, basemap.info())
            elif route.startswith("/tiles/"):
                self._tile(route)
            elif route == "/api/whereami":
                # Only used when the browser will not share its position.
                # It looks up this server's own connection, which says
                # nothing about a visitor coming through the tunnel or the
                # cloud server's web server.
                if self._client() != self.client_address[0]:
                    self._json(503, {"error": "Allow location access to see where you are."})
                    return
                try:
                    self._json(200, locate.whereami())
                except RuntimeError as exc:
                    self._json(503, {"error": "Could not work out your area from your "
                                              "internet connection (%s)." % exc})
            elif route == "/api/status":
                with preload._lock:
                    self._json(200, {"preload": dict(preload.status)})
            elif route == "/api/timings":
                board = _coord(params, "blat", "blon")
                alight = _coord(params, "alat", "alon")
                device = (params.get("device") or [""])[0][:64]
                self._json(200, {"times": timings.near(board, alight, device)})
            elif route == "/api/stops/ask":
                # Questions for this rider about the stops of their trip.
                keys = [k for k in (params.get("keys") or [""])[0].split(",") if k][:6]
                points = []
                for pair in (params.get("points") or [""])[0].split(";")[:6]:
                    try:
                        points.append(_point(*pair.split(",")))
                    except (PlanError, TypeError):
                        pass
                device = (params.get("device") or [""])[0][:64]
                keys = [k[:80] for k in keys]
                self._json(200, {"questions": stopnames.ask(keys, points, device)})
            elif route == "/api/prefetch":
                if not self._limit("prefetch"):
                    return
                point = _coord(params, "lat", "lon")
                mode = (params.get("mode") or ["bus"])[0]
                if mode not in MODES:
                    raise PlanError("Unknown mode.")
                # A nicety: when enough already run, this one is skipped.
                if limits.prefetch_slots.acquire(blocking=False):
                    threading.Thread(target=_prefetch, args=(point, mode), daemon=True).start()
                self._json(202, {"ok": True})
            elif route == "/api/trains/live":
                if not self._limit("trainlive"):
                    return
                try:
                    self._json(200, livetrains.live((params.get("number") or [""])[0]))
                except livetrains.LiveError as exc:
                    self._json(400, {"error": str(exc)})
            elif route == "/api/inbox":
                self._inbox()
            elif route == "/" and ("from" in params or "to" in params):
                # A trip shared before the app moved to /app.
                self._redirect("/app?" + parsed.query)
            elif route == "/":
                self._static("home.html")          # the Vazhi website
            elif route == "/app/":
                self._redirect("/app" + ("?" + parsed.query if parsed.query else ""))
            elif route == "/app":
                self._static("index.html")         # the app itself
            elif route in contact.PAGES or route[:-5] in contact.PAGES and route.endswith(".html"):
                self._policy(contact.PAGES[route if route in contact.PAGES else route[:-5]])
            else:
                self._static(route)
        except PlanError as exc:
            if not self.started:
                self._json(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True   # the client went away; nothing to report
        except Exception:  # noqa: BLE001 - details go to the log, not the visitor
            traceback.print_exc()
            if not self.started:
                self._json(500, {"error": OOPS})

    def _plan_stream(self, params):
        """Plan a journey, streaming progress as Server-Sent Events.

        Fetching a city's network from Overpass can take a minute or more, so
        the client gets a running commentary instead of a frozen spinner.
        """
        origin = _coord(params, "olat", "olon")
        destination = _coord(params, "dlat", "dlon")
        mode = (params.get("mode") or ["bus"])[0]
        access = (params.get("access") or ["walk"])[0]
        _check_modes(mode, access)

        self.started = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(event, payload):
            chunk = "event: %s\ndata: %s\n\n" % (event, json.dumps(payload))
            try:
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                raise ClientGone() from None

        def report(stage, message):
            emit("progress", {"stage": stage, "message": message})

        started = time.time()

        # A browser that leaves mid-plan surfaces as ClientGone from whichever
        # emit() notices first - including the one reporting an error - so it
        # is caught once, around everything, and never logged as a crash.
        try:
            try:
                emit("progress", {"stage": "start", "message": "Planning your journey"})
                try:
                    limits.hit("plan", self._client())
                except limits.Limited:
                    raise PlanError("That is a lot of trips in a few minutes. Please wait a "
                                    "little and try again.", title="Too many trips at once") from None
                if not limits.plan_slots.acquire(timeout=0.05):
                    report("start", "Many trips are being planned right now - yours is next")
                    if not limits.plan_slots.acquire(timeout=limits.PLAN_WAIT_S):
                        raise PlanError("Vazhi is very busy right now. Please try again in a "
                                        "minute.", title="Vazhi is busy")
                try:
                    result = plan(origin, destination, mode, report, access)
                finally:
                    limits.plan_slots.release()
                _log_plan(mode, access, "ok", started)
                emit("result", result)
            except (PlanError, overpass.OverpassError) as exc:
                _log_plan(mode, access, "refused (%s)" % (getattr(exc, "title", None) or "no route"),
                          started)
                emit("error", {"error": str(exc), "title": getattr(exc, "title", None),
                               "actions": getattr(exc, "actions", [])})
            except ClientGone:
                raise
            except Exception:  # noqa: BLE001 - details go to the log, not the visitor
                traceback.print_exc()
                _log_plan(mode, access, "FAILED", started)
                emit("error", {"error": OOPS})
        except ClientGone:
            _log_plan(mode, access, "left early", started)

    def _client(self):
        """Who is asking, for rate limits only (never stored). Through the
        phone-link tunnel every request arrives from this machine, and the
        tunnel names the real client in CF-Connecting-IP; on the cloud server
        the web server in front (Caddy) names it in X-Forwarded-For."""
        addr = self.client_address[0]
        if addr in ("127.0.0.1", "::1"):
            if self.headers.get("CF-Connecting-IP"):
                return self.headers["CF-Connecting-IP"]
            forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if forwarded:
                return forwarded
        return addr

    def _timings_post(self, route):
        """Riders' contributions: bus times (timings.py), and stop names and
        missing stops (stopnames.py)."""
        try:
            body = self._body()
            client = self._client()
            if route == "/api/timings":
                self._json(200, {"ok": True, "id": timings.add(body, client)})
            elif route == "/api/timings/vote":
                timings.vote(body, client)
                self._json(200, {"ok": True})
            elif route == "/api/stops/rename":
                self._json(200, {"ok": True, "names": stopnames.rename(body, client),
                                 "needed": stopnames.CONFIRM})
            elif route == "/api/places/add":
                self._json(200, dict(userplaces.add(body, client), ok=True))
                _search_cache.clear()          # found by the next search at once
            elif route == "/api/stops/add":
                self._json(200, dict(stopnames.add(body, client), ok=True, needed=stopnames.CONFIRM))
            else:   # /api/stops/answer
                stopnames.answer(body, client)
                self._json(200, {"ok": True})
        except timings.TimingError as exc:
            self._json(400, {"error": str(exc)})
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "Malformed request."})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": OOPS})

    def _feedback_post(self):
        """A rider's message to the maker (feedback.py)."""
        try:
            self._json(200, {"ok": True, "id": feedback.add(self._body(), self._client())})
        except feedback.FeedbackError as exc:
            self._json(400, {"error": str(exc)})
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "Malformed request."})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": OOPS})

    def _ticket_post(self, path):
        """Buy me a bus ticket: a Razorpay order, then the checked payment (tickets.py)."""
        try:
            body = self._body()
            if path == "/api/ticket/order":
                self._json(200, tickets.order(body, self._client()))
            else:
                self._json(200, tickets.verify(body))
        except tickets.TicketError as exc:
            self._json(400, {"error": str(exc)})
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "Malformed request."})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": OOPS})

    def do_POST(self):
        """Riders' bus times, and a non-streaming /api/plan for scripts and curl."""
        parsed = urllib.parse.urlparse(self.path)
        if not self._same_site():
            self._json(403, {"error": "Requests must be JSON from Vazhi's own pages."})
            return
        if parsed.path in ("/api/timings", "/api/timings/vote", "/api/stops/rename",
                           "/api/stops/add", "/api/stops/answer", "/api/places/add"):
            self._timings_post(parsed.path)
            return
        if parsed.path in ("/api/ticket/order", "/api/ticket/paid"):
            self._ticket_post(parsed.path)
            return
        if parsed.path == "/api/feedback":
            self._feedback_post()
            return
        if parsed.path == "/api/trains/report":
            try:
                self._json(200, {"ok": True, "kept": livetrains.report(self._body(), self._client())})
            except livetrains.LiveError as exc:
                self._json(400, {"error": str(exc)})
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                self._json(500, {"error": OOPS})
            return
        if parsed.path != "/api/plan":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        if not self._limit("plan"):
            return
        try:
            body = self._body()
            origin = _point(body["origin"]["lat"], body["origin"]["lon"])
            destination = _point(body["destination"]["lat"], body["destination"]["lon"])
            mode = body.get("mode", "bus")
            access = body.get("access", "walk")
            _check_modes(mode, access)
        except PlanError as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception:  # noqa: BLE001
            self._json(400, {"error": "Body must be {origin:{lat,lon}, destination:{lat,lon}, "
                                      "mode, access?}"})
            return
        if not limits.plan_slots.acquire(timeout=limits.PLAN_WAIT_S):
            self._json(503, {"error": "Vazhi is very busy right now. Please try again in a minute."})
            return
        try:
            self._json(200, plan(origin, destination, mode, access=access))
        except (PlanError, overpass.OverpassError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": OOPS})
        finally:
            limits.plan_slots.release()

    def do_HEAD(self):
        """Uptime monitors often check with HEAD; answer as GET, without a body."""
        self.do_GET()


class Server(ThreadingHTTPServer):
    # Connections waiting to be accepted. Python's default of 5 refuses
    # visitors as soon as a few dozen arrive at once.
    request_queue_size = 256


def serve(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    httpd = Server((host, port), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd
