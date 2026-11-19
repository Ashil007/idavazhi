/* Where is my train? From the riders on it (server: transitnav/livetrains.py).
 *
 * - Sharing: during a train trip with the alarm on, a rider can turn on
 *   "Share where this train is". About every 45 seconds, while the phone is
 *   on that train's track, its position goes to the server with the train
 *   number and a random code for this trip only - nothing that says who it
 *   is. The server keeps it in memory for 20 minutes.
 * - Looking: each train in a planned trip has "Where is this train now?",
 *   which shows where riders last saw it and follows it every 30 seconds. */
var LIVETRAIN = (function () {
  "use strict";

  var SEND_EVERY_MS = 45000, FOLLOW_EVERY_MS = 30000, ON_TRACK_M = 600, MAX_ACCURACY_M = 250;
  var KEY = "vazhi.shareTrain";
  var plan = null, token = null, lastSent = 0, layer = null, follow = null;

  function $(id) { return document.getElementById(id); }
  function esc(t) { var d = document.createElement("div"); d.textContent = t == null ? "" : String(t); return d.innerHTML; }
  function trains(p) {
    return ((p && p.legs) || []).filter(function (l) { return l.type === "transit" && l.train && l.train.number; });
  }

  // Metres from a point to a polyline ([[lat, lon], ...]), flat-earth: fine at a few km.
  function toLine(lat, lon, line) {
    var k = Math.cos(lat * Math.PI / 180) * 111320, best = Infinity;
    for (var i = 1; i < line.length; i++) {
      var ax = (line[i - 1][1] - lon) * k, ay = (line[i - 1][0] - lat) * 111320;
      var bx = (line[i][1] - lon) * k, by = (line[i][0] - lat) * 111320;
      var dx = bx - ax, dy = by - ay, len = dx * dx + dy * dy;
      var t = len ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len)) : 0;
      var px = ax + t * dx, py = ay + t * dy;
      best = Math.min(best, Math.sqrt(px * px + py * py));
    }
    return best;
  }

  function sharing() { try { return localStorage.getItem(KEY) === "1"; } catch (e) { return false; } }

  /* ------------------------------------------------------------ sharing */

  function attach(p) {
    plan = p;
    var row = $("trip-share");
    if (row) row.hidden = !trains(p).length;
    stopFollowing();
  }

  function onFix(e) {
    var f = e.detail || {};
    if (f.simulating || !sharing() || !plan) return;
    if (f.accuracy > MAX_ACCURACY_M || Date.now() - lastSent < SEND_EVERY_MS) return;
    // Only from on the train: the phone must be on one of the trip's train lines.
    var leg = null;
    trains(plan).forEach(function (l) {
      if (!leg && l.geometry && l.geometry.length > 1 && toLine(f.lat, f.lon, l.geometry) <= ON_TRACK_M) leg = l;
    });
    if (!leg) return;
    if (!token) {
      var b = new Uint8Array(12);
      window.crypto.getRandomValues(b);
      token = Array.prototype.map.call(b, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
    }
    lastSent = Date.now();
    fetch("/api/trains/report", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ number: leg.train.number, lat: f.lat, lon: f.lon, token: token })
    }).catch(function () { /* a missed report is fine; the next one follows */ });
  }

  function onTripEnd() { token = null; lastSent = 0; }   // a new code for the next trip

  /* ------------------------------------------------------------ looking */

  function trainIcon() {
    return L.divIcon({ className: "live-train", iconSize: [34, 34], iconAnchor: [17, 17],
      html: '<span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="3" width="14" ' +
            'height="13" rx="3"/><path d="M5 10h14M9 20l-2 2M15 20l2 2M9 16h.01M15 16h.01"/></svg></span>' });
  }

  // How far along a line ([[lat, lon], ...]) the point nearest to (lat, lon) is, in metres.
  function along(line, lat, lon) {
    var k = Math.cos(lat * Math.PI / 180) * 111320, best = Infinity, at = 0, run = 0;
    for (var i = 1; i < line.length; i++) {
      var ax = (line[i - 1][1] - lon) * k, ay = (line[i - 1][0] - lat) * 111320;
      var bx = (line[i][1] - lon) * k, by = (line[i][0] - lat) * 111320;
      var dx = bx - ax, dy = by - ay, len = dx * dx + dy * dy, seg = Math.sqrt(len);
      var t = len ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len)) : 0;
      var px = ax + t * dx, py = ay + t * dy, d = Math.sqrt(px * px + py * py);
      if (d < best) { best = d; at = run + t * seg; }
      run += seg;
    }
    return at;
  }

  // Where on this leg the train was seen, in words: "at Aluva", or "about
  // 6 km after Aluva, towards Thrissur" - measured along the track.
  function whereOnLeg(leg, lat, lon) {
    var line = leg.geometry || [], stations = [leg.from].concat(leg.stops_passed || [], [leg.to]).filter(Boolean);
    if (line.length < 2 || !stations.length) return "on its way";
    var p = along(line, lat, lon), prev = null, next = null;
    stations.forEach(function (s) {
      var m = along(line, s.lat, s.lon);
      if (m <= p && (!prev || m >= prev.m)) prev = { s: s, m: m };
      if (m > p && (!next || m < next.m)) next = { s: s, m: m };
    });
    if (prev && p - prev.m < 1500) return "at " + esc(prev.s.name);
    if (next && next.m - p < 1500) return "at " + esc(next.s.name);
    if (!prev) return "about " + Math.round((next.m - p) / 1000) + " km before " + esc(next.s.name);
    var km = Math.round((p - prev.m) / 1000);
    return "about " + km + " km after " + esc(prev.s.name) + (next ? ", towards " + esc(next.s.name) : "");
  }

  function ago(s) {
    if (s < 60) return "just now";
    var m = Math.round(s / 60);
    return m + " min ago";
  }

  function stopFollowing() {
    if (follow) { clearInterval(follow.timer); follow.btn.textContent = "Where is this train now?"; follow = null; }
    if (layer) layer.clearLayers();
  }

  function look(leg, btn, out) {
    if (follow && follow.btn === btn) { stopFollowing(); out.hidden = true; return; }
    stopFollowing();
    if (!layer) layer = L.layerGroup().addTo(map);
    out.hidden = false;
    out.className = "livetrain__out";
    out.textContent = "Asking riders on the train…";
    btn.textContent = "Stop following";
    var first = true;
    function ask() {
      fetch("/api/trains/live?number=" + encodeURIComponent(leg.train.number))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          layer.clearLayers();
          if (!d.seen) {
            out.className = "livetrain__out is-none";
            out.innerHTML = "No Idavazhi rider on this train is sharing where it is right now. " +
              "On board? Turn on <b>Share where this train is</b> under the alarm, and others can see it.";
            return;
          }
          var where = whereOnLeg(leg, d.lat, d.lon);
          out.className = "livetrain__out is-seen";
          out.innerHTML = "<b>Seen " + where + "</b> " + ago(d.age_s) + " &middot; shared by " +
            d.riders + (d.riders === 1 ? " rider" : " riders") + " on the train";
          L.marker([d.lat, d.lon], { icon: trainIcon(), zIndexOffset: 900 })
            .addTo(layer).bindTooltip(esc(leg.train.number + " " + (leg.train.name || "")) + "<br>Seen " +
                                      ago(d.age_s), { direction: "top", offset: [0, -16] });
          if (first) map.panTo([d.lat, d.lon]);
          first = false;
        })
        .catch(function () {
          out.className = "livetrain__out is-none";
          out.textContent = navigator.onLine === false ? "You are offline." : "Couldn't ask right now - trying again soon.";
        });
    }
    ask();
    follow = { btn: btn, timer: setInterval(ask, FOLLOW_EVERY_MS) };
  }

  // Under a train in the trip list: the button and its answer.
  function legButton(li, leg) {
    if (!leg.train || !leg.train.number) return;
    var box = document.createElement("div");
    box.className = "livetrain";
    box.innerHTML = '<button type="button" class="livetrain__btn">Where is this train now?</button>' +
                    '<p class="livetrain__out" hidden aria-live="polite"></p>';
    var btn = box.querySelector("button"), out = box.querySelector("p");
    btn.addEventListener("click", function () { look(leg, btn, out); });
    li.appendChild(box);
  }

  function init() {
    var box = $("trip-share-on");
    if (box) {
      box.checked = sharing();
      box.addEventListener("change", function () {
        try { localStorage.setItem(KEY, box.checked ? "1" : "0"); } catch (e) { /* this visit only */ }
      });
    }
    document.addEventListener("vazhi:fix", onFix);
    document.addEventListener("vazhi:tripend", onTripEnd);
  }

  return { init: init, attach: attach, legButton: legButton, _toLine: toLine };
})();
