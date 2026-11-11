/* About Vazhi: why it exists, a box to message the maker, and
 * "Buy me a bus ticket".
 *
 * - The story: the maker fell asleep on a bus and woke 10 km past his stop.
 * - "Tell me what you think": saved on the server and sent to the maker's
 *   Telegram (feedback.py). Without internet it waits on the phone
 *   (outbox.js) and goes when the phone is back online.
 * - "Buy me a bus ticket": a small amount by UPI. Kerala's minimum bus fare
 *   is Rs 10, so that is the smallest ticket. On a phone a button opens the
 *   UPI app (GPay, PhonePe, Paytm...); everywhere a QR code can be scanned.
 *   The UPI ID and an optional card-payment link come from the server
 *   (/api/support), so they live in the owner's settings, not in this file.
 */
var ABOUT = (function () {
  "use strict";

  var MAKER = "Ashil J Thomas";
  var TICKETS = [
    { amount: 10, label: "Minimum fare" },
    { amount: 30, label: "Town trip" },
    { amount: 50, label: "Long ride" }
  ];
  var MAX_TEXT = 1000;

  var box = null, lastFocus = null, support = null;
  var choice = String(TICKETS[0].amount), amount = TICKETS[0].amount;

  function el(id) { return document.getElementById(id); }
  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }
  function isPhone() { return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || ""); }

  var TICKET_ICON = '<svg class="ticket__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M3 8a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 0-2 2H5a2 2 0 0 0-2-2v-2a2 2 0 0 0 0-4Z"/>' +
    '<path d="M14 6v2M14 11v2M14 16v2"/></svg>';

  function build() {
    box = document.createElement("div");
    box.className = "about";
    box.hidden = true;
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-labelledby", "about-title");
    box.innerHTML =
      '<div class="about__card">' +
      '  <button type="button" class="about__close" aria-label="Close">&times;</button>' +
      '  <p class="about__eyebrow">About Vazhi</p>' +
      '  <h2 class="about__title" id="about-title">Why I made Vazhi</h2>' +
      '  <p class="about__story">I\'m Ashil, a student from Kerala. One day I fell asleep on the bus ' +
      '  and woke up 10&nbsp;km after my stop, and had to find my way back from there. No app could ' +
      '  simply wake me before my stop, so I built one. Tell Vazhi where you\'re going: it finds your ' +
      '  bus, train or metro and rings an alarm before your stop, so you can rest on the way and ' +
      '  still get off at the right place.</p>' +
      '  <p class="about__by">&mdash; ' + esc(MAKER) + ', maker of Vazhi</p>' +

      '  <section class="about__sec" id="about-feedback" aria-labelledby="about-fb-title">' +
      '    <h3 id="about-fb-title">Tell me what you think</h3>' +
      '    <p class="about__hint">A wrong route, a missing stop, an idea &mdash; I read every message.</p>' +
      '    <form class="about__form" novalidate>' +
      '      <label class="sr-only" for="about-text">Your message</label>' +
      '      <textarea id="about-text" name="text" rows="4" maxlength="' + MAX_TEXT + '" ' +
      '        placeholder="Type your message here"></textarea>' +
      '      <div class="about__count" id="about-count">0 / ' + MAX_TEXT + '</div>' +
      '      <label class="sr-only" for="about-contact">Your name or contact (optional)</label>' +
      '      <input id="about-contact" name="contact" maxlength="120" autocomplete="name" ' +
      '        placeholder="Your name, phone or email (optional, for a reply)" />' +
      '      <button type="submit" class="about__send">Send message</button>' +
      '      <p class="about__msg" id="about-msg" aria-live="polite"></p>' +
      '    </form>' +
      '  </section>' +

      '  <section class="about__sec ticket" id="about-ticket" aria-labelledby="about-tk-title">' +
      '    <h3 id="about-tk-title">' + TICKET_ICON + 'Buy me a bus ticket</h3>' +
      '    <p class="about__hint">Vazhi is free and has no ads. If it ever saved you from missing your ' +
      '    stop, a ticket helps keep its server running.</p>' +
      '    <div class="ticket__amts" role="radiogroup" aria-label="Ticket">' +
      TICKETS.map(function (t) {
        return '<button type="button" role="radio" class="ticket__amt" data-amount="' + t.amount + '">' +
               '<b>&#8377;' + t.amount + '</b><span>' + esc(t.label) + '</span></button>';
      }).join("") +
      '      <button type="button" role="radio" class="ticket__amt" data-amount="other">' +
      '        <b>Other</b><span>Your amount</span></button>' +
      '    </div>' +
      '    <label class="ticket__other" id="ticket-other" hidden>&#8377; ' +
      '      <input type="number" id="ticket-other-amt" min="1" max="100000" step="1" inputmode="numeric" ' +
      '        aria-label="Your amount in rupees" placeholder="100" /></label>' +
      '    <div class="ticket__pay" id="ticket-pay"></div>' +
      '  </section>' +
      '</div>';
    document.body.appendChild(box);

    box.addEventListener("click", function (e) { if (e.target === box) close(); });
    box.querySelector(".about__close").addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (!box.hidden && e.key === "Escape") close();
    });

    var text = el("about-text"), count = el("about-count");
    text.addEventListener("input", function () { count.textContent = text.value.length + " / " + MAX_TEXT; });
    box.querySelector(".about__form").addEventListener("submit", sendFeedback);

    Array.prototype.forEach.call(box.querySelectorAll(".ticket__amt"), function (b) {
      b.addEventListener("click", function () { pick(b.getAttribute("data-amount")); });
    });
    el("ticket-other-amt").addEventListener("input", function () {
      var v = Math.floor(Number(el("ticket-other-amt").value));
      amount = v >= 1 && v <= 100000 ? v : 0;
      renderPay();
    });
  }

  /* ------------------------------------------------------------ feedback */

  function sendFeedback(e) {
    e.preventDefault();
    var form = e.target, msg = el("about-msg"), btn = form.querySelector(".about__send");
    var text = el("about-text").value.trim();
    if (text.length < 3) { msg.className = "about__msg is-bad"; msg.textContent = "Write a few words first."; return; }
    btn.disabled = true;
    msg.className = "about__msg";
    msg.textContent = "Sending...";
    OUTBOX.post("/api/feedback", { text: text, contact: el("about-contact").value.trim(), page: "app" })
      .then(function (j) {
        form.reset();
        el("about-count").textContent = "0 / " + MAX_TEXT;
        msg.className = "about__msg is-ok";
        msg.textContent = j.queued
          ? "No internet right now - your message is saved on this phone and will be sent when you're back online."
          : "Thank you! Your message is sent - Ashil reads every one.";
      })
      .catch(function (err) { msg.className = "about__msg is-bad"; msg.textContent = err.message; })
      .then(function () { btn.disabled = false; });
  }

  /* -------------------------------------------------------- bus ticket */

  function pick(which) {
    var other = which === "other";
    choice = String(which);
    Array.prototype.forEach.call(box.querySelectorAll(".ticket__amt"), function (b) {
      var on = b.getAttribute("data-amount") === String(which);
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
    el("ticket-other").hidden = !other;
    if (other) {
      var v = Math.floor(Number(el("ticket-other-amt").value));
      amount = v >= 1 && v <= 100000 ? v : 0;
      el("ticket-other-amt").focus();
    } else {
      amount = Number(which);
    }
    renderPay();
  }

  function upiLink(amt) {
    return "upi://pay?pa=" + support.upi + "&pn=" + encodeURIComponent(support.name) +
           "&am=" + amt.toFixed(2) + "&cu=INR&tn=" + encodeURIComponent("Vazhi bus ticket");
  }

  // The QR code as an SVG: dark squares on white with a quiet margin, so any
  // UPI app's scanner reads it on Vazhi's dark screen.
  function qrSvg(text) {
    var q = qrcode(0, "M");
    q.addData(text);
    q.make();
    var n = q.getModuleCount(), pad = 4, d = "";
    for (var y = 0; y < n; y++) {
      for (var x = 0; x < n; x++) {
        if (q.isDark(y, x)) d += "M" + (x + pad) + " " + (y + pad) + "h1v1h-1z";
      }
    }
    var size = n + pad * 2;
    return '<svg class="ticket__qr" viewBox="0 0 ' + size + ' ' + size + '" role="img" ' +
           'aria-label="UPI QR code" shape-rendering="crispEdges">' +
           '<rect width="' + size + '" height="' + size + '" fill="#fff"/>' +
           '<path d="' + d + '" fill="#0d1117"/></svg>';
  }

  function renderPay() {
    var pay = el("ticket-pay");
    if (!support) { pay.innerHTML = '<p class="ticket__note">Loading...</p>'; return; }
    if (!support.upi && !support.link) {
      pay.innerHTML = '<p class="ticket__note">Tickets open soon. Thank you for wanting to help!</p>';
      return;
    }
    var html = "";
    if (support.upi) {
      if (!amount) {
        html += '<p class="ticket__note">Type an amount in rupees.</p>';
      } else {
        var link = upiLink(amount);
        if (isPhone()) {
          html += '<a class="ticket__open" href="' + esc(link) + '">Pay &#8377;' + amount +
                  ' with a UPI app</a>';
        }
        html += '<div class="ticket__scan">' + qrSvg(link) +
                '<p>Scan with GPay, PhonePe, Paytm or any UPI app to pay <b>&#8377;' + amount + '</b>.</p></div>';
      }
      html += '<p class="ticket__id">UPI ID: <b>' + esc(support.upi) + '</b>' +
              '<button type="button" class="ticket__copy" id="ticket-copy">Copy</button></p>';
    }
    if (support.link) {
      html += '<a class="ticket__card" href="' + esc(support.link) + '" target="_blank" rel="noopener">' +
              'Pay by card or other ways</a>';
    }
    pay.innerHTML = html;
    var copy = el("ticket-copy");
    if (copy) copy.addEventListener("click", function () {
      var done = function () { copy.textContent = "Copied"; setTimeout(function () { copy.textContent = "Copy"; }, 1800); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(support.upi).then(done, function () { copy.textContent = "Copy failed"; });
      } else {
        copy.textContent = "Copy failed";
      }
    });
  }

  function loadSupport() {
    if (support) return;
    fetch("/api/support").then(function (r) { return r.json(); }).then(function (j) {
      support = j || { upi: "", link: "" };
      renderPay();
    }).catch(function () {
      el("ticket-pay").innerHTML = '<p class="ticket__note">Connect to the internet to buy a ticket.</p>';
    });
  }

  /* ------------------------------------------------------- open, close */

  function open(section) {
    if (!box) build();
    lastFocus = document.activeElement;
    box.hidden = false;
    document.body.classList.add("has-about");
    pick(choice);
    loadSupport();
    var target = section === "ticket" ? el("about-ticket") : null;
    if (target) target.scrollIntoView({ block: "start" });
    else box.scrollTop = 0;
    box.querySelector(".about__close").focus({ preventScroll: true });
  }

  function close() {
    box.hidden = true;
    document.body.classList.remove("has-about");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function init() {
    var a = el("about-open"), t = el("ticket-open");
    if (a) a.addEventListener("click", function () { open(); });
    if (t) t.addEventListener("click", function () { open("ticket"); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  return { open: open };
})();
