"""Who runs Idavazhi and how to reach them, for the policy pages.

The pages /privacy, /terms, /refunds and /contact (web/*.html) hold
{{OWNER}}, {{PLACE}}, {{CONTACT_LINE}} and {{CONTACT_LIST}}; the server fills
them from data/vazhi.env so a personal email or phone number never goes into
git:

    VAZHI_OWNER=Your full name             (default: Ashil J Thomas)
    VAZHI_CONTACT_EMAIL=you@example.com
    VAZHI_CONTACT_PHONE=+91 98xxxxxxxx
    VAZHI_ADDRESS=Town, District, Kerala   (default: Kerala, India)

Razorpay checks a site's contact page for an email and phone before it
switches on live payments. Without them the pages point to the message form.
"""

from __future__ import annotations

import html
import re

from . import settings

PAGES = {"/privacy": "privacy.html", "/terms": "terms.html",
         "/refunds": "refunds.html", "/contact": "contact.html"}

_EMAIL = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
_PHONE = re.compile(r"^\+?[0-9][0-9 -]{6,18}[0-9]$")


def details() -> dict:
    """The settings, checked: anything malformed counts as not set."""
    email = (settings.get("VAZHI_CONTACT_EMAIL") or "").strip()
    phone = (settings.get("VAZHI_CONTACT_PHONE") or "").strip()
    return {
        "owner": (settings.get("VAZHI_OWNER") or "").strip()[:80] or "Ashil J Thomas",
        "email": email if _EMAIL.match(email) else "",
        "phone": phone if _PHONE.match(phone) else "",
        "address": (settings.get("VAZHI_ADDRESS") or "").strip()[:160],
    }


def fill(page: str) -> str:
    """The page with its {{...}} spots filled in, HTML-escaped."""
    d = details()
    e = {k: html.escape(v) for k, v in d.items()}
    mail = '<a href="mailto:%s">%s</a>' % (e["email"], e["email"]) if d["email"] else ""
    tel = ('<a href="tel:%s">%s</a>' % (html.escape(re.sub(r"[ -]", "", d["phone"])), e["phone"])
           if d["phone"] else "")
    form = 'the <a href="/contact">contact page</a>'
    ways = [w for w in (mail and "email " + mail, tel and "phone " + tel) if w]
    line = ("You can reach me by %s, or through %s." % (" or ".join(ways), form) if ways
            else "You can reach me through %s." % form)

    rows = [("Name", e["owner"])]
    if mail:
        rows.append(("Email", mail))
    if tel:
        rows.append(("Phone", tel))
    rows.append(("Address", e["address"] or "Kerala, India"))
    if not ways:
        rows.append(("Message", "Use the form on this page"))
    dl = '<dl class="contact__list">%s</dl>' % "".join(
        "<div><dt>%s</dt><dd>%s</dd></div>" % r for r in rows)

    place = e["address"] or "Kerala, India"
    for key, value in (("OWNER", e["owner"]), ("PLACE", place),
                       ("CONTACT_LINE", line), ("CONTACT_LIST", dl)):
        page = page.replace("{{%s}}" % key, value)
    return page
