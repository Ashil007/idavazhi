"""A dependency-free HTTP server: static files, a JSON API and an SSE feed.

Everything runs on the standard library so the app starts with nothing more
than a Python install.
"""

from __future__ import annotations

import json
import math
import mimetypes
import os
import posixpath
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import basemap, locate, overpass, places, preload, stopnames, timings
from .config import ACCESS_MODES, MODES
from .planner import PlanError, plan, prefetch

NL = chr(10)

# Not in every system's MIME table; browsers want this type for the app manifest.
mimetypes.add_type("application/manifest+json", ".webmanifest")


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


def _coord(params, lat_key, lon_key):
    try:
        return _point(params[lat_key][0], params[lon_key][0])
    except (KeyError, IndexError):
        raise PlanError("Missing or malformed coordinates in the request.") from None


class Handler(BaseHTTPRequestHandler):
    server_version = "TransitNav/1.0"
    protocol_version = "HTTP/1.1"

    # Set once a response has begun, so error handling never tries to write a
    # second set of headers down a stream that is already open (or gone).
    started = False

    # Quieter logs: one line per API call, nothing for static assets.
    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            print("  %s %s" % (self.command, self.path.split("?")[0]))

    # ------------------------------------------------------------- plumbing

    def _send(self, code, body: bytes, ctype: str, extra=None):
        self.started = True
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

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
        if not inside or not os.path.isfile(full):
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _search(self, params):
        """Places for the destination box, as the user types.

        The local index of every named place in the Kerala map answers at
        once. The online search (Nominatim) is asked only when the local
        one finds little - a place outside Kerala, or before the index is
        built - or when the user presses Enter (`full=1`)."""
        q = (params.get("q") or [""])[0].strip()
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
        results = places.search(q, near=near, limit=8)
        if len(results) < 3 or (params.get("full") or ["0"])[0] == "1":
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
        self._json(200, {"results": results[:10]})

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
                self._search(params)
            elif route == "/api/plan":
                self._plan_stream(params)
            elif route == "/api/mapinfo":
                self._json(200, basemap.info())
            elif route.startswith("/tiles/"):
                self._tile(route)
            elif route == "/api/whereami":
                # Only used when the browser will not share its position.
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
                device = (params.get("device") or [""])[0]
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
                device = (params.get("device") or [""])[0]
                self._json(200, {"questions": stopnames.ask(keys, points, device)})
            elif route == "/api/prefetch":
                point = _coord(params, "lat", "lon")
                mode = (params.get("mode") or ["bus"])[0]
                if mode not in MODES:
                    raise PlanError("Unknown mode.")
                threading.Thread(target=_prefetch, args=(point, mode), daemon=True).start()
                self._json(202, {"ok": True})
            elif route == "/" and ("from" in params or "to" in params):
                # A trip shared before the app moved to /app.
                self._redirect("/app?" + parsed.query)
            elif route == "/":
                self._static("home.html")          # the Vazhi website
            elif route == "/app/":
                self._redirect("/app" + ("?" + parsed.query if parsed.query else ""))
            elif route == "/app":
                self._static("index.html")         # the app itself
            else:
                self._static(route)
        except PlanError as exc:
            if not self.started:
                self._json(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True   # the client went away; nothing to report
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            if not self.started:
                self._json(500, {"error": "%s: %s" % (type(exc).__name__, exc)})

    def _plan_stream(self, params):
        """Plan a journey, streaming progress as Server-Sent Events.

        Fetching a city's network from Overpass can take a minute or more, so
        the client gets a running commentary instead of a frozen spinner.
        """
        origin = _coord(params, "olat", "olon")
        destination = _coord(params, "dlat", "dlon")
        mode = (params.get("mode") or ["bus"])[0]
        access = (params.get("access") or ["walk"])[0]

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

        # A browser that leaves mid-plan surfaces as ClientGone from whichever
        # emit() notices first - including the one reporting an error - so it
        # is caught once, around everything, and never logged as a crash.
        try:
            try:
                emit("progress", {"stage": "start", "message": "Planning your journey"})
                result = plan(origin, destination, mode, report, access)
                emit("result", result)
            except (PlanError, overpass.OverpassError) as exc:
                emit("error", {"error": str(exc), "title": getattr(exc, "title", None),
                               "actions": getattr(exc, "actions", [])})
            except ClientGone:
                raise
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                emit("error", {"error": "Unexpected failure: %s: %s" % (type(exc).__name__, exc)})
        except ClientGone:
            print("  client left before the plan finished")

    def _client(self):
        """Who is asking, for rate limits only (never stored). Through the
        phone-link tunnel every request arrives from this machine, and the
        tunnel names the real client in CF-Connecting-IP."""
        addr = self.client_address[0]
        if addr in ("127.0.0.1", "::1") and self.headers.get("CF-Connecting-IP"):
            return self.headers["CF-Connecting-IP"]
        return addr

    def _timings_post(self, route):
        """Riders' contributions: bus times (timings.py), and stop names and
        missing stops (stopnames.py)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 4096:
                raise timings.TimingError("That request is too large.")
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise timings.TimingError("Malformed request.")
            client = self._client()
            if route == "/api/timings":
                self._json(200, {"ok": True, "id": timings.add(body, client)})
            elif route == "/api/timings/vote":
                timings.vote(body, client)
                self._json(200, {"ok": True})
            elif route == "/api/stops/rename":
                self._json(200, {"ok": True, "names": stopnames.rename(body, client),
                                 "needed": stopnames.CONFIRM})
            elif route == "/api/stops/add":
                self._json(200, dict(stopnames.add(body, client), ok=True, needed=stopnames.CONFIRM))
            else:   # /api/stops/answer
                stopnames.answer(body, client)
                self._json(200, {"ok": True})
        except timings.TimingError as exc:
            self._json(400, {"error": str(exc)})
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "Malformed request."})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": "%s: %s" % (type(exc).__name__, exc)})

    def do_POST(self):
        """Riders' bus times, and a non-streaming /api/plan for scripts and curl."""
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/api/timings", "/api/timings/vote", "/api/stops/rename",
                           "/api/stops/add", "/api/stops/answer"):
            self._timings_post(parsed.path)
            return
        if parsed.path != "/api/plan":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            origin = _point(body["origin"]["lat"], body["origin"]["lon"])
            destination = _point(body["destination"]["lat"], body["destination"]["lon"])
            mode = body.get("mode", "bus")
            access = body.get("access", "walk")
        except PlanError as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception:  # noqa: BLE001
            self._json(400, {"error": "Body must be {origin:{lat,lon}, destination:{lat,lon}, "
                                      "mode, access?}"})
            return
        try:
            self._json(200, plan(origin, destination, mode, access=access))
        except (PlanError, overpass.OverpassError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": str(exc)})


def serve(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd
