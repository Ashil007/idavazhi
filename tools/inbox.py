"""Vazhi Messages: read what riders send you, on this computer.

    pythonw tools/inbox.py          (or the "Vazhi Messages" shortcut on your desktop)
    python tools/inbox.py --shortcut   puts that shortcut on your desktop

Shows the messages from "Tell me what you think" (About Vazhi), the stars
riders give a trip and the bus tickets they buy you (Razorpay), newest first, straight from data/feedback.db - the file
the Vazhi server on this computer saves them in. Ones you haven't seen yet
are in bold. It checks for new ones every 15 seconds.

When Vazhi runs on a cloud server, the messages are saved there instead; then
read them in Telegram (tools/telegram_setup.py), which works on your phone too.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from transitnav import feedback  # noqa: E402

SEEN_FILE = os.path.join(ROOT, "data", "inbox-seen.txt")
REFRESH_MS = 15000

BG, PANEL, LINE, INK, DIM, FAINT, AMBER = "#0d1117", "#171f2b", "#2a3442", "#eef2f7", "#a3b0c2", "#6b7a8f", "#f5b642"


def stars(n):
    return "★" * n + "☆" * (5 - n) if n else ""


def load():
    """Messages, ratings and bus tickets, newest first, as
    (key, text, contact, page, created, sent, rating, rupees) rows; key is the
    message id, or "t:<payment id>" for a ticket."""
    if not os.path.isfile(feedback.DB_PATH):
        return []
    db = feedback._connect()                  # adds the rating column to old files
    try:
        rows = [(r[0],) + tuple(r[1:]) + (None,) for r in db.execute(
            "SELECT id, text, contact, page, created, sent, rating FROM feedback")]
        has_tickets = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'tickets'").fetchone()
        if has_tickets:
            rows += [("t:" + pid, "Payment " + pid, "", "Razorpay", created, sent, None, amount)
                     for pid, amount, created, sent in db.execute(
                         "SELECT payment_id, amount, created, sent FROM tickets")]
    finally:
        db.close()
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
                            command=self.refresh).pack(side="right", padx=6)

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
        self.refresh()
        self.tick()

    def refresh(self):
        rows = load()
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
        self.summary.configure(text="  ·  ".join(parts) if rows else "No messages yet")
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
                                     "The list checks for new ones every 15 seconds (F5 checks now).")
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
        self.root.after(REFRESH_MS, self.tick)

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


if __name__ == "__main__":
    if "--shortcut" in sys.argv:
        sys.exit(make_shortcut())
    app = tk.Tk()
    Inbox(app)
    app.mainloop()
