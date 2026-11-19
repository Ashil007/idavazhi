"""Collect private bus routes from Kerala RTA timing-conference notices.

A Regional Transport Authority (RTA) holds a "timing conference" before a
stage carriage (a private bus) gets a new permit or new timings. The district
website publishes a notice for each one as a PDF: the bus's registration
number and its route, e.g. "Panangad - Aluva via Madavana, Kundannoor, Vyttila".
The notices have no timings, but they show which roads a bus takes and which
permits are new or changed.

What the script does, step by step:
  1. Reads robots.txt and stops if the site asks robots to stay out.
  2. Fetches the RTA page and finds the PDF links in its "Timing Conference"
     column.
  3. Downloads each PDF once, waiting a few seconds between downloads, and
     keeps it in a local cache so it is never fetched twice.
  4. Reads the text with pypdf and finds each bus: registration number,
     route, type of service, and conference date.
  5. Keeps only those fields. The notices also name the permit holder and
     their home address; those are never saved.
  6. Writes routes.csv and routes.json for study.

Permission: the district site's copyright policy asks you to email for
permission before reproducing its material, and to name the source. So this
is for study only. Nothing goes into the app. Ask the district first, then
credit "RTA notices, <district> district website".

Usage:
  python tools/scrape_rta_routes.py                # Ernakulam, 10 newest PDFs
  python tools/scrape_rta_routes.py --limit 0      # every PDF on the page
  python tools/scrape_rta_routes.py --offline      # re-read cached PDFs only
  python tools/scrape_rta_routes.py --page https://<district>.nic.in/en/rta/

Needs pypdf (pip install pypdf). Everything else is the standard library.
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = "https://ernakulam.nic.in/en/rta/"
AGENT = "rta-route-study/1.0 (student project; slow, cached)"
CACHE = os.path.join(ROOT, ".cache", "rta", "pdfs")
OUT = os.path.join(ROOT, ".cache", "rta_routes")
DELAY_S = 3.0
TIMEOUT_S = 60
FIELDS = ["vehicle", "start", "end", "via", "service", "date", "office", "check", "source"]


# ---- 1-2. The page and its links --------------------------------------------

class _Links(HTMLParser):
    """PDF links in the first column of each table row."""

    def __init__(self):
        super().__init__()
        self.links, self._cell, self._href, self._text = [], -1, None, []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cell = -1
        elif tag in ("td", "th"):
            self._cell += 1
        elif tag == "a" and self._cell == 0:
            href = dict(attrs).get("href") or ""
            if href.lower().split("?")[0].endswith(".pdf"):
                self._href, self._text = href, []

    def handle_data(self, data):
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def pdf_links(html, base=PAGE):
    """(url, label) for each timing-conference PDF, page order, no repeats."""
    parser = _Links()
    parser.feed(html)
    seen, out = set(), []
    for href, label in parser.links:
        url = urllib.parse.urljoin(base, href)
        if url not in seen:
            seen.add(url)
            out.append((url, label))
    return out


def allowed(url, agent=AGENT):
    """What the site's robots.txt says. No robots.txt means no rules."""
    parts = urllib.parse.urlsplit(url)
    robots = urllib.robotparser.RobotFileParser()
    try:
        with urllib.request.urlopen(_request(f"{parts.scheme}://{parts.netloc}/robots.txt"),
                                    timeout=TIMEOUT_S) as reply:
            robots.parse(reply.read().decode("utf-8", "replace").splitlines())
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            return False
        return True                     # 404 and friends: no rules
    return robots.can_fetch(agent, url)


def _request(url):
    return urllib.request.Request(url, headers={"User-Agent": AGENT})


# ---- 3. Polite, cached downloads --------------------------------------------

class Fetcher:
    """Downloads each URL once, waits between real downloads."""

    def __init__(self, cache_dir=CACHE, delay=DELAY_S, offline=False, log=print,
                 robots=None, opener=None):
        self.cache_dir, self.delay, self.offline, self.log = cache_dir, delay, offline, log
        self.robots = robots or allowed
        self.opener = opener or (lambda url: urllib.request.urlopen(_request(url), timeout=TIMEOUT_S))
        self._hosts = {}
        self._last = 0.0
        os.makedirs(cache_dir, exist_ok=True)

    def may_fetch(self, url):
        """robots.txt, asked once per server (the PDFs live on a different one)."""
        host = urllib.parse.urlsplit(url).netloc
        if host not in self._hosts:
            self._hosts[host] = self.robots(url)
        return self._hosts[host]

    def path(self, url):
        name = os.path.basename(urllib.parse.urlsplit(url).path) or "file"
        digest = hashlib.sha1(url.encode()).hexdigest()[:10]
        return os.path.join(self.cache_dir, f"{digest}-{name}")

    def get(self, url):
        """The file's bytes, or None when offline and not cached."""
        path = self.path(url)
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        if self.offline:
            return None
        if not self.may_fetch(url):
            raise PermissionError(f"robots.txt asks robots not to read {url}")
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.log(f"  download {url}")
        with self.opener(url) as reply:
            data = reply.read()
        self._last = time.monotonic()
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        return data


def pdf_text(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("This needs pypdf: pip install pypdf")
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ---- 4. Reading a notice -----------------------------------------------------

# KL-40-2419, KL35J2497, KL 07 CS 0795, KL40A 4149
VEHICLE = re.compile(r"\bKL[\s.-]*(\d{1,2})[\s.-]*([A-Z]{0,3})[\s.-]*(\d{3,4})\b")
DATE = re.compile(r"\b(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(20\d\d)\b")
# Notes about the permit that sit between the number and the route:
# "(Permit Variation granted by RTA ...)", "Revision of Timings (Judgement
# ...)", "( Variation of Permit. RTA dated ...)". A route can have brackets
# too ("Ponekkara (Aims)"), so only notes with these words are dropped.
PERMIT_NOTE = re.compile(r"^\s*([^()]{0,40})\(([^)]*)\)", re.S)
NOTE_WORDS = re.compile(r"permit|variation|revision|timing|judg|\bw\.?p|\brta\b|item|dated|mvarp", re.I)
# Lines of such a note when it is not in brackets: "Time Revision Request
# [WP(C) No. ...]", "FRESH PERMIT GRANTED BY RTA ... (TIME LIMIT) On the route".
NOTE_LINE = re.compile(NOTE_WORDS.pattern + r"|\d|[\[\]©]|^\s*\(|\btime\b|request|granted|"
                       r"circulation|\bdtd\b|^\s*on\s+the\s+route\s*$", re.I)
NOTE_LINES_MAX = 12
# Where a route stops and the next column (or the letter's end) begins.
ROUTE_END = re.compile(
    r"Secretary|\n\s*To\s*\n|Variation\s*\(|\[\s*to\s+deviate|"
    r"\bas\s+(?:an?\s+)?(?:\S+\s+){0,3}?\S{0,3}r\S{0,2}ice\b|"
    r"Fresh\s+Stage|Revision\s+of|Application\s+for|\bExtend|\bas\s+per\b|\bGranted\b|\.\s*RTA\b|"
    r"\b\d{1,2}\s*[./-]\s*[\dOo]{1,2}\s*[./-]\s*[\dOo]{2,4}\b|\n\s*-\s*\n|\n\s*At\s*\n",
    re.I)
# Scanned notices read by OCR put digits inside words and letters inside
# numbers ("be1ow", "l2o2s"). Typed ones only have "7th", "10pm", "kl17".
OCR_JUNK = re.compile(r"\b\w*[a-z]\d[a-z]\w*\b|\b\w*\d[a-z]\d\w*\b")
OCR_JUNK_MIN = 3
SERVICE = re.compile(r"\bas\s+((?:an?\s+)?(?:\S+\s+){0,3}?\S{0,3}r\S{0,2}ice)\b", re.I)
VIA = re.compile(r"\[?\s*\bvia\b\s*\]?", re.I)
DASH = re.compile(r"\s*[-–—]+\s*")
# Letters, spaces and the few marks place names use; anything else means the
# PDF was a scan read badly and a person should look.
CLEAN_NAME = re.compile(r"^[A-Za-z][A-Za-z ()'&]*$")
CAMEL = re.compile(r"[a-z][A-Z]")        # "MeLnakkad": a scan read badly


def vehicle_id(match):
    district, series, number = match.groups()
    return " ".join(p for p in ("KL", district.zfill(2), series, number.zfill(4)) if p)


def tidy(name):
    name = " ".join(name.replace("\n", " ").split()).strip(" .,;:")
    if name.isupper() or name.islower():
        name = name.title()
    return name


def split_route(text):
    """'A - M - B via X, Y and Z' -> ('A', 'B', ['M', 'X', 'Y', 'Z'])."""
    text = " ".join(text.split())
    parts = VIA.split(text, maxsplit=1)
    ends = [tidy(p) for p in DASH.split(parts[0]) if tidy(p)]
    via = []
    if len(parts) > 1:
        via = [tidy(p) for p in re.split(r",|\band\b|&", parts[1], flags=re.I) if tidy(p)]
    if not ends:
        return "", "", via
    start, end = ends[0], ends[-1] if len(ends) > 1 else ""
    return start, end, ends[1:-1] + via


def service_type(text):
    found = SERVICE.search(text)
    if not found:
        return ""
    words = found.group(1).lower()
    for key, label in (("fast", "Fast Passenger"), ("limit", "Limited Stop"),
                       ("rdinary", "Ordinary")):
        if key in words:
            return label
    return ""


def _date(text):
    """The first date that is not a "dated ..." reference to an earlier order."""
    for m in DATE.finditer(text):
        before = text[max(0, m.start() - 12):m.start()].lower()
        if "dated" in before or "dtd" in before:
            continue
        day, month, year = (int(g) for g in m.groups())
        if 1 <= day <= 31 and 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def strip_note(text):
    note = PERMIT_NOTE.match(text)
    if note and NOTE_WORDS.search(note.group(1) + note.group(2)):
        text = text[note.end():]
    lines = text.split("\n")
    skip = 0
    while (skip < min(len(lines), NOTE_LINES_MAX)
           and (not lines[skip].strip() or NOTE_LINE.search(lines[skip]))):
        skip += 1
    return "\n".join(lines[skip:])


def office(text):
    head = text[:600].lower()
    for key, name in (("muvattupuzha", "Muvattupuzha"), ("muuattupuzha", "Muvattupuzha"),
                      ("ernakulam", "Ernakulam")):
        if key in head:
            return name
    return ""


def notices(text):
    """One record per bus in the notice: only route facts, never people."""
    text = text.replace("\r", "")
    # The addressee list (permit holder's name and home address) comes after
    # "Secretary ... To". Drop it before reading anything.
    end = re.search(r"\n\s*To\s*\n", text)
    letter = text[:end.start()] if end else text
    found = list(VEHICLE.finditer(letter))
    scanned = len(OCR_JUNK.findall(letter)) >= OCR_JUNK_MIN
    rows = []
    for i, m in enumerate(found):
        stop = found[i + 1].start() if i + 1 < len(found) else len(letter)
        after = strip_note(letter[m.end():stop])
        cut = ROUTE_END.search(after)
        route = after[:cut.start()] if cut else after
        start, finish, via = split_route(route)
        rest = after[cut.start():] if cut else ""
        before = letter[found[i - 1].end() if i else 0:m.start()]
        date = _date(rest) or _date(before)
        if not finish and not via:
            continue        # a number in a remark (a court case, an old order), not a route
        names = [n for n in [start, finish] + via if n]
        check = (scanned or not start or not finish or not via
                 or not all(CLEAN_NAME.match(n) and not CAMEL.search(n) for n in names)
                 or any(len(n) > 40 or len(n.split()) > 3 for n in names))   # missing commas
        rows.append({"vehicle": vehicle_id(m), "start": start, "end": finish, "via": via,
                     "service": service_type(after), "date": date,
                     "office": office(text), "check": check})
    return rows


# ---- 5-6. Run it -------------------------------------------------------------

def collect(page=PAGE, limit=10, fetcher=None, log=print):
    fetcher = fetcher or Fetcher(log=log)
    if fetcher.offline:
        html = fetcher.get(page)
        if html is None:
            sys.exit(f"{page} is not cached yet; run once without --offline.")
    else:
        # The list changes, so the page itself is always read fresh.
        if not fetcher.may_fetch(page):
            sys.exit(f"robots.txt asks robots not to read {page}. Stopping.")
        with fetcher.opener(page) as reply:
            html = reply.read()
        with open(fetcher.path(page), "wb") as f:
            f.write(html)
    links = pdf_links(html.decode("utf-8", "replace"), base=page)
    if limit:
        links = links[:limit]
    log(f"{len(links)} notices to read")
    rows, unread = [], []
    for url, label in links:
        try:
            data = fetcher.get(url)
        except (urllib.error.URLError, TimeoutError, PermissionError) as err:
            log(f"  could not download {url}: {err}")
            unread.append(url)
            continue
        if data is None:
            unread.append(url)
            continue
        try:
            text = pdf_text(data)
            found = notices(text)
        except Exception as err:       # a broken PDF should not stop the rest
            log(f"  could not read {url}: {err}")
            unread.append(url)
            continue
        if not found:
            log(f"  no bus found in {label or url}"
                + ("" if "transport" in text.lower() else
                   " (a scanned image with no text; it would need OCR)"))
        for row in found:
            row["source"] = url
            rows.append(row)
    return rows, unread


def write(rows, out_dir=OUT):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "routes.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row, via=" > ".join(row["via"]), check="yes" if row["check"] else ""))
    with open(os.path.join(out_dir, "routes.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--page", default=PAGE, help="district RTA page")
    ap.add_argument("--limit", type=int, default=10, help="newest N notices (0 = all)")
    ap.add_argument("--delay", type=float, default=DELAY_S, help="seconds between downloads")
    ap.add_argument("--offline", action="store_true", help="read cached files only")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    if args.delay < 1:
        ap.error("--delay must be at least 1 second")
    rows, unread = collect(args.page, args.limit,
                           Fetcher(delay=args.delay, offline=args.offline))
    write(rows, args.out)
    checks = sum(1 for r in rows if r["check"])
    print(f"{len(rows)} buses from {len(set(r['source'] for r in rows))} notices"
          f" ({checks} to check by hand, {len(unread)} notices unread)")
    print(f"Saved to {args.out}")


if __name__ == "__main__":
    main()
