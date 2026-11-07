"""Roughly where the user is, when the browser may not say.

The page asks the browser for the user's position first. If that is refused
or unavailable, it asks this module, which:

  1. looks up the public IP address of this computer's internet connection
     with a free IP-location service (ipwho.is, falling back to ipapi.co) -
     good to the nearest town, sometimes only the region, and
  2. asks OpenStreetMap's Nominatim which district contains that point,
     with the district's outline, so the page can show "you are around
     Kottayam district" rather than pretend to know the street.

Note that step 1 sends the connection's IP address to that service; nothing
else about the user is sent. The answer is kept for half an hour.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.parse
import urllib.request

from . import overpass

IP_SERVICES = (
    # (url, lat key, lon key, city key, region key, country key)
    ("https://ipwho.is/", "latitude", "longitude", "city", "region", "country"),
    ("https://ipapi.co/json/", "latitude", "longitude", "city", "region", "country_name"),
)
REVERSE = "https://nominatim.openstreetmap.org/reverse"
DISTRICT_ZOOM = 7          # Nominatim's level for a district (India: state_district)
CACHE_S = 30 * 60
TIMEOUT_S = 8

_cache = {"at": 0.0, "value": None}
_lock = threading.Lock()


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": overpass.USER_AGENT,
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "replace"))


def _ip_location():
    """(lat, lon, city, region, country) from the first service that answers."""
    errors = []
    for url, lat_k, lon_k, city_k, region_k, country_k in IP_SERVICES:
        try:
            d = _get_json(url)
            if d.get("success") is False or d.get("error"):
                raise ValueError(d.get("message") or d.get("reason") or "no answer")
            lat, lon = float(d[lat_k]), float(d[lon_k])
            return lat, lon, d.get(city_k), d.get(region_k), d.get(country_k)
        except Exception as exc:  # noqa: BLE001 - try the next service
            errors.append("%s: %s" % (url.split("/")[2], exc))
    raise RuntimeError("; ".join(errors))


def _district(lat, lon):
    """Nominatim's district (or nearest equivalent area) holding the point."""
    q = urllib.parse.urlencode({"format": "jsonv2", "lat": "%.5f" % lat, "lon": "%.5f" % lon,
                                "zoom": DISTRICT_ZOOM, "polygon_geojson": 1,
                                "polygon_threshold": 0.003})
    overpass._throttle()   # Nominatim asks for at most one request a second
    return _get_json(REVERSE + "?" + q)


def whereami() -> dict:
    """{"lat", "lon", "district", "state", "country", "city", "outline",
    "bbox", "source"} for this internet connection. Raises RuntimeError if
    no service answers."""
    with _lock:
        if _cache["value"] and time.time() - _cache["at"] < CACHE_S:
            return _cache["value"]
    lat, lon, city, region, country = _ip_location()
    out = {"lat": lat, "lon": lon, "city": city, "state": region, "country": country,
           "district": None, "outline": None, "bbox": None,
           "source": "your internet connection (IP address)"}
    try:
        d = _district(lat, lon)
        addr = d.get("address") or {}
        out["district"] = (addr.get("state_district") or addr.get("county")
                           or d.get("name") or city)
        out["state"] = addr.get("state") or region
        out["country"] = addr.get("country") or country
        if d.get("geojson", {}).get("type") in ("Polygon", "MultiPolygon"):
            out["outline"] = d["geojson"]
        if d.get("boundingbox"):
            s, n, w, e = (float(v) for v in d["boundingbox"])
            out["bbox"] = [s, w, n, e]
    except Exception:  # noqa: BLE001 - the town from the IP lookup will do
        out["district"] = city
    with _lock:
        _cache.update(at=time.time(), value=out)
    return out
