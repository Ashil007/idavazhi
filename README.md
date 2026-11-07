# OSM Transit Navigator

Public-transport journey planning on OpenStreetMap. Pick two points on the map,
choose **bus**, **train** or **metro**, choose whether you get to and from the
stops on foot or by **two-wheeler**, and the app works out:

1. the shortest walk or ride from your start to the nearest stop of that mode,
2. the vehicle route between stops, traced over the real network — roads for a
   bus, heavy rail for a train, subway/light-rail track for a metro — listing
   every stop passed on the way, and
3. the shortest walk or ride from the arrival stop to your destination.

Every leg is a real shortest path over OSM data, not a straight line between
markers. Walking, two-wheeler and bus legs come from a public street router
(Valhalla, with OSRM as a fallback), which answers in under a second; metro and train legs are A\* searches
over track downloaded from Overpass and cached in map tiles. Two-wheeler legs
use roads only and obey one-way streets; walking legs may use footpaths.

## Running it

Requires **Python 3.9+** and nothing else — no `pip install`, no build step.

```bash
python serve.py
```

Then open <http://127.0.0.1:8000>. Useful flags: `--port 8001`, `--host 0.0.0.0`,
`--no-browser`. On Windows you can double-click `run.bat`.

### Offline region data

On start the server downloads every metro station, railway station, bus stop
and all metro and railway track for a region (Kerala by default) and saves it
in `.cache/`. Trips inside the region then plan without contacting Overpass. The
first download takes a while because Overpass is a busy shared service. The
page shows its progress. After that, a restart only re-fetches data older than
30 days.

```bash
python serve.py --region kochi             # or bengaluru, chennai, hyderabad, mumbai, delhi, kolkata
python serve.py --region 9.8,76.1,10.3,76.5 # any south,west,north,east box
python serve.py --no-preload               # download only what each trip needs
python -m transitnav.preload kerala        # preload without starting the server
python -m transitnav.preload timetable     # just the train timetable (below)
```

### Own map and routing server (optional, needs Docker Desktop)

The map is drawn in a plain, delivery-app style (`web/basemap.js`) from
OpenStreetMap vector tiles. By default they come from
[OpenFreeMap](https://openfreemap.org), which is free and needs no key. For
Kerala you can build your own tiles from an OpenStreetMap extract, so the map
is served from this computer:

```bash
python tools/build_map.py           # Kerala extract -> .cache/map/kerala-<date>.mbtiles (tilemaker)
python tools/routing_server.py start  # your own Valhalla routing server on port 8002
```

With the routing server running, walking, riding and bus routes in Kerala are
answered by this computer in milliseconds. The app asks it first and falls
back to the public servers elsewhere, or when it is not running.

### Long-distance trains

Train trips up to 300 km follow the OSM track around the trip, as above.
Longer ones, up to 3,500 km, are planned from the Indian Railways timetable:
real trains, their departure and arrival times, and at most one change (with
at least 40 minutes to make it). The timetable is the open
[datameet/railways](https://github.com/datameet/railways) dataset (CC0). It is
downloaded once at start-up, about 100 MB, and indexed into a few MB in
`.cache/timetable/`. After that, a search takes well under a second
(`transitnav/timetable.py`).

- The data dates from 2016. Trains have changed since, and it has no running
  days, so every train is treated as daily. Check the train on IRCTC or NTES
  before you travel.
- The total time counts from when the first train leaves. Without a departure
  time, the wait for the first train is not included.
- Seasonal "special" trains and ladies-only locals are left out.
- A station beyond walking range (3 km on a long trip) or riding range is
  reached by "bus or taxi", up to 60 km, at about 30 km/h.

Roads are not preloaded. Walking, riding and bus legs come from a street
routing server that already holds every road and answers in under a second.
Downloading a whole state's roads from Overpass would take hours and hundreds
of megabytes.

## Using it

When the page opens it asks the browser for your location. If you allow it,
the map zooms to you and your position becomes the start point; the
**Locate** button does the same again later. If you refuse, the app works out
your district from your internet connection and outlines it on the map. To do
that, the server sends your connection's public IP address to an
IP-location service (ipwho.is, or ipapi.co as a backup) and asks OpenStreetMap
Nominatim for the district boundary. Nothing else about you is sent. No
transport mode is selected at first, so pick Bus, Train or Metro before
planning.

- **Click the map** to place the highlighted point; the panel shows which one is
  active. Click a point row to re-target it, or drag a marker to move it.
- **Random** drops a point at a random spot inside the current view.
- **Find a place** searches by name (Nominatim) and moves the active point.
- Pick a **mode**, pick **Walk** or **Two-wheeler** for getting to and from the
  stops, and press **Plan journey**. Progress streams live while the
  map data downloads.

## How it works

| Stage | What happens |
|---|---|
| Stops | Every stop of the chosen mode in a corridor around the trip, loaded from fixed map tiles (0.25° for bus and metro, 1° for train). The same data supplies the boarding stop, the alighting stop and the intermediate stops. |
| Network | Metro and train track is loaded from the same tiles and turned into a weighted graph (nodes = OSM node ids, edge weight = metres). Buses need no download: their route comes from the street router. |
| Choosing stops | The stops nearest on the map are only a shortlist. Each is re-ranked by its real walking or riding distance from the street router, since a river or railway with few crossings can make the nearest-looking station a far longer walk. |
| Vehicle leg | Every pair of shortlisted stops joined by track or road is traced, and the pair with the shortest door-to-door time wins. For each pair, one A\* search starts from every track node near the boarding platform and ends at any track node near the alighting one. Union-find rejects unreachable pairs instantly. |
| Access legs | Both legs are asked of the street router at once (pedestrian profile for walking, motor-scooter profile for two-wheelers). If it does not answer, the footpath or road network around both ends is downloaded instead and routed locally, joining each end where it projects onto a nearby road. |
| Long rail trips | If no connection exists inside a corridor hugging the straight line, the track is fetched again over a corridor half the trip length wide - railways can bend far off the direct line (Kochi to Coimbatore runs about 57 km off it, via Shoranur). |

### Why each station is matched to several track nodes

Rail is mapped as one way *per direction*, so a station's closest track node is
often on the opposite-direction track, in a graph component that cannot reach
the destination. Each station is therefore snapped to several candidate track
nodes, and a single search runs from all of them at once, each costed by its
distance from the platform. Choosing the closest node first and routing second
fails at big stations: the nearest node can be on a track that needs a long
detour to reach. If the genuinely nearest stop sits on an isolated fragment,
the next one is used and the itinerary says so.

## Notes and limits

- **Times are estimates, not a timetable.** They come from distance, a typical
  speed per mode, a dwell allowance per intermediate stop and a nominal wait.
  The app does not read GTFS or route relations, so it does not know whether one
  vehicle actually runs end to end — it shows the infrastructure path between
  your nearest stops.
- **Quality follows OSM coverage.** Where the network is not mapped, planning
  fails with an explanation rather than inventing a route.
- **Planning is fast once an area's tiles are on disk** - typically a second or
  two, most of it the street router. The first plan in a new city waits for
  Overpass, a shared public service that can take from seconds to minutes
  under load. To hide that, the server preloads a whole region at start (see
  *Offline region data*), and outside it starts downloading an area's
  stations and track the moment you place a point. Tiles are kept in `.cache/` for 30 days,
  and an expired tile is still used if Overpass is down.
- **Street routing uses FOSSGIS's public servers**: Valhalla
  (valhalla1.openstreetmap.de) first, OSRM (routing.openstreetmap.de) if it
  fails. A server that stops answering is skipped for 5 minutes. If neither
  answers, the app falls back to downloading streets, which is slower.
- A walk or ride is only drawn as a straight line if no mapped way lies within
  600 m of its end point at all; it is then labelled "straight-line estimate".

## Layout

```
serve.py            entry point
transitnav/
  config.py         per-mode tags, speeds, search radii
  overpass.py       Overpass + Nominatim client, tile cache, mirror failover
  preload.py        downloads a region's stations and track to disk at start
  locate.py         your district from your internet connection (location fallback)
  basemap.py        serves the own Kerala map tiles (.mbtiles)
tools/
  build_map.py      builds the Kerala map tiles with tilemaker (Docker)
  routing_server.py starts/stops your own Valhalla routing server (Docker)
  timetable.py      Indian Railways timetable: download, index, train search
  router.py         street routing (Valhalla, OSRM) for walk, ride and bus legs
  geo.py            distances, bounding boxes, corridors, polyline maths
  graph.py          graph, spatial index, union-find, A*
  planner.py        the three-leg journey planner
  server.py         stdlib HTTP server, JSON API, SSE progress
web/                Leaflet front end
```

## API

```bash
curl -X POST http://127.0.0.1:8000/api/plan \
  -H "Content-Type: application/json" \
  -d '{"origin":{"lat":12.9784,"lon":77.6408},
       "destination":{"lat":12.9756,"lon":77.5726},
       "mode":"metro","access":"walk"}'
```

`access` is `walk` (default) or `two_wheeler`.
`GET /api/plan?olat=&olon=&dlat=&dlon=&mode=&access=` is the same planner as a
Server-Sent Events stream (`progress`, then `result` or `error`).
`GET /api/prefetch?lat=&lon=&mode=` starts caching the area around a point in
the background and returns at once. `GET /api/status` reports the region
preload (`state`, `done`, `total`, `current`).

Map data © OpenStreetMap contributors, ODbL.
