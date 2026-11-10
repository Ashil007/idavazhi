# Vazhi (വഴി)

*Vazhi* is Malayalam for "the way". Public-transport journey planning for
Kerala on OpenStreetMap data. Pick two points on the map,
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

### The website and the app

- `/` is the **Vazhi website**: what Vazhi is for, its features, screenshots,
  how it works, questions and answers, and two ways in: **Download the app**
  and **Use the web version**. The page is `web/home.html` (with `home.css`
  and `home.js`); the screenshots in `web/site/` are of the real app.
- `/app` is the **app itself** (`web/index.html`). The installed app opens
  here too (`start_url` in the manifest).
- Shared trips use `/app?from=...`. Older links (`/?from=...`) are sent on to
  `/app`.

**Download the app** installs Vazhi from the website, with no app store. On
Android, and in Chrome or Edge on a computer, it opens the browser's own Install
prompt. On an iPhone, or where the browser has no prompt, it shows the steps
instead (Share > Add to Home Screen, or the browser menu).

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

### The map and the routing server (needs Docker Desktop)

The map is drawn in a plain, delivery-app style (`web/basemap.js`) and every
part of it comes from this app's own server, with no outside map service:
vector tiles of Kerala built from an OpenStreetMap extract, the sea
(`web/map/sea.geojson`, built from the extract's coastline) and the label
fonts (`web/fonts/`, Noto Sans). Around Kerala it shows the main roads,
cities and state borders from Natural Earth (public domain,
`web/map/region.geojson`). Until the tiles are built the map shows only land
and sea. The Kerala data is OpenStreetMap's, so its credit shows when the map
opens and then folds into an (i) button.

```bash
python tools/build_map.py           # Kerala extract -> .cache/map/kerala-<date>.mbtiles (tilemaker) + the sea
python tools/build_sea.py           # just the sea, from the extract already downloaded
python tools/build_region.py        # just the roads and cities around Kerala
python tools/build_transit.py       # just the "All stops" layer (every stop and station)
python tools/routing_server.py start  # your own Valhalla routing server on port 8002
```

With the routing server running, walking, riding and bus routes in Kerala are
answered by this computer in milliseconds. The app asks it first and falls
back to the public servers elsewhere, or when it is not running.

### On your phone (trip alarm)

**Location and alarms are required.** Until both are allowed, a screen covers
the app and asks for them (`web/permit.js`): location to find the nearest stop
and follow the bus, notifications so the alarm wakes you with the screen off.
A phone asks only once, so after **Block** the screen explains how to allow
them in settings and checks again when you come back. It also explains the
cases where they can't be given here: a page that isn't https, an iPhone where
Vazhi isn't on the Home Screen (Safari has no alarms in a tab), and in-app
browsers such as WhatsApp's or Instagram's.

After planning a trip, **Wake me before <stop>** follows your location and
sounds a loud alarm 2 km (or the distance you pick) before each change and your final
stop, then "This is your stop" on arrival. **Try it: simulate the ride**
shows it working without travelling. Keep the page open; the screen stays on
while alerts run.

Phones allow location and notifications only on secure (https) pages. With
the app running, this gives your phone a temporary https link:

```bash
python tools/phone_link.py
```

Open the `https://….trycloudflare.com` address it prints on your phone, and
press Ctrl+C to close it. While it runs, anyone with the link can open the
app, so don't post it publicly.

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

- **Type in Where from / Where to** for suggestions from every named place in
  Kerala (online search as a backup), or **click the map** to place the
  highlighted point. Drag a marker to move it. The place list holds every
  named thing in Kerala's OpenStreetMap data (about 377,000: villages,
  junctions, bus stops, offices, temples, shops), read from the full Kerala
  file with pyosmium (`pip install osmium`; without it, from the map tiles,
  which hold fewer). Any English spelling of a Malayalam name finds it:
  Kuthiathode, Kuthiyathode and Kuthiyathodu all match, as do Kozhikode and
  Kozhikkode. Rebuild by hand with `python -m transitnav.places`.
- **☆ saves a place**: with the box empty, your saved and recent places are
  listed first.
- Pick a **mode**, pick **Walk** or **Two-wheeler** for getting to and from the
  stops, and press **Plan journey**.
- **Share this trip** copies a link that opens the same trip, already planned.
- **On a phone**, planning (where from, where to, Bus/Train/Metro) is in a
  sheet at the bottom; the planned trip slides in from the left
  (`web/drawer.js`). **Map ›** or a swipe left slides it away to show the
  route, and the **Trip ›** tab on the left edge brings it back.
- **Wake me before my stop** rings before the stop you get off at. Under it,
  **Ring the alarm** shows the distance in large numbers with one button per
  choice (Auto, 500 m to 10 km), and the **Vibrate only** switch turns the
  sound off.
- **Pop-ups in plain words** (`web/notice.js`): when a trip can't be planned,
  a pop-up says why in a few words ("Nearest metro station is 44.1 km away")
  with buttons that plan it another way (**Use a two-wheeler**, **Try a
  bus**). Things to know about a planned trip (a long walk, a far station, an
  old train timetable) pop up once and stay as yellow cards in the result;
  everything else is under **More about this trip**. Text is large throughout.

### Install it as an app

Vazhi is an installable web app (PWA). On Android, Chrome shows **Install Vazhi
on this phone** at the top of the panel; on an iPhone, the button explains
Safari's **Share > Add to Home Screen**. Installed, it has its own icon, opens
full screen without the browser bar, and opens even with no internet (planning
still needs it; a trip alarm that is already on keeps working offline).
Installing needs https: on a phone, use the link from `tools/phone_link.py`.

The app's own files are cached by `web/sw.js` but always fetched fresh when
online, so an update shows on the next open. Trip plans, search, bus times and
map tiles are never cached. The icons are drawn by `tools/make_icons.py`.

### Bus times from riders

No open timetable exists for KSRTC and private buses, so riders add the times
they know. Under every bus ride is the list of times riders have added at that
stop for buses going the same way, with the next ones first ("2:28 pm, in 15
min"). Anyone can add a time (when it leaves, KSRTC or private, which days, the
board on the bus) and confirm (**Seen it**) or flag (**Wrong**) other riders'
times. A time is hidden once more riders flag it than confirm it, by two.

Times are matched by boarding stop (within 400 m) and direction, so a time one
rider adds shows for everyone boarding there going that way. They are stored in
`data/timings.db`, which is not in git and is never cleared with the cache.
No IP addresses are kept: a browser is known only by a hash of a random id it
made itself, which allows one vote per time. Adding is limited to 20 times an
hour per connection.

When riders' times are known for your stop, the trip shows **Leave by**: the
first reported bus you can still reach (walk to the stop, plus two minutes),
when to set off, and when you will arrive. **Remind me when to leave** rings
five minutes before that, with the same alarm as the stop alarm (keep the page
open). The total time above it still allows a typical wait, because a rider's
time is a report, not a timetable.

### Stop names and missing stops from riders

OpenStreetMap often has a bus stop under an old or odd name, or misses a stop
where buses really stop. Riders fix both:

- **Wrong stop name?** under a bus ride: give the stop's real name.
- **Add a missing bus stop**: tap the map where buses stop and name it.

When a rider's trip uses a stop that others have renamed, or passes within
600 m of a stop riders added, a small card asks them about it: which name is
right (the map's name is always one of the choices), or whether buses really
stop there. Each rider backs one name per stop and can change it later; a
rider is asked about a stop once ("Not sure" waits a week).

A name replaces the map's once **at least 30 riders** back it **and more riders
back it than any other name**; a tie keeps the map's name, and riders backing
the map's name can win it back. An added stop is used for planning once at
least 30 riders confirm it and more say yes than no, under the name most riders
back. The threshold is `CONFIRM` in `transitnav/stopnames.py`. Stored in
`data/stops.db`, like bus times: never in git, no IP addresses, one vote per
browser.

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
  places.py         search index of every named place in Kerala (OSM file + tiles)
  timings.py        bus times added, confirmed and flagged by riders
  stopnames.py      stop names and missing stops from riders (30 must agree)
  timetable.py      Indian Railways timetable: download, index, train search
  router.py         street routing (Valhalla, OSRM) for walk, ride and bus legs
  geo.py            distances, bounding boxes, corridors, polyline maths
  graph.py          graph, spatial index, union-find, A*
  planner.py        the three-leg journey planner
  server.py         stdlib HTTP server, JSON API, SSE progress
tools/
  build_map.py      builds the Kerala map tiles with tilemaker (Docker)
  routing_server.py starts/stops your own Valhalla routing server (Docker)
  phone_link.py     temporary https link so the app (and trip alarm) works on a phone
  make_icons.py     draws the app icons into web/icons (Pillow)
web/                Leaflet front end (times.js: bus times, stops.js: stop names,
                    permit.js: the location and alarm permission screen);
                    home.html/.css/.js: the website at "/", site/: its screenshots
data/               riders' data (timings.db, stops.db) - live data, not in git
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

Riders' bus times: `GET /api/timings?blat=&blon=&alat=&alon=` lists the times
at a boarding stop for buses heading toward the alighting stop.
`POST /api/timings` adds one (`{board:{name,lat,lon}, toward:{name,lat,lon},
time:"07:40", operator, days, route?, device}`) and `POST /api/timings/vote`
confirms or flags (`{ids:[...], vote:1|-1, device}`).

Riders' stops: `POST /api/stops/rename` (`{key:"node/123", name, lat, lon,
map_name, device}`), `POST /api/stops/add` (`{lat, lon, name, device}`),
`POST /api/stops/answer` (`{key, name?, exists?, device}`), and
`GET /api/stops/ask?keys=node/1,node/2&points=lat,lon;lat,lon&device=` for the
questions to put to a rider.

Map data © OpenStreetMap contributors, ODbL.
