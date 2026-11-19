"""The RTA timing-notice route scraper (tools/scrape_rta_routes.py).

The notices below are made up, but laid out like the real ones: Ernakulam
sends one typed letter per bus; Muvattupuzha sends a table of several buses.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import scrape_rta_routes as rta  # noqa: E402

ERNAKULAM = """REGIONAL TRANSPORT OFFICE, ERNAKULAM
(Time Settlement Schedule for Stage Carriage)
The following Timing conference is scheduled to be held as shown
below.
DATE & TIME VEHICLE NUMBER ROUTE
21/01/2025
AT 02.30 PM at
Timing conference hall
KL-40-2419 (Permit
Variation granted by RTA
Ernakulam dated
17/08/2024 vide item no
20)
Panangad-Aluva via
Madavana, Kundannoor,
Vyttila, Edappally and
Kalamassery
Secretary, RTA
Ernakulam
To
1. Sri. Test Person, C/O Other Person, Sample House,
Sample Road, Kakkanad, Ernakulam 682030
2. DTO, KSRTC, Ernakulam
"""

MUVATTUPUZHA = """No. C1/614/2020/EM Office of the Secretary, Regional Transport Authority
Muvattupuzha dated 10-02-2026
NOTICE FOR TIMING CONFERENCE
Sl. No. Reference No. Vehicle No. Route Date and time Remarks/Reason
1. C1/1376482/2025/EM KL 38-A-7374 Manjapra- Mumamkuzhy-
Thattekkead[Via] Kalady, Perumbavoor and
Kothamangalam as Ordinary
Moffusil Service
-
20-02-2026
2.30 PM
Revision of timings
Judgment in WP(C) No. 1 of 2025 Dtd. 16.10.2025.
2. C1/1380395/2025/EM KL 40-Q-9048 Perumbavoor-
Kothamangalam- Aluva as
ordinary moffusil service
20-02-2026
3.00PM
Application for
Revision of timings; earlier heard with KL 07 BG 4405
Dtd. 21.07.2022
"""

SCANNED = """Offtce of the RegionalTransport Officer
Muuattupuzha dated - 1 5. 0 9. 2 02 5
scheduled to be convened be1ow. Hon' b1e High Court l2o2s
1 4.4 KL 17 D 0665 Vazhithala - Pothanikad Via
Pcriyambra, Thodupuzha as
Srdinary Molussil Scrvice.
"""


class LinksTest(unittest.TestCase):
    HTML = """<table>
      <tr><th>Timing Conference</th><th>Agenda</th><th>Decisions</th></tr>
      <tr><td><a href="https://cdn.example/a.pdf">KL-40 notice (PDF 90 KB)</a></td>
          <td><a href="https://cdn.example/agenda.pdf">Agenda</a></td><td>-</td></tr>
      <tr><td><a href="/uploads/b.PDF">Second</a> <a href="/about/">not a pdf</a></td></tr>
      <tr><td><a href="https://cdn.example/a.pdf">Same file again</a></td></tr>
    </table>"""

    def test_only_timing_notices_once_each(self):
        links = rta.pdf_links(self.HTML, base="https://district.example/en/rta/")
        self.assertEqual([u for u, _ in links],
                         ["https://cdn.example/a.pdf", "https://district.example/uploads/b.PDF"])
        self.assertEqual(links[0][1], "KL-40 notice (PDF 90 KB)")


class ReadTest(unittest.TestCase):
    def test_vehicle_numbers(self):
        for text, want in (("KL-40-2419", "KL 40 2419"), ("KL35J2497", "KL 35 J 2497"),
                           ("KL 07 CS 0795", "KL 07 CS 0795"), ("KL40A 4149", "KL 40 A 4149"),
                           ("KL-7-AB-12 3", None)):
            m = rta.VEHICLE.search(text)
            self.assertEqual(rta.vehicle_id(m) if m else None, want, text)

    def test_split_route(self):
        self.assertEqual(rta.split_route("Panangad-Aluva via Madavana, Vyttila and Kalamassery"),
                         ("Panangad", "Aluva", ["Madavana", "Vyttila", "Kalamassery"]))
        self.assertEqual(rta.split_route("Manjapra- Mumamkuzhy- Thattekkead[Via] Kalady"),
                         ("Manjapra", "Thattekkead", ["Mumamkuzhy", "Kalady"]))
        self.assertEqual(rta.split_route("ALUVA – FORT KOCHI"), ("Aluva", "Fort Kochi", []))

    def test_ernakulam_letter(self):
        [row] = rta.notices(ERNAKULAM)
        self.assertEqual(row["vehicle"], "KL 40 2419")
        self.assertEqual((row["start"], row["end"]), ("Panangad", "Aluva"))
        self.assertEqual(row["via"], ["Madavana", "Kundannoor", "Vyttila", "Edappally", "Kalamassery"])
        self.assertEqual(row["date"], "2025-01-21")          # not the "dated 17/08/2024" order
        self.assertEqual(row["office"], "Ernakulam")
        self.assertFalse(row["check"])

    def test_permit_notes_before_the_route(self):
        head = "REGIONAL TRANSPORT OFFICE, ERNAKULAM\n16/01/2026\nAT 03.00 PM\n"
        cases = (
            ("KL-40-E-2223\nRevision of Timings\n(Judgement of Hon'ble\nSTAT dated\n10/05/2025)\n"
             "Kodungalloor – Ezhikkara\nvia Moothakunnam, North\nParavur",
             ("Kodungalloor", "Ezhikkara", ["Moothakunnam", "North Paravur"])),
            ("KL56D0427\n(\nVariation of Permit. RTA\ndated 18/01/2025 vide\nitem no 32)\n"
             "Manjanakkad- High Court\nvia Njarackal,\nValappu\nExtending two trips from\nHigh Court",
             ("Manjanakkad", "High Court", ["Njarackal", "Valappu"])),
            ("KL-07-CS-0795\nPonekkara (Aims) –\nKumbalanghy via Boat Jetty, Ekm South",
             ("Ponekkara (Aims)", "Kumbalanghy", ["Boat Jetty", "Ekm South"])),
            ("KL-05-W-0066 \n(Time\nRevision) MVARP\n59/2024 dated\n09/12/2024\n"
             "Perumbavoor - Vyttila\nHub          Via\nKakkanad, Bypass,\nAnd Medical Centre",
             ("Perumbavoor", "Vyttila Hub", ["Kakkanad", "Bypass", "Medical Centre"])),
            ("KL-37-2000\nTime Revision\nRequest [WP© No.\n33961/2023 dated\n16/10/2023]\n"
             "Kakkanad - Poochakkal Via Ngo\nQuarters,  Palarivattom, Aroor  Temple  And\nArookutty.",
             ("Kakkanad", "Poochakkal", ["Ngo Quarters", "Palarivattom", "Aroor Temple", "Arookutty"])),
            ("KL 07 BA 3658\nFRESH PERMIT GRANTED BY\nRTA, ERNAKULAM under\ncirculation on 03/07/2023.\n"
             "(TIME LIMIT)\nOn the route \nNorth Paravur –\nVyttila Hub via Varapuzha\n"
             "Bridge and Edappally Bypass\nas Ordinary Moffusil Service",
             ("North Paravur", "Vyttila Hub", ["Varapuzha Bridge", "Edappally Bypass"])),
        )
        for body, want in cases:
            [row] = rta.notices(head + body + "\nSecretary, RTA\n")
            self.assertEqual((row["start"], row["end"], row["via"]), want, body[:12])
            self.assertEqual(row["date"], "2026-01-16")

    def test_no_personal_details_kept(self):
        saved = json.dumps(rta.notices(ERNAKULAM) + rta.notices(MUVATTUPUZHA))
        for word in ("Sri", "Test Person", "C/O", "House", "682030", "Sample"):
            self.assertNotIn(word, saved)

    def test_muvattupuzha_table(self):
        rows = rta.notices(MUVATTUPUZHA)
        # KL 07 BG 4405 is only named in a remark, so it is not a route.
        self.assertEqual([r["vehicle"] for r in rows], ["KL 38 A 7374", "KL 40 Q 9048"])
        first, second = rows
        self.assertEqual((first["start"], first["end"]), ("Manjapra", "Thattekkead"))
        self.assertEqual(first["via"], ["Mumamkuzhy", "Kalady", "Perumbavoor", "Kothamangalam"])
        self.assertEqual(first["service"], "Ordinary")
        self.assertEqual(first["date"], "2026-02-20")
        self.assertEqual((second["start"], second["end"], second["via"]),
                         ("Perumbavoor", "Aluva", ["Kothamangalam"]))
        self.assertEqual(second["office"], "Muvattupuzha")
        self.assertFalse(any(r["check"] for r in rows))

    def test_scanned_notice_is_marked_for_checking(self):
        [row] = rta.notices(SCANNED)
        self.assertEqual(row["vehicle"], "KL 17 D 0665")
        self.assertEqual((row["start"], row["end"]), ("Vazhithala", "Pothanikad"))
        self.assertTrue(row["check"])


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.calls, self.robots_asked = [], []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def opener(self, url):
        self.calls.append(url)
        return _Reply(url.encode())

    def robots(self, url):
        self.robots_asked.append(url)
        return "blocked" not in url

    def fetcher(self, **kw):
        return rta.Fetcher(cache_dir=self.dir, delay=0, log=lambda *a: None,
                           robots=self.robots, opener=self.opener, **kw)

    def test_downloads_once(self):
        f = self.fetcher()
        self.assertEqual(f.get("https://cdn.example/a.pdf"), b"https://cdn.example/a.pdf")
        f.get("https://cdn.example/a.pdf")
        f.get("https://cdn.example/b.pdf")
        self.assertEqual(self.calls, ["https://cdn.example/a.pdf", "https://cdn.example/b.pdf"])
        self.assertEqual(len(self.robots_asked), 1)              # once per server

    def test_respects_robots(self):
        with self.assertRaises(PermissionError):
            self.fetcher().get("https://blocked.example/a.pdf")
        self.assertEqual(self.calls, [])

    def test_offline_uses_cache_only(self):
        self.fetcher().get("https://cdn.example/a.pdf")
        offline = self.fetcher(offline=True)
        self.assertIsNotNone(offline.get("https://cdn.example/a.pdf"))
        self.assertIsNone(offline.get("https://cdn.example/new.pdf"))
        self.assertEqual(len(self.calls), 1)


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = rta.pdf_text
        rta.pdf_text = lambda data: data.decode()

    def tearDown(self):
        rta.pdf_text = self.old
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_page_to_csv(self):
        files = {
            "https://district.example/en/rta/":
                b'<table><tr><td><a href="/e.pdf">E</a></td></tr>'
                b'<tr><td><a href="/m.pdf">M</a></td></tr>'
                b'<tr><td><a href="/gone.pdf">Gone</a></td></tr></table>',
            "https://district.example/e.pdf": ERNAKULAM.encode(),
            "https://district.example/m.pdf": MUVATTUPUZHA.encode(),
        }

        def opener(url):
            if url not in files:
                raise rta.urllib.error.URLError("404")
            return _Reply(files[url])

        fetcher = rta.Fetcher(cache_dir=os.path.join(self.dir, "cache"), delay=0,
                              log=lambda *a: None, robots=lambda u: True, opener=opener)
        with contextlib.redirect_stdout(io.StringIO()):
            rows, unread = rta.collect("https://district.example/en/rta/", 0, fetcher,
                                       log=lambda *a: None)
        self.assertEqual([r["vehicle"] for r in rows], ["KL 40 2419", "KL 38 A 7374", "KL 40 Q 9048"])
        self.assertEqual(rows[0]["source"], "https://district.example/e.pdf")
        self.assertEqual(unread, ["https://district.example/gone.pdf"])

        rta.write(rows, self.dir)
        with open(os.path.join(self.dir, "routes.csv"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(rta.FIELDS))
        self.assertIn("Madavana > Kundannoor > Vyttila", lines[1])


if __name__ == "__main__":
    unittest.main()
