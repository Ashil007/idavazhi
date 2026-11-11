/* Three small windows over the app:
 *
 * - About Vazhi: why it exists (the maker fell asleep on a bus and woke
 *   10 km past his stop) and a box to message the maker. Messages are saved
 *   on the server and sent to the maker's Telegram (feedback.py); without
 *   internet they wait on the phone (outbox.js).
 * - Buy me a bus ticket: a small amount through Razorpay Checkout (UPI apps,
 *   card, net banking) when the owner has Razorpay keys; otherwise by UPI. Kerala's minimum bus fare is
 *   Rs 10, so that is the smallest ticket. "Pay" opens the UPI app on a phone
 *   and shows a QR code to scan on a computer. The UPI ID and an optional
 *   card-payment link come from the server (/api/support), so they live in
 *   the owner's settings, not in this file.
 * - How was your trip?: pops up when a trip ends at the rider's stop. One to
 *   five stars and an optional comment go to the maker like a message; below,
 *   a way to buy a bus ticket. After a real trip it asks at most once a day.
 */
var ABOUT = (function () {
  "use strict";

  var MAKER = "Ashil J Thomas";
  var TICKETS = [
    { amount: 10, label: "Minimum fare" },
    { amount: 30, label: "Town trip" },
    { amount: 50, label: "Long ride" }
  ];
  var STARS = ["Bad", "Poor", "Okay", "Good", "Great"];
  var MAX_TEXT = 1000;
  var ASK_EVERY_MS = 20 * 3600 * 1000;      // real trips: one rating pop-up a day at most
  var ASKED_KEY = "vazhi.rateAsked";

  var support = null;
  var choice = String(TICKETS[0].amount), amount = TICKETS[0].amount, showScan = false;
  var stars = 0, rateDemo = false;

  function el(id) { return document.getElementById(id); }
  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }
  function isPhone() { return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || ""); }
  function isIOS() { return /iPhone|iPad|iPod/i.test(navigator.userAgent || ""); }

  // UPI apps a phone can jump straight into, payment filled in. On Android an
  // intent: link names the app (and goes to the Play Store if it is missing);
  // on iPhone each app has its own link.
  var APPS = [
    { name: "Google Pay", dot: "#4285f4", android: "com.google.android.apps.nbu.paisa.user", ios: "gpay://upi/pay" },
    { name: "PhonePe",    dot: "#5f259f", android: "com.phonepe.app",                      ios: "phonepe://pay" },
    { name: "Paytm",      dot: "#00baf2", android: "net.one97.paytm",                      ios: "paytmmp://pay" }
  ];

  var TICKET_ICON = '<svg class="ticket__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M3 8a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 0-2 2H5a2 2 0 0 0-2-2v-2a2 2 0 0 0 0-4Z"/>' +
    '<path d="M14 6v2M14 11v2M14 16v2"/></svg>';
  var STAR_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.8l2.8 5.8 6.3.9-4.6 4.4 1.1 6.3L12 17.2l-5.6 3 1.1-6.3-4.6-4.4 6.3-.9z"/></svg>';

  /* ------------------------------------------------------- windows */

  // One window: built on first use, closed by its x, Esc or a tap outside.
  var open_ = [];
  function dialog(id, labelId, html, wire) {
    var box = null, lastFocus = null;
    function build() {
      box = document.createElement("div");
      box.className = "about";
      box.id = id;
      box.hidden = true;
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-labelledby", labelId);
      box.innerHTML = '<div class="about__card">' +
        '<button type="button" class="about__close" aria-label="Close">&times;</button>' + html + '</div>';
      document.body.appendChild(box);
      box.addEventListener("click", function (e) { if (e.target === box) hide(); });
      box.querySelector(".about__close").addEventListener("click", hide);
      if (wire) wire(box);
    }
    function show() {
      if (!box) build();
      lastFocus = document.activeElement;
      box.hidden = false;
      box.scrollTop = 0;
      open_.push(api);
      document.body.classList.add("has-about");
      box.querySelector(".about__close").focus({ preventScroll: true });
    }
    function hide() {
      if (!box || box.hidden) return;
      box.hidden = true;
      open_ = open_.filter(function (d) { return d !== api; });
      if (!open_.length) document.body.classList.remove("has-about");
      if (lastFocus && lastFocus.focus && document.contains(lastFocus)) lastFocus.focus();
    }
    var api = { show: show, hide: hide, box: function () { return box; } };
    return api;
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && open_.length) open_[open_.length - 1].hide();
  });

  /* --------------------------------------------------------- about */

  var about = dialog("about", "about-title",
    '<p class="about__eyebrow">About Vazhi</p>' +
    '<h2 class="about__title" id="about-title">Why I made Vazhi</h2>' +
    '<p class="about__story">I\'m Ashil, a student from Kerala. One day I fell asleep on the bus ' +
    'and woke up 10&nbsp;km after my stop, and had to find my way back from there. No app could ' +
    'simply wake me before my stop, so I built one. Tell Vazhi where you\'re going: it finds your ' +
    'bus, train or metro and rings an alarm before your stop, so you can rest on the way and ' +
    'still get off at the right place.</p>' +
    '<p class="about__by">&mdash; ' + esc(MAKER) + ', maker of Vazhi</p>' +
    '<section class="about__sec" aria-labelledby="about-fb-title">' +
    '  <h3 id="about-fb-title">Tell me what you think</h3>' +
    '  <p class="about__hint">A wrong route, a missing stop, an idea &mdash; I read every message.</p>' +
    '  <form class="about__form" novalidate>' +
    '    <label class="sr-only" for="about-text">Your message</label>' +
    '    <textarea id="about-text" name="text" rows="4" maxlength="' + MAX_TEXT + '" ' +
    '      placeholder="Type your message here"></textarea>' +
    '    <div class="about__count" id="about-count">0 / ' + MAX_TEXT + '</div>' +
    '    <label class="sr-only" for="about-contact">Your name or contact (optional)</label>' +
    '    <input id="about-contact" name="contact" maxlength="120" autocomplete="name" ' +
    '      placeholder="Your name, phone or email (optional, for a reply)" />' +
    '    <button type="submit" class="about__send">Send message</button>' +
    '    <p class="about__msg" id="about-msg" aria-live="polite"></p>' +
    '  </form>' +
    '</section>',
    function (box) {
      var text = el("about-text"), count = el("about-count");
      text.addEventListener("input", function () { count.textContent = text.value.length + " / " + MAX_TEXT; });
      box.querySelector(".about__form").addEventListener("submit", sendFeedback);
    });

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

  /* ---------------------------------------------------- bus ticket */

  var ticket = dialog("ticket", "ticket-title",
    '<p class="about__eyebrow">Support Vazhi</p>' +
    '<h2 class="about__title ticket__title" id="ticket-title">' + TICKET_ICON + 'Buy me a bus ticket</h2>' +
    '<p class="about__story">Vazhi is free and has no ads. If it ever saved you from missing your ' +
    'stop, a ticket helps keep its server running.</p>' +
    '<div class="ticket__amts" role="radiogroup" aria-label="Ticket">' +
    TICKETS.map(function (t) {
      return '<button type="button" role="radio" class="ticket__amt" data-amount="' + t.amount + '">' +
             '<b>&#8377;' + t.amount + '</b><span>' + esc(t.label) + '</span></button>';
    }).join("") +
    '  <button type="button" role="radio" class="ticket__amt" data-amount="other">' +
    '    <b>Other</b><span>Your amount</span></button>' +
    '</div>' +
    '<label class="ticket__other" id="ticket-other" hidden>&#8377; ' +
    '  <input type="number" id="ticket-other-amt" min="1" max="100000" step="1" inputmode="numeric" ' +
    '    aria-label="Your amount in rupees" placeholder="100" /></label>' +
    '<button type="button" class="ticket__paybtn" id="ticket-paybtn">Pay &#8377;10</button>' +
    '<div class="ticket__pay" id="ticket-pay" aria-live="polite"></div>',
    function (box) {
      Array.prototype.forEach.call(box.querySelectorAll(".ticket__amt"), function (b) {
        b.addEventListener("click", function () { pick(b.getAttribute("data-amount")); });
      });
      el("ticket-other-amt").addEventListener("input", function () {
        var v = Math.floor(Number(el("ticket-other-amt").value));
        amount = v >= 1 && v <= 100000 ? v : 0;
        renderPay();
      });
      el("ticket-paybtn").addEventListener("click", pay);
    });

  function pick(which) {
    var other = which === "other";
    choice = String(which);
    Array.prototype.forEach.call(ticket.box().querySelectorAll(".ticket__amt"), function (b) {
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

  function upiQuery(amt) {
    return "pa=" + support.upi + "&pn=" + encodeURIComponent(support.name) +
           "&am=" + amt.toFixed(2) + "&cu=INR&tn=" + encodeURIComponent("Vazhi bus ticket");
  }
  function upiLink(amt) { return "upi://pay?" + upiQuery(amt); }
  function appLink(app, amt) {
    if (isIOS()) return app.ios + "?" + upiQuery(amt);
    return "intent://pay?" + upiQuery(amt) + "#Intent;scheme=upi;package=" + app.android + ";end";
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

  // "Pay": on a phone the UPI app opens; the QR code and UPI ID show too, for
  // a computer or a phone where no UPI app answered.
  function pay() {
    if (!amount) { el("ticket-other-amt").focus(); return; }
    if (support && support.razorpay) { razorpay(); return; }
    showScan = true;
    renderPay();
  }

  /* Razorpay: the server makes an order for the amount, Razorpay's own
     window takes the payment, and the server checks Razorpay's signature
     before it counts. checkout.js loads only when someone pays. */
  var rzpLoading = null;
  function loadCheckout() {
    if (window.Razorpay) return Promise.resolve();
    if (!rzpLoading) {
      rzpLoading = new Promise(function (ok, fail) {
        var sc = document.createElement("script");
        sc.src = "https://checkout.razorpay.com/v1/checkout.js";
        sc.onload = ok;
        sc.onerror = function () { rzpLoading = null; fail(new Error("Couldn't open the payment window. Check your internet and try again.")); };
        document.head.appendChild(sc);
      });
    }
    return rzpLoading;
  }
  function postJSON(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) throw new Error(j.error || "Something went wrong. Please try again.");
          return j;
        });
      });
  }
  function payNote(text, cls) {
    el("ticket-pay").innerHTML = '<p class="ticket__note ' + (cls || "") + '">' + esc(text) + '</p>';
  }
  function razorpay() {
    var btn = el("ticket-paybtn"), rupees = amount;
    btn.disabled = true;
    payNote("Opening the payment window...");
    Promise.all([loadCheckout(), postJSON("/api/ticket/order", { amount: rupees })])
      .then(function (r) {
        var o = r[1];
        var rzp = new window.Razorpay({
          key: o.key, order_id: o.order_id, amount: o.amount, currency: o.currency,
          name: "Vazhi", description: "A bus ticket for Vazhi's maker",
          image: location.origin + "/icons/icon-192.png",
          notes: { for: "Vazhi bus ticket" },
          theme: { color: "#f5b642" },
          handler: function (res) {
            payNote("Checking your payment...");
            postJSON("/api/ticket/paid", res).then(function () {
              el("ticket-pay").innerHTML = '<div class="ticket__thanks"><b>Thank you!</b>' +
                '<p>Your &#8377;' + rupees + ' bus ticket keeps Vazhi free for everyone.</p></div>';
            }).catch(function () {
              payNote("Paid - thank you! (Vazhi couldn't confirm it just now, but Razorpay has your payment.)", "is-ok");
            });
          },
          modal: { ondismiss: function () { renderPay(); } }
        });
        rzp.on("payment.failed", function (res) {
          payNote((res && res.error && res.error.description) || "The payment didn't go through. You can try again.", "is-bad");
        });
        rzp.open();
        el("ticket-pay").innerHTML = "";
      })
      .catch(function (err) { payNote(err.message, "is-bad"); })
      .then(function () { btn.disabled = !amount; });
  }

  function renderPay() {
    var btn = el("ticket-paybtn"), pay_ = el("ticket-pay");
    btn.textContent = amount ? "Pay ₹" + amount : "Pay";
    btn.disabled = !amount;
    // On a phone the app buttons are the Pay button: one tap opens the app.
    btn.hidden = !!(support && !support.razorpay && support.upi && isPhone());
    if (!support) { pay_.innerHTML = showScan ? '<p class="ticket__note">Loading...</p>' : ""; return; }
    if (support.offline) {
      pay_.innerHTML = '<p class="ticket__note">Connect to the internet to buy a ticket.</p>';
      return;
    }
    var html = "";
    if (support.razorpay) {
      // Razorpay's window offers UPI apps, cards and net banking itself.
      html = '<p class="ticket__note">Pay by UPI (Google Pay, PhonePe, Paytm...), card or net banking ' +
             '&mdash; securely through Razorpay.</p>';
    } else if (!support.upi && !support.link) {
      if (showScan) html = '<p class="ticket__note">Payments are not switched on yet. Thank you for wanting to help!</p>';
    } else if (support.upi && isPhone()) {
      if (!amount) {
        html += '<p class="ticket__note">Type an amount in rupees.</p>';
      } else {
        html += '<p class="ticket__with">Pay <b>&#8377;' + amount + '</b> with</p><div class="ticket__apps">' +
          APPS.map(function (a) {
            return '<a class="ticket__app" href="' + esc(appLink(a, amount)) + '">' +
                   '<i style="background:' + a.dot + '"></i>' + esc(a.name) + '</a>';
          }).join("") +
          '<a class="ticket__app" href="' + esc(upiLink(amount)) + '"><i class="is-any"></i>Other UPI app</a></div>' +
          '<p class="ticket__note">Opens the app with the ticket filled in &mdash; just confirm with your UPI PIN.</p>';
        if (showScan) {
          html += '<div class="ticket__scan">' + qrSvg(upiLink(amount)) +
                  '<p>Scan this from another phone to pay <b>&#8377;' + amount + '</b>.</p></div>' +
                  '<p class="ticket__id">UPI ID: <b>' + esc(support.upi) + '</b>' +
                  '<button type="button" class="ticket__copy" id="ticket-copy">Copy</button></p>';
        } else {
          html += '<button type="button" class="ticket__more" id="ticket-showqr">Paying from another phone? Show the QR code</button>';
        }
      }
    } else if (support.upi && showScan && amount) {
      var link = upiLink(amount);
      html += '<div class="ticket__scan">' + qrSvg(link) +
              '<p>Scan this with your phone &mdash; GPay, PhonePe, Paytm or any UPI app &mdash; to pay <b>&#8377;' +
              amount + '</b>.</p></div>' +
              '<p class="ticket__id">UPI ID: <b>' + esc(support.upi) + '</b>' +
              '<button type="button" class="ticket__copy" id="ticket-copy">Copy</button></p>' +
              '<p class="ticket__note">Paid? Thank you! Every ticket keeps Vazhi running.</p>';
    }
    if (support.link) {
      html += '<a class="ticket__card" href="' + esc(support.link) + '" target="_blank" rel="noopener">' +
              'Pay by card or other ways</a>';
    }
    pay_.innerHTML = html;
    var more = el("ticket-showqr");
    if (more) more.addEventListener("click", function () { showScan = true; renderPay(); });
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
    if (support && !support.offline) return;
    fetch("/api/support").then(function (r) { return r.json(); }).then(function (j) {
      support = j || { upi: "", link: "" };
      renderPay();
    }).catch(function () {
      support = { offline: true };
      renderPay();
    });
  }

  function openTicket() {
    showScan = false;
    ticket.show();
    pick(choice);
    loadSupport();
  }

  /* ------------------------------------------------- trip rating */

  var rate = dialog("rate", "rate-title",
    '<p class="about__eyebrow" id="rate-where">Trip done</p>' +
    '<h2 class="about__title" id="rate-title">How was your trip?</h2>' +
    '<div class="rate__stars" role="radiogroup" aria-label="Your rating">' +
    STARS.map(function (label, i) {
      return '<button type="button" role="radio" aria-checked="false" class="rate__star" data-n="' + (i + 1) +
             '" aria-label="' + (i + 1) + ' of 5: ' + label + '">' + STAR_ICON + '</button>';
    }).join("") +
    '</div>' +
    '<p class="rate__word" id="rate-word" aria-hidden="true">Tap a star</p>' +
    '<form class="about__form rate__form" id="rate-form" novalidate hidden>' +
    '  <label class="sr-only" for="rate-text">Anything to tell (optional)</label>' +
    '  <textarea id="rate-text" rows="2" maxlength="' + MAX_TEXT + '" ' +
    '    placeholder="Anything to tell? (optional)"></textarea>' +
    '  <button type="submit" class="about__send">Send rating</button>' +
    '</form>' +
    '<p class="about__msg" id="rate-msg" aria-live="polite"></p>' +
    '<section class="about__sec ticket rate__ticket" aria-labelledby="rate-tk-title">' +
    '  <h3 id="rate-tk-title">' + TICKET_ICON + 'Did Vazhi wake you on time?</h3>' +
    '  <p class="about__hint">Vazhi is free with no ads. A bus ticket for its maker keeps it that way.</p>' +
    '  <button type="button" class="ticket__paybtn" id="rate-ticket">Buy me a bus ticket</button>' +
    '</section>',
    function (box) {
      Array.prototype.forEach.call(box.querySelectorAll(".rate__star"), function (b) {
        b.addEventListener("click", function () { setStars(Number(b.getAttribute("data-n"))); });
      });
      el("rate-form").addEventListener("submit", sendRating);
      el("rate-ticket").addEventListener("click", function () { rate.hide(); openTicket(); });
    });

  function setStars(n) {
    stars = n;
    Array.prototype.forEach.call(rate.box().querySelectorAll(".rate__star"), function (b) {
      var k = Number(b.getAttribute("data-n"));
      b.classList.toggle("is-on", k <= n);
      b.setAttribute("aria-checked", k === n ? "true" : "false");
    });
    el("rate-word").textContent = n ? STARS[n - 1] : "Tap a star";
    el("rate-form").hidden = !n;
  }

  function sendRating(e) {
    e.preventDefault();
    if (!stars) return;
    var btn = e.target.querySelector(".about__send"), msg = el("rate-msg");
    btn.disabled = true;
    msg.className = "about__msg";
    msg.textContent = "Sending...";
    OUTBOX.post("/api/feedback", { rating: stars, text: el("rate-text").value.trim(),
                                   page: rateDemo ? "demo trip" : "trip" })
      .then(function (j) {
        el("rate-form").hidden = true;
        msg.className = "about__msg is-ok";
        msg.textContent = j.queued ? "Saved - it will be sent when you're back online. Thank you!"
                                   : "Thank you for rating your trip!";
      })
      .catch(function (err) { msg.className = "about__msg is-bad"; msg.textContent = err.message; })
      .then(function () { btn.disabled = false; });
  }

  function askRating(demo, where) {
    if (!demo) {
      try {
        var last = Number(window.localStorage.getItem(ASKED_KEY) || 0);
        if (Date.now() - last < ASK_EVERY_MS) return;
        window.localStorage.setItem(ASKED_KEY, String(Date.now()));
      } catch (e) { /* no storage: ask anyway */ }
    }
    rateDemo = !!demo;
    rate.show();
    setStars(0);
    el("rate-form").reset();
    el("rate-msg").textContent = "";
    el("rate-where").textContent = (demo ? "Demo ride done" : "Trip done") +
      (where && where !== "your stop" ? " · " + where : "");
  }

  /* ---------------------------------------------------------- start */

  function init() {
    var a = el("about-open"), t = el("ticket-open");
    if (a) a.addEventListener("click", function () { about.show(); });
    if (t) t.addEventListener("click", openTicket);
    document.addEventListener("vazhi:arrived", function (e) {
      var d = e.detail || {};
      setTimeout(function () { askRating(d.demo, d.stop); }, 900);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  return { open: function () { about.show(); }, ticket: openTicket, rate: askRating };
})();
