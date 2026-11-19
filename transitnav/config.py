"""Per-mode configuration: how to find stops, what network to route over, speeds.

The `*_filters` strings are Overpass QL tag filters. Two tagging subtleties
drove the shape of this file, both verified against the live API:

  * `["key"!~"regex"]` only matches elements that *have* the key. Most roads
    carry no `access` tag and most track carries no `service` tag, so writing
    `["access"!~"private"]` silently returns nothing. Negative conditions must
    therefore be expressed either as `[!"key"]` (key absent) or as a subtracted
    clause in the union - see `network_exclude`.
  * metro deliberately includes light_rail, because many real metro systems are
    mapped as light_rail in OSM.
"""

from __future__ import annotations

MAX_WALK_TO_STOP_M = 8000     # fallback cap on the walk to a stop
LONG_ACCESS_WARN_M = 3000     # above this, the itinerary suggests a local hop
STOP_CANDIDATES = 4           # how many nearby stops to try per end
STOP_SHORTLIST = 6            # nearest-on-the-map stops re-ranked by real walk/ride
INTERMEDIATE_STOP_TOLERANCE_M = 220  # how close a stop must sit to the routed line

# Interchanges. OSM maps each line's track separately, and where two lines meet
# at different levels their tracks never share a node, so the raw graph cannot
# change lines. Tracks of different components near one station are linked:
# near-parallel ones are the two directions of the same line (a cheap switch,
# not reported), crossing ones are different lines (a real change of train).
PARALLEL_TRACK_DEG = 25.0      # headings closer than this count as one line
TRACK_SWITCH_PENALTY_M = 60.0  # cost of hopping to the other-direction track
INTERCHANGE_PENALTY_M = 800.0  # cost of a real change, as distance-equivalent
TRANSFER_WALK_S = 180          # platform-to-platform time added per change

MODES = {
    "bus": {
        "label": "Bus",
        "color": "#22a06b",
        "vehicle": "bus",
        "stop_noun": "bus stop",
        "speed_kmh": 20.0,
        "dwell_s": 20,        # added per intermediate stop
        "wait_s": 360,        # nominal headway allowance
        "stop_search_radii": [500, 1200, 2500, 5000],
        "snap_radius_m": 180,
        "max_access_m": 2500,
        "network_margin_m": 1200,
        # Bus routes come from the street router (your own Valhalla server in
        # Kerala answers in ~0.1 s at any length), so long-distance buses -
        # KSRTC's bread and butter - are planned too.
        "max_trip_m": 600000,
        # Beyond this, without a street router, the fallback would download
        # every road along the trip; too slow to be worth waiting for.
        "road_fallback_max_m": 60000,
        # Long-distance buses, from `from_m`: less frequent, and the stops
        # listed are bus stands plus a main stop about every `list_every_m`.
        # Without a router time, rides are estimated at town pace for the
        # first `town_m`, then `speed_kmh` with a stop every `stop_every_m`.
        "intercity": {"from_m": 30000, "wait_s": 900, "list_every_m": 15000,
                      "town_m": 10000, "speed_kmh": 45.0, "stop_every_m": 5000},
        # When the street router gives a travel time for the actual roads
        # (highway or lane), the bus takes that much longer for its stops,
        # boarding and slower pulling away: more in town than between towns.
        "road_time_factor": {"town": 1.35, "intercity": 1.2},
        "tile_deg": 0.25,     # stop data is cached in tiles this size
        "router": "bus",      # buses follow the street router's bus routes
        # Bus stops on the map where no bus runs: never boarded or left at.
        # Only stops someone who knows the road has checked; riders report
        # the rest ("No bus runs here", stopnames.py).
        "skip_stops": {
            # Side road west of Turavur station (Cherthala): no bus service.
            "node/11505252377": "Nalukulangara",
            "node/11505252378": "Nalukulangara (other side)",
            "way/620304883": "unnamed stop near Turavur station",
        },
        # Stops only private buses call at (no KSRTC), as riders who use them
        # report. A KSRTC trip does not start or end there, and a ride from
        # one is shown as a private bus.
        "private_only_stops": {
            # Unnichira, Thrikkakara: private town buses only.
            "node/5757830538": "Unnichira",
            "node/9402200479": "Unnichira (other side)",
        },
        # Names of bus stands, where long-distance buses start and stop. OSM
        # also tags some roadside stops as "stations", so the name decides.
        "stand_names": r"bus\s*(stand|station|terminal|terminus|depot)|mobility hub|\bksrtc\b|\bdepot\b",
        "stop_filters": [
            '["highway"="bus_stop"]',
            '["public_transport"="platform"]["bus"="yes"]',
            '["amenity"="bus_station"]',
        ],
        "network_filters": [
            '["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|'
            'residential|living_street|service|busway|road|motorway_link|trunk_link|'
            'primary_link|secondary_link|tertiary_link)$"]',
        ],
        "network_exclude": [
            '["highway"]["access"~"^(private|no)$"]',
        ],
    },
    "train": {
        "label": "Train",
        "color": "#e8833a",
        "vehicle": "train",
        "stop_noun": "railway station",
        # Signal cabins ("Ernakulam D Cabin") are sometimes tagged as stations
        # in OSM; no passenger boards there.
        "skip_stop_names": r"\bcabin\b",
        "speed_kmh": 70.0,
        "dwell_s": 90,
        "wait_s": 900,
        "stop_search_radii": [1500, 4000, 10000, 25000],
        "snap_radius_m": 450,
        "max_access_m": 12000,
        "network_margin_m": 5000,
        "max_trip_m": 300000,  # longer trips use the timetable (timetable.py)
        "tile_deg": 1.0,      # rail is sparse: big tiles, few downloads
        # Trips beyond max_trip_m are planned from the Indian Railways
        # timetable: real trains and times instead of track geometry.
        "long_trip_m": 3500000,
        "stop_filters": [
            '["railway"="station"]["station"!~"^(subway|light_rail|monorail)$"]',
            '["railway"="station"][!"station"]',
            '["railway"="halt"]',
        ],
        # [!"service"] drops sidings, spurs, yards and crossovers from routing.
        "network_filters": [
            '["railway"~"^(rail|narrow_gauge)$"][!"service"]',
        ],
        "network_exclude": [],
    },
    "metro": {
        "label": "Metro",
        "color": "#8b5cf6",
        "vehicle": "metro train",
        "stop_noun": "metro station",
        # 34 km/h is a metro's average *including* its station stops (Kochi
        # Metro: Aluva-Vyttila, ~23 km, about 40 min), so no extra per stop.
        "speed_kmh": 34.0,
        "dwell_s": 0,
        "wait_s": 300,
        "stop_search_radii": [700, 1800, 4000, 9000],
        "snap_radius_m": 350,
        "max_access_m": 4000,
        "network_margin_m": 2500,
        "max_trip_m": 90000,
        "tile_deg": 0.25,
        "stop_filters": [
            '["railway"="station"]["station"="subway"]',
            '["railway"="station"]["subway"="yes"]',
            '["railway"="station"]["station"="light_rail"]',
            '["railway"="station"]["light_rail"="yes"]',
        ],
        "network_filters": [
            '["railway"~"^(subway|light_rail)$"][!"service"]',
        ],
        "network_exclude": [],
    },
}

# Railway lines can bend far from the straight line between two points
# (Kochi-Coimbatore runs ~57 km off it, via Shoranur). If no connection exists
# in the first, tight search area, the track is fetched again over a corridor
# this fraction of the trip length either side of the line.
NETWORK_WIDEN_FACTOR = 0.5

# How you reach the boarding stop and leave the alighting one. Both legs are
# shortest paths over a street network downloaded around them; the download
# margin (ACCESS_MARGIN_*) grows with the leg's length so a route that must
# detour to a bridge or a level crossing still lies inside the data.
ACCESS_MODES = {
    "walk": {
        "label": "Walk",
        "verb": "Walk",
        "noun": "on foot",
        "network_noun": "footpath",
        "speed_kmh": 4.8,
        # Motorways are never walkable. Trunk roads are: in much of the world,
        # India included, they carry pavements and bus stops. Where walking is
        # banned the way says so with foot=no or motorroad=yes.
        "filters": [
            '["highway"~"^(footway|path|pedestrian|steps|living_street|residential|service|'
            'unclassified|tertiary|secondary|primary|trunk|track|cycleway|road|corridor|'
            'platform|tertiary_link|secondary_link|primary_link|trunk_link)$"]',
        ],
        "exclude": [
            '["highway"]["foot"~"^(no|private)$"]',
            '["highway"]["access"~"^(private|no)$"]',
            '["highway"]["motorroad"="yes"]',
        ],
        "oneway_keys": None,            # pedestrians may walk either way
        "router": "foot",
    },
    "two_wheeler": {
        "label": "Two-wheeler",
        "verb": "Ride",
        "noun": "by two-wheeler",
        "network_noun": "street",
        "speed_kmh": 22.0,              # typical urban scooter/motorbike average
        # Roads only. Motorways are left out: two-wheelers are barred from
        # most access-controlled expressways, India's included.
        "filters": [
            '["highway"~"^(trunk|primary|secondary|tertiary|unclassified|residential|'
            'living_street|service|road|track|trunk_link|primary_link|secondary_link|'
            'tertiary_link)$"]',
        ],
        "exclude": [
            '["highway"]["access"~"^(private|no)$"]',
            '["highway"]["motor_vehicle"~"^(private|no)$"]',
            '["highway"]["motorcycle"~"^(private|no)$"]',
        ],
        "oneway_keys": ["oneway:motorcycle", "oneway"],
        # Valhalla's motor_scooter profile: streets only, one-way rules obeyed,
        # no motorways. (Its OSRM fallback only has a car profile.)
        "router": "scooter",
        # May reach stops this many times farther than on foot: a 10 km ride
        # to a metro station is ordinary, a 10 km walk is not.
        "reach_factor": 3.0,
    },
    # A taxi, auto-rickshaw or ride-hailing cab (the app offers to book it
    # in Uber) to the first stop. Like a two-wheeler it is left behind, so
    # the far end is a walk.
    "cab": {
        "label": "Cab",
        "verb": "Cab",
        "noun": "by cab",
        "network_noun": "street",
        "speed_kmh": 20.0,              # town traffic, stops and turns
        "pickup_wait_s": 360,           # waiting for the cab to arrive
        "filters": [
            '["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|'
            'living_street|service|road|motorway_link|trunk_link|primary_link|secondary_link|'
            'tertiary_link)$"]',
        ],
        "exclude": [
            '["highway"]["access"~"^(private|no)$"]',
            '["highway"]["motor_vehicle"~"^(private|no)$"]',
            '["highway"]["motorcar"~"^(private|no)$"]',
        ],
        "oneway_keys": ["oneway"],
        "router": "car",
        "reach_factor": 5.0,
    },
}
ACCESS_MARGIN_MIN_M = 400
ACCESS_MARGIN_MAX_M = 1500
ACCESS_MARGIN_FACTOR = 0.5       # of the leg's straight-line length
ACCESS_SNAP_RADIUS_M = 600
# Weight on the straight hop from a point onto its road when choosing where to
# join. Above 1, the route joins the road beside the point rather than
# cutting across buildings to a farther one.
OFFNET_FACTOR = 2.0

# Stations and track are downloaded in fixed map tiles (sized per mode, see
# `tile_deg`), so every trip in a city reuses the same cached files. They
# change rarely, so tiles are kept for a month - and used past that if the
# Overpass service is down.
TILE_TTL_S = 30 * 24 * 3600


def mode_config(mode: str) -> dict:
    try:
        return MODES[mode]
    except KeyError:
        raise ValueError(
            "unknown mode %r; expected one of %s" % (mode, ", ".join(sorted(MODES)))
        ) from None


def access_config(access: str) -> dict:
    try:
        return ACCESS_MODES[access]
    except KeyError:
        raise ValueError(
            "unknown access mode %r; expected one of %s"
            % (access, ", ".join(sorted(ACCESS_MODES)))
        ) from None
