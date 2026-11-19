"""Fare estimates, only from the operators' own published fares.

  * KSRTC buses - Kerala's stage-carriage fare order, G.O. dated 30-04-2022,
    in force from 01-05-2022 (the same table is on keralartc.com/about-us/fare).
    For each class of service it sets a minimum fare, the distance that
    minimum covers, and a rate per km for the distance beyond it:

        fare = minimum fare + (km - minimum km) x rate

    rounded the class's way: ordinary to the nearest rupee (50 paise and up
    rounds up), fast and super fast up to the next rupee, express up to the
    next Rs 5, deluxe, AC and Volvo up to the next Rs 10, low-floor AC up to
    the next Rs 2. The order's own example: 10 km by ordinary bus is
    Rs 10 for the first 2.5 km + 7.5 km x Re 1 = Rs 17.50, so Rs 18.
    Conductors charge by fare stage; on ordinary buses a stage is 2.5 km, so
    ordinary fares go 10, 13, 15, 18, 20, 23 ... and a ride is priced as a
    range from the stage just below the distance to the stage just above.
    Not added, because Idavazhi cannot know them: +25% on ghat roads, festival
    and special services, KSRTC's flexi fares, season and student tickets.
  * Kochi Metro - KMRL's distance-based fare chart
    (corporate.kochimetro.org/fare-chart), single-journey fares.

Not priced, because no official per-km table was found to work from:
trains (Indian Railways publishes only the changes to its fare table),
auto-rickshaws and cabs. Those legs carry `fare: None`, and a trip with one
says its fare is not complete rather than guess.

Idavazhi does not know which bus type comes, so a bus leg lists the fare for
every type, and the summary shows the ordinary-to-fast-passenger range.
Private buses charge by the same order; they run the ordinary and fast
classes, so a ride by private bus lists only those. The higher classes
(Super Fast and up, AC, Volvo, sleeper) are KSRTC's.
"""

from __future__ import annotations

KSRTC_SOURCE = "Kerala bus fares in force from 1 May 2022 (G.O. of 30-04-2022, keralartc.com)"
METRO_SOURCE = "Kochi Metro fare chart (kochimetro.org)"

# Every class on the 2022 order: (name, paise per km, minimum fare in rupees,
# km the minimum covers, rounding). Rounding "nearest" is to the nearest
# rupee; a number is "up to the next multiple of that many rupees".
KSRTC_TYPES = [
    ("City / Ordinary", 100, 10, 2.5, "nearest"),
    ("City Fast", 103, 12, 2.5, 1),
    ("Fast Passenger", 105, 15, 5, 1),
    ("Super Fast", 108, 22, 10, 1),
    ("Express / Super Express", 110, 28, 15, 5),
    ("Super Air Express", 115, 35, 15, 5),
    ("Super Deluxe / Semi Sleeper", 120, 40, 15, 10),
    ("Luxury / Hi-tech / AC", 150, 60, 20, 10),
    ("Low Floor AC", 175, 26, 5, 2),
    ("Low Floor non-AC", 100, 10, 2.5, 1),
    ("Single Axle Volvo", 181, 60, 20, 10),
    ("Multi Axle Volvo", 225, 100, 20, 10),
    ("AC Sleeper", 250, 130, 20, 10),
]
# KSRTC Swift is a company, not a fare class: its buses run as Super Fast,
# Deluxe, AC seater and so on, and charge that class's fare.
NOTES = ["KSRTC Swift buses charge the fare of their class "
         "(a Swift Super Fast costs the Super Fast fare)."]
# Ordinary fare stages are 2.5 km apart; the order gives no stage length for
# the other classes.
STAGE_M = {"City / Ordinary": 2500, "Low Floor non-AC": 2500}
# The summary shows the common ordinary and fast ones; the rider sees every
# type in the leg's details.
SUMMARY_TYPES = ("City / Ordinary", "Fast Passenger")
# The classes a private bus runs as (see the top of this file).
PRIVATE_TYPES = ("City / Ordinary", "Fast Passenger")
PRIVATE_NOTE = "Private buses charge the same government fares as KSRTC."

# Kochi Metro single-journey fares: (up to this many metres, rupees).
METRO_SLABS = [(2000, 10), (5000, 20), (10000, 30), (15000, 40), (20000, 50)]
METRO_MAX_FARE = 60


def ksrtc_fare(paise_per_km, minimum, minimum_km, rounding, metres):
    """One class's fare in rupees for `metres`, by the 2022 order."""
    beyond = max(0.0, metres - minimum_km * 1000.0)
    paise = minimum * 100 + int(round(beyond * paise_per_km / 1000.0))
    if rounding == "nearest":
        return (paise + 50) // 100
    step = rounding * 100
    return -(-paise // step) * rounding


def bus_fare(metres, operator=None):
    """What a bus ride of `metres` costs on each class of KSRTC bus:
    {"min", "max", "source", "types": [{"name", "rupees", "low", "high"}],
    "notes": [...]}. "rupees" is the fare for the exact distance; low/high
    are the fares at the fare stages either side of it. min/max are the
    summary's range (ordinary at the stage below to fast passenger).
    `operator` "private": only the classes private buses run."""
    types = []
    for name, rate, minimum, minimum_km, rounding in KSRTC_TYPES:
        if operator == "private" and name not in PRIVATE_TYPES:
            continue
        exact = ksrtc_fare(rate, minimum, minimum_km, rounding, metres)
        low = high = exact
        stage = STAGE_M.get(name)
        if stage:
            below = (metres // stage) * stage
            above = -(-metres // stage) * stage
            low = ksrtc_fare(rate, minimum, minimum_km, rounding, below)
            high = ksrtc_fare(rate, minimum, minimum_km, rounding, above)
        types.append({"name": name, "rupees": exact, "low": low, "high": high})
    by = {t["name"]: t for t in types}
    low = by[SUMMARY_TYPES[0]]["low"]
    high = max(by[SUMMARY_TYPES[1]]["high"], by[SUMMARY_TYPES[0]]["high"])
    return {"min": low, "max": high, "source": KSRTC_SOURCE, "types": types,
            "notes": [PRIVATE_NOTE] if operator == "private" else list(NOTES)}


def metro_fare(metres):
    for upto, rupees in METRO_SLABS:
        if metres <= upto:
            return {"min": rupees, "max": rupees, "source": METRO_SOURCE}
    return {"min": METRO_MAX_FARE, "max": METRO_MAX_FARE, "source": METRO_SOURCE}


def leg_fare(leg):
    """What one leg costs: a dict, 0 for the rider's own walk or ride, or None
    when Idavazhi has no official fare for it."""
    kind = leg.get("type")
    if kind == "wait":
        return 0
    if kind == "transit":
        mode = leg.get("mode")
        if mode == "bus":
            return bus_fare(leg["distance_m"], leg.get("operator"))
        if mode == "metro":
            # A metro ride with a change is still one ticket.
            return metro_fare(leg["distance_m"])
        return None                       # trains: no official table to work from
    access = leg.get("access")
    if access == "road":
        # "Bus or auto-rickshaw": priced as the bus; an auto costs more.
        return dict(bus_fare(leg["distance_m"]), note="by bus; an auto-rickshaw costs more")
    if access == "cab":
        return None
    return 0                              # walking, or the rider's own two-wheeler


def _rupees(low, high):
    return "₹%d" % low if low == high else "₹%d–%d" % (low, high)


def price(plan):
    """Puts a fare on each leg and a total in the summary, for the plan and
    each of its alternatives. Returns the plan."""
    for option in [plan] + list(plan.get("alternatives") or []):
        low = high = 0
        missing, sources = [], []
        for leg in option["legs"]:
            fare = leg_fare(leg)
            if isinstance(fare, dict):
                leg["fare"] = dict(fare, text=_rupees(fare["min"], fare["max"]))
                low += fare["min"]
                high += fare["max"]
                if fare["source"] not in sources:
                    sources.append(fare["source"])
            elif fare is None:
                leg["fare"] = None
                what = ("train" if leg.get("type") == "transit" else
                        "cab" if leg.get("access") == "cab" else "ride")
                if what not in missing:
                    missing.append(what)
        text = _rupees(low, high) if (low or not missing) else ""
        if missing:
            extra = " + ".join("%s fare" % m for m in missing)
            text = (text + " + " + extra) if text else extra.capitalize()
        option["summary"]["fare"] = {
            "min": low, "max": high, "complete": not missing, "missing": missing,
            "text": text if (low or missing) else "Free", "sources": sources,
        }
    return plan
