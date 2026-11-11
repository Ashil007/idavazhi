"""Vazhi Messages: read what riders send you, on this computer.

    pythonw tools/inbox.py          (or the "Vazhi Messages" shortcut on your desktop)
    python tools/inbox.py --shortcut   puts that shortcut on your desktop

Shows the messages from "Tell me what you think" (About Vazhi) and the
contact page, the stars riders give a trip and the bus tickets they buy you
(Razorpay), newest first. Ones you haven't seen yet are in bold. It checks
for new ones every 15 seconds (30 when it asks a server).

Where it reads them from:

  * Vazhi runs on this computer: straight from data/feedback.db.
  * Vazhi runs on a server (the cloud): from that server, with a password.

        python tools/inbox.py --setup https://your-vazhi-address
            makes the password (VAZHI_INBOX_TOKEN) and saves it and the
            address (VAZHI_INBOX_URL) in data/vazhi.env here. Copy the
            VAZHI_INBOX_TOKEN line into the server's data/vazhi.env too.
        python tools/inbox.py --local
            back to reading data/feedback.db on this computer.

Telegram (tools/telegram_setup.py) is optional: it pings your phone as well.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from tkinter import ttk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from transitnav import feedback, settings  # noqa: E402

SEEN_FILE = os.path.join(ROOT, "data", "inbox-seen.txt")
REFRESH_MS = 15000
REMOTE_REFRESH_MS = 30000

BG, PANEL, LINE, INK, DIM, FAINT, AMBER = "#0d1117", "#171f2b", "#2a3442", "#eef2f7", "#a3b0c2", "#6b7a8f", "#f5b642"


def stars(n):
    return "★" * n + "☆" * (5 - n) if n else ""


class InboxError(Exception):
    """Why the server's messages couldn't be fetched, in words for the owner."""


def server():
    """The Vazhi server to ask, or "" to read data/feedback.db here."""
    return settings.get("VAZHI_INBOX_URL").rstrip("/")


def safe_url(url):
    """Only https (or this computer itself): the password must not travel in the clear."""
    u = urllib.parse.urlparse(url)
    if u.scheme == "https":
        return bool(u.netloc)
    return u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")


def load():
    """Messages, ratings and bus tickets, newest first, as
    (key, text, contact, page, created, sent, rating, rupees) rows; key is the
    message id, or "t:<payment id>" for a ticket."""
    url = server()
    return load_remote(url) if url else rows_of(feedback.inbox())


def load_remote(url):
    if not safe_url(url):
        raise InboxError("VAZHI_INBOX_URL must start with https:// (run --setup again).")
    token = settings.get("VAZHI_INBOX_TOKEN")
    if not token:
        raise InboxError("No password yet: run  python tools/inbox.py --setup " + url)
    req = urllib.request.Request(url + "/api/inbox", headers={
        "Authorization": "Bearer " + token, "User-Agent": "VazhiMessages/1"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return rows_of(json.loads(r.read().decode("utf-8")))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise InboxError("The server refused the password. Copy the VAZHI_INBOX_TOKEN line "
                             "from data/vazhi.env here into the server's data/vazhi.env.") from None
        if e.code == 404:
            raise InboxError("The server has no inbox switched on: put the VAZHI_INBOX_TOKEN line "
                             "in its data/vazhi.env.") from None
        if e.code == 429:
            raise InboxError("Too many tries for now; it will work again within the hour.") from None
        raise InboxError("The server answered %d." % e.code) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        why = getattr(e, "reason", e)
        raise InboxError("Can't reach %s (%s). Showing the last messages fetched."
                         % (urllib.parse.urlparse(url).netloc, why)) from None


def rows_of(data):
    rows = [(m["id"], m["text"], m["contact"], m["page"], m["created"], m["sent"], m["rating"], None)
            for m in data.get("messages", [])]
    rows += [("t:" + t["payment_id"], "Payment " + t["payment_id"], "", "Razorpay",
              t["created"], t["sent"], None, t["amount"]) for t in data.get("tickets", [])]
    return sorted(rows, key=lambda r: r[4], reverse=True)


def read_seen():
    """When the newest item was, last time the window closed (a Unix time)."""
    try:
        with open(SEEN_FILE, encoding="utf-8") as fh:
            v = float(fh.read().strip() or 0)
            return v if v > 1e9 else 0        # an old file held a message number
    except (OSError, ValueError):
        return 0


def write_seen(n):
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(str(n))


class Inbox:
    def __init__(self, root):
        self.root = root
        self.rows = {}
        self.seen = read_seen()
        self.first_seen = self.seen
        self.loading, self.result, self.error, self.last = False, None, None, []
        root.title("Vazhi Messages")
        root.geometry("980x640")
        root.minsize(720, 460)
        root.configure(bg=BG)
        icon = os.path.join(ROOT, "web", "icons", "icon-192.png")
        if os.path.isfile(icon):
            try:
                self._icon = tk.PhotoImage(file=icon)
                root.iconphoto(True, self._icon)
            except tk.TclError:
                pass

        style = ttk.Style(root)
        style.theme_use("clam")
        base = ("Segoe UI", 11)
        style.configure(".", background=BG, foreground=INK, font=base)
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK,
                        rowheight=34, borderwidth=0, font=base)
        style.configure("Treeview.Heading", background=BG, foreground=FAINT, relief="flat",
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#24324a")], foreground=[("selected", INK)])
        style.map("Treeview.Heading", background=[("active", BG)])
        style.configure("TRadiobutton", background=BG, foreground=DIM, font=base)
        style.map("TRadiobutton", foreground=[("selected", INK)], background=[("active", BG)])

        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=20, pady=(16, 8))
        tk.Label(top, text="Messages from riders", bg=BG, fg=INK, font=("Segoe UI Semibold", 18)).pack(side="left")
        self.summary = tk.Label(top, text="", bg=BG, fg=DIM, font=base)
        self.summary.pack(side="left", padx=16)
        self.show = tk.StringVar(value="all")
        for value, label in (("all", "All"), ("msg", "Messages"), ("rate", "Trip ratings"), ("tk", "Bus tickets")):
            ttk.Radiobutton(top, text=label, value=value, variable=self.show,
                            command=lambda: self.draw(self.last)).pack(side="right", padx=6)

        self.status = tk.Label(root, text="", bg=BG, fg=AMBER, font=("Segoe UI", 10), anchor="w",
                               justify="left", wraplength=920)
        self.status.pack(fill="x", padx=20)
        body = tk.PanedWindow(root, orient="vertical", bg=BG, sashwidth=6, bd=0)
        body.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        frame = tk.Frame(body, bg=PANEL)
        cols = ("when", "kind", "from", "text")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for col, title, width, stretch in (("when", "WHEN", 150, False), ("kind", "TYPE", 120, False),
                                           ("from", "FROM", 170, False), ("text", "MESSAGE", 400, True)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=width, stretch=stretch, anchor="w")
        self.tree.tag_configure("new", font=("Segoe UI Semibold", 11), foreground=INK)
        self.tree.tag_configure("old", foreground=DIM)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.select)
        body.add(frame, stretch="always", minsize=160)

        detail = tk.Frame(body, bg=BG)
        self.head = tk.Label(detail, text="", bg=BG, fg=AMBER, font=("Segoe UI Semibold", 13), anchor="w")
        self.head.pack(fill="x", pady=(10, 4))
        self.text = tk.Text(detail, height=7, wrap="word", bg=PANEL, fg=INK, insertbackground=INK,
                            relief="flat", padx=14, pady=12, font=("Segoe UI", 12), highlightthickness=0)
        self.text.pack(fill="both", expand=True)
        self.meta = tk.Label(detail, text="", bg=BG, fg=FAINT, font=("Segoe UI", 10), anchor="w", justify="left")
        self.meta.pack(fill="x", pady=(6, 0))
        body.add(detail, stretch="never", minsize=150)

        root.bind("<F5>", lambda e: self.refresh())
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.tick()
        self.poll()

    def refresh(self):
        """Fetch in the background (a server can be slow); poll() draws it."""
        if self.loading:
            return
        self.loading = True

        def work():
            try:
                self.result = (load(), None)
            except InboxError as e:
                self.result = (None, str(e))
            except Exception as e:                # a broken file must not kill the window
                self.result = (None, "Couldn't read the messages: %s" % e)
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        if self.result is not None:
            rows, err = self.result
            self.result, self.loading, self.error = None, False, err
            if rows is not None:
                self.last = rows
            self.draw(self.last)
        self.root.after(200, self.poll)

    def draw(self, rows):
        self.rows = {str(r[0]): r for r in rows}
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        show = self.show.get()
        ratings = [r[6] for r in rows if r[6]]
        paid = [r for r in rows if r[7] is not None]
        new = 0
        for r in rows:
            key, text, contact, page, created, sent, rating, rupees = r
            ticket = rupees is not None
            kind_of = "tk" if ticket else ("rate" if rating else "msg")
            if show != "all" and show != kind_of:
                continue
            is_new = created > self.first_seen
            new += is_new
            when = time.strftime("%d %b, %I:%M %p", time.localtime(created))
            kind = ("Ticket ₹%d" % rupees) if ticket else (stars(rating) if rating else "Message")
            line = " ".join((text or ("(no comment)" if rating else "")).split())
            self.tree.insert("", "end", iid=str(key), values=(when, kind, contact or "-", line[:160]),
                             tags=("new" if is_new else "old",))
        msgs = len(rows) - len(ratings) - len(paid)
        parts = ["%d message%s" % (msgs, "" if msgs == 1 else "s")]
        if ratings:
            parts.append("%d rating%s, average %.1f★" % (len(ratings), "" if len(ratings) == 1 else "s",
                                                            sum(ratings) / len(ratings)))
        if paid:
            parts.append("₹%d from %d ticket%s" % (sum(r[7] for r in paid), len(paid),
                                                        "" if len(paid) == 1 else "s"))
        if new:
            parts.append("%d new" % new)
        text = "  ·  ".join(parts) if rows else "No messages yet"
        where = urllib.parse.urlparse(server()).netloc if server() else "this computer"
        self.summary.configure(text=text)
        self.status.configure(text=self.error or "Reading from " + where,
                              fg=AMBER if self.error else FAINT)
        self.root.title("Vazhi Messages" + (" (%d new)" % new if new else ""))
        if keep and keep[0] in self.rows and self.tree.exists(keep[0]):
            self.tree.selection_set(keep[0])
        elif not rows:
            self.show_detail(None)
        if rows:
            self.seen = max(self.seen, rows[0][4])   # the newest one's time

    def select(self, _event=None):
        sel = self.tree.selection()
        self.show_detail(self.rows.get(sel[0]) if sel else None)

    def show_detail(self, r):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if not r:
            self.head.configure(text="")
            self.meta.configure(text="Riders' messages and trip ratings show up here. "
                                     "The list checks for new ones by itself (F5 checks now).")
        else:
            key, text, contact, page, created, sent, rating, rupees = r
            when = time.strftime("%A %d %B %Y, %I:%M %p", time.localtime(created))
            tg = "sent to Telegram" if sent else "not sent to Telegram yet"
            if rupees is not None:
                self.head.configure(text="Bus ticket  ₹%d" % rupees)
                self.text.insert("1.0", "Someone bought you a ₹%d bus ticket through Razorpay.\n\n%s"
                                 % (rupees, text))
                self.meta.configure(text="%s     ·     checked with Razorpay's signature     ·     %s" % (when, tg))
            else:
                self.head.configure(text=("Trip rating  " + stars(rating) + "  %d/5" % rating) if rating else "Message")
                self.text.insert("1.0", text or "(no comment)")
                self.meta.configure(text="From: %s     ·     %s     ·     from the %s     ·     #%d     ·     %s"
                                    % (contact or "no name given", when, page or "app", key, tg))
        self.text.configure(state="disabled")

    def tick(self):
        self.refresh()
        self.root.after(REMOTE_REFRESH_MS if server() else REFRESH_MS, self.tick)

    def quit(self):
        write_seen(self.seen)                 # next time, only newer ones are bold
        self.root.destroy()


def make_shortcut():
    """'Vazhi Messages' on the Windows desktop, opening this window."""
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    icon = os.path.join(ROOT, "tools", "inbox.ico")
    ps = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Vazhi Messages.lnk'));"
        "$s.TargetPath='%s';$s.Arguments='\"%s\"';$s.WorkingDirectory='%s';"
        "$s.Description='Messages and trip ratings from Vazhi riders';"
        "%s$s.Save();Write-Output (Join-Path $d 'Vazhi Messages.lnk')"
        % (pythonw, os.path.abspath(__file__), ROOT,
           ("$s.IconLocation='%s';" % icon) if os.path.isfile(icon) else ""))
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    print(out.stdout.strip() or out.stderr.strip())
    return out.returncode


def setup(url):
    """Make the inbox password (once) and remember which server to ask."""
    if url:
        url = url.strip().rstrip("/")
        if "://" not in url:
            url = "https://" + url
        if not safe_url(url):
            print("Use an https:// address (or http://127.0.0.1:... for this computer).")
            return 2
    if len(settings.get("VAZHI_INBOX_TOKEN")) < 24:
        settings.put("VAZHI_INBOX_TOKEN", secrets.token_urlsafe(32))
        print("Made a new inbox password and saved it in data/vazhi.env (VAZHI_INBOX_TOKEN).")
    else:
        print("Keeping the inbox password already in data/vazhi.env (VAZHI_INBOX_TOKEN).")
    if url:
        settings.put("VAZHI_INBOX_URL", url)
        print("Vazhi Messages will now read from %s." % url)
        print("On that server, put the same VAZHI_INBOX_TOKEN line in its data/vazhi.env and restart it.")
    return 0


def go_local():
    settings.put("VAZHI_INBOX_URL", "")
    print("Vazhi Messages will read data/feedback.db on this computer again.")
    return 0


if __name__ == "__main__":
    if "--shortcut" in sys.argv:
        sys.exit(make_shortcut())
    if "--setup" in sys.argv:
        rest = sys.argv[sys.argv.index("--setup") + 1:]
        sys.exit(setup(rest[0] if rest else ""))
    if "--local" in sys.argv:
        sys.exit(go_local())
    app = tk.Tk()
    Inbox(app)
    app.mainloop()
