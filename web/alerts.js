/* Trip alerts: "wake me before my stop".
 *
 * After a journey is planned the rider can start trip alerts. The page then
 * follows their position (or, in demo mode, a pretend position moving along
 * the route) and, for every place they must get off - a change of train or
 * the final stop:
 *   - well before it (WAKE_M, or the distance the rider picked): a real
 *     alarm - a looping alarm-clock sound and vibration that go on until
 *     "I'm awake" is tapped, and a notification - the passenger may be asleep;
 *   - on arrival (ARRIVE_M): "this is your stop". It rings like the first
 *     alarm until tapped if that one was never answered; after "I'm awake"
 *     it is a short chime.
 *
 * A web page can only do this while it is open; the screen is kept on while
 * tracking, because phones pause hidden pages.
 */
"use strict";

var ALERTS = (function () {
  var WAKE_M = 2000;        // "auto" (Default): wake the rider this far before the stop
  var SETTING = "vazhi.alarmDistance";   // the rider's own choice, kept on this device
  var ARRIVE_M = 150;       // "this is your stop"
  var SIM_SECONDS = 10;     // demo: the whole trip in about this long

  var plan = null, targets = [], fired = {};
  var watchId = null, simTimer = null, running = false, simulating = false;
  var last = null, speed = null;                  // previous fix and m/s
  var meLayer = null, audio = null, beepTimer = null, wakeLock = null, swReg = null;
  var siren = null;        // the looping alarm sound (an <audio> element)
  var awake = false;       // the rider tapped "I'm awake" during this ride

  var el = function (id) { return document.getElementById(id); };

  function haversine(a, b) {
    var R = 6371000, toRad = Math.PI / 180;
    var dLat = (b[0] - a[0]) * toRad, dLon = (b[1] - a[1]) * toRad;
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(a[0] * toRad) * Math.cos(b[0] * toRad) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  function fmtDist(m) { return m < 1000 ? Math.round(m / 10) * 10 + " m" : (m / 1000).toFixed(1) + " km"; }

  /* ---------------------------------------------------------- the plan */

  // Every place the rider has to get off: each change, then the last stop.
  function targetsOf(p) {
    var rides = p.legs.filter(function (l) { return l.type === "transit"; });
    return rides.map(function (leg, i) {
      var next = rides[i + 1];
      // Everything the rider covers until getting off here: the walk or
      // ride to the first stop and every ride up to this stop.
      var upto = p.legs.slice(0, p.legs.indexOf(leg) + 1), path = [];
      upto.forEach(function (l) { path = path.concat(l.geometry || []); });
      return {
        name: (leg.to && leg.to.name) || "your stop",
        at: [leg.to.lat, leg.to.lon],
        route: routeOf(path),
        final: !next,
        nextLabel: next ? next.label : null,
        vehicle: leg.label
      };
    });
  }

  // A path with the distance left to its end from every point.
  function routeOf(path) {
    var left = new Array(path.length);
    left[path.length - 1] = 0;
    for (var k = path.length - 2; k >= 0; k--) left[k] = left[k + 1] + haversine(path[k], path[k + 1]);
    return { path: path, left: left };
  }

  // Off the route by more than this (the bus took another road), the
  // straight-line distance is used instead.
  var OFF_ROUTE_M = 500;

  // How far is left to the stop along the route, the way the trip's
  // "Total distance" counts it - not as the crow flies, which is shorter
  // on any road that bends. Straight line when off the route.
  function distanceLeft(here, t) {
    var straight = haversine(here, t.at), r = t.route;
    if (!r || r.path.length < 2) return straight;
    var best = null;
    for (var k = 0; k < r.path.length - 1; k++) {
      var a = r.path[k], b = r.path[k + 1];
      var kx = 111320 * Math.cos((a[0] + b[0]) / 2 * Math.PI / 180), ky = 111320;
      var bx = (b[1] - a[1]) * kx, by = (b[0] - a[0]) * ky;
      var px = (here[1] - a[1]) * kx, py = (here[0] - a[0]) * ky;
      var seg = bx * bx + by * by;
      var f = seg ? Math.max(0, Math.min(1, (px * bx + py * by) / seg)) : 0;
      var off = Math.hypot(px - bx * f, py - by * f);
      if (best === null || off < best.off) {
        best = { off: off, left: r.left[k + 1] + Math.sqrt(seg) * (1 - f) };
      }
    }
    return best.off > OFF_ROUTE_M ? straight : best.off + best.left;
  }

  // Called whenever a plan (or another route option) is shown.
  function attach(p) {
    plan = p;
    var box = el("trip");
    if (!box) return;
    var t = targetsOf(p);
    if (!t.length) { box.hidden = true; return; }
    box.hidden = false;
    el("trip-start").textContent = "Wake me before " + t[t.length - 1].name;
    if (running) {            // switched route options mid-trip: follow the new one
      targets = t;
      fired = {};
      update();
    }
  }

  /* ---------------------------------------------------- sound, buzz, note */

  function ensureAudio() {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!audio) audio = new Ctx();
    if (audio.state === "suspended") audio.resume();
    return audio;
  }

  // One "beep-beep-beep" - three short tones, like an alarm clock.
  function beep() {
    var ctx = ensureAudio();
    if (!ctx) return;
    [0, 0.22, 0.44].forEach(function (offset) {
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "square";
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, ctx.currentTime + offset);
      g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + offset + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + offset + 0.16);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + offset);
      o.stop(ctx.currentTime + offset + 0.18);
    });
  }

  // The alarm sound: "beep-beep-beep-beep", a pause, over and over, like an
  // alarm clock. Made here as a WAV file and played by an <audio> element
  // on loop, which keeps sounding while the phone is busy or the page is in
  // the background, where beeps timed by the page get delayed or dropped.
  function alarmTone() {
    var rate = 22050, secs = 1.4, n = Math.floor(rate * secs);
    var buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    function str(o, t) { for (var i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); }
    str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
    str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, "data"); v.setUint32(40, n * 2, true);
    for (var i = 0; i < n; i++) {
      var t = i / rate, k = Math.floor(t / 0.16), u = t - k * 0.16, x = 0;
      if (k < 4 && u < 0.11) {
        var env = Math.min(1, u / 0.005, (0.11 - u) / 0.005);
        var w = 2 * Math.PI * 1760 * t;      // high and piercing, with harmonics
        x = env * (0.62 * Math.sin(w) + 0.25 * Math.sin(3 * w) + 0.13 * Math.sin(5 * w));
      }
      v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x)) * 32000, true);
    }
    return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
  }

  function ensureSiren() {
    if (!siren && typeof Audio !== "undefined") {
      siren = new Audio(alarmTone());
      siren.loop = true;
      siren.preload = "auto";
    }
    return siren;
  }

  // Phones only let a page play sound it was allowed to start from a tap:
  // play the alarm muted for a moment in the tap that starts the trip.
  function unlockSiren() {
    var s = ensureSiren();
    if (!s) return;
    s.muted = true;
    var done = function () { if (!ringing) { s.pause(); s.currentTime = 0; } s.muted = false; };
    var p = s.play();
    if (p && p.then) p.then(done, function () { s.muted = false; }); else done();
  }

  var ringing = false;

  // Ring until silence(): the looping sound (or, if the browser will not
  // play it, the page's own beeps), with vibration, over and over.
  function ring() {
    stopRinging();
    ringing = true;
    vibrate([700, 300, 700, 300, 1200]);
    var s = silent() ? null : ensureSiren();
    var beepToo = !s;
    if (s) {
      s.muted = false;
      s.volume = 1;
      s.currentTime = 0;
      var p = s.play();
      if (p && p.catch) p.catch(function () { beepToo = true; if (ringing && !silent()) beep(); });
    } else if (!silent()) {
      beep();
    }
    beepTimer = setInterval(function () {
      if (beepToo && !silent()) beep();
      vibrate([700, 300, 700]);
    }, 1400);
  }

  function stopRinging() {
    ringing = false;
    clearInterval(beepTimer);
    beepTimer = null;
    if (siren) { siren.pause(); siren.currentTime = 0; }
    if (navigator.vibrate) navigator.vibrate(0);
  }

  function vibrate(pattern) {
    if (navigator.vibrate) navigator.vibrate(pattern);
  }

  function notify(title, body) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    var opts = { body: body, tag: "vazhi-trip", renotify: true, requireInteraction: true,
                 vibrate: [500, 200, 500, 200, 900], silent: false };
    // Android shows notifications only through a service worker.
    if (swReg && swReg.showNotification) swReg.showNotification(title, opts);
    else { try { new Notification(title, opts); } catch (e) { /* not allowed here */ } }
  }

  function silent() { return !!(el("trip-silent") && el("trip-silent").checked); }

  // The full-screen alarm. Loud ones ring until "I'm awake" (only buzz,
  // with "Vibrate only" on - for a quiet bus or a sleeping baby); the rest
  // chime once.
  function alarm(title, body, loud) {
    el("alarm-title").textContent = title;
    el("alarm-body").textContent = body;
    el("alarm").hidden = false;
    el("alarm").classList.toggle("is-loud", !!loud);
    notify(title, body);
    if (loud) { ring(); return; }
    stopRinging();
    vibrate([300, 150, 300]);
    if (!silent()) beep();
  }

  function silence() {
    stopRinging();
    awake = true;
    el("alarm").hidden = true;
    if (targets.length && fired[(targets.length - 1) + ":arrive"]) {
      var demo = simulating, where = targets[targets.length - 1].name;
      stop("You have arrived. Trip alerts are off.");
      // The trip is over: about.js asks how it went (and offers a bus ticket).
      document.dispatchEvent(new CustomEvent("vazhi:arrived", { detail: { demo: demo, stop: where } }));
    }
  }

  /* ----------------------------------------------------- tracking */

  // "auto" (Default): 2 km. Otherwise exactly the distance the rider picked.
  function wakeDistance() {
    var chosen = el("trip-distance") ? el("trip-distance").value : "auto";
    if (chosen !== "auto" && Number(chosen) > 0) return Number(chosen);
    return WAKE_M;
  }

  function loadSetting() {
    var box = el("trip-distance");
    if (!box) return;
    try {
      var saved = window.localStorage.getItem(SETTING);
      if (saved && box.querySelector('option[value="' + saved + '"]')) box.value = saved;
    } catch (e) { /* storage blocked: the default stays */ }
    box.addEventListener("change", function () {
      try { window.localStorage.setItem(SETTING, box.value); } catch (e) { /* not kept */ }
      update();                       // a running trip uses the new distance at once
    });
  }

  /* The alarm distance, in large type: the choice in big numbers, and one
   * button per distance under it (the <select> behind them keeps the value). */
  var RING = {
    auto: ["2", "km", "Default \u00b7 before your stop", "Default"],
    500: ["500", "m", "before your stop", "500 m"],
    1000: ["1", "km", "before your stop", "1 km"],
    3000: ["3", "km", "before your stop", "3 km"],
    5000: ["5", "km", "before your stop", "5 km"],
    10000: ["10", "km", "before your stop", "10 km"]
  };

  function buildRing() {
    var box = el("trip-distance"), chips = el("ring-chips");
    if (!box || !chips) return;
    Array.prototype.forEach.call(box.options, function (o) {
      var r = RING[o.value];
      if (!r) return;
      var b = document.createElement("button");
      b.type = "button";
      b.className = "ring__chip";
      b.setAttribute("role", "radio");
      b.setAttribute("data-value", o.value);
      b.textContent = r[3];
      b.addEventListener("click", function () {
        box.value = o.value;
        box.dispatchEvent(new Event("change"));
      });
      chips.appendChild(b);
    });
    box.addEventListener("change", showRing);
    showRing();
  }

  function showRing() {
    var v = el("trip-distance").value, r = RING[v] || RING.auto;
    el("ring-num").textContent = r[0];
    el("ring-unit").textContent = r[1];
    el("ring-sub").textContent = r[2];
    Array.prototype.forEach.call(el("ring-chips").children, function (b) {
      b.setAttribute("aria-checked", String(b.getAttribute("data-value") === v));
    });
  }

  function nextTarget() {
    for (var i = 0; i < targets.length; i++) {
      if (!fired[i + ":arrive"]) return i;
    }
    return -1;
  }

  function onFix(lat, lon, accuracy, when) {
    var here = [lat, lon];
    // For "Share where this train is" (livetrain.js), which decides itself
    // whether to send anything.
    document.dispatchEvent(new CustomEvent("vazhi:fix", {
      detail: { lat: lat, lon: lon, accuracy: accuracy, simulating: simulating } }));
    if (last) {
      var dt = (when - last.t) / 1000;
      if (dt > 0.5) {
        var v = haversine(last.at, here) / dt;
        speed = speed == null ? v : speed * 0.6 + v * 0.4;   // smoothed
      }
    }
    last = { at: here, t: when };
    showMe(here, accuracy);
    update();
  }

  function update() {
    if (!running || !last) return;
    var i = nextTarget();
    if (i < 0) return;
    var t = targets[i];
    var dist = distanceLeft(last.at, t);
    var secs = speed && speed > 1 ? dist / speed : null;
    var wakeAt = wakeDistance();

    if (dist <= ARRIVE_M && !fired[i + ":arrive"]) {
      fired[i + ":arrive"] = fired[i + ":wake"] = true;
      // Still asleep (the first alarm never answered): ring until tapped.
      if (t.final) alarm("This is your stop", "Get off at " + t.name + ".", !awake);
      else alarm("Change here", "Get off at " + t.name + " and change to " + t.nextLabel + ".", !awake);
      awake = false;   // the next ride may be slept through too
    } else if (dist <= wakeAt && !fired[i + ":wake"]) {
      fired[i + ":wake"] = true;
      awake = false;   // until "I'm awake" is tapped for this alarm
      alarm(t.final ? "Wake up — your stop is coming" : "Wake up — you change soon",
            t.name + " is " + fmtDist(dist) + " away" +
            (secs ? ", about " + Math.max(1, Math.round(secs / 60)) + " min" : "") +
            ". Get ready to get off.", true);
    }
    var eta = secs ? " · about " + Math.max(1, Math.round(secs / 60)) + " min" : "";
    el("trip-status").innerHTML = (simulating ? "<b>Demo ride</b> · " : "<b>Tracking</b> · ") +
      (t.final ? "your stop " : "change at ") + "<b>" + escape(t.name) + "</b>: " +
      fmtDist(dist) + eta;
  }

  function escape(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function showMe(here, accuracy) {
    if (!meLayer) meLayer = L.layerGroup().addTo(map);
    meLayer.clearLayers();
    if (accuracy && accuracy > 15) {
      L.circle(here, { radius: accuracy, color: "#4da3ff", weight: 1, fillOpacity: 0.08,
                       interactive: false }).addTo(meLayer);
    }
    L.marker(here, { icon: L.divIcon({ className: "", html: '<div class="me-dot"></div>',
                                       iconSize: [14, 14], iconAnchor: [7, 7] }),
                     zIndexOffset: 2000, interactive: false }).addTo(meLayer);
  }

  /* ------------------------------------------------------ start / stop */

  function prepare() {
    ensureAudio();                      // must start inside the tap that began the trip
    unlockSiren();
    if ("serviceWorker" in navigator && !swReg) {
      navigator.serviceWorker.register("sw.js").then(function (r) { swReg = r; })
        .catch(function () { /* notifications fall back to the page */ });
    }
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
    keepAwake();
  }

  function keepAwake() {
    if (!("wakeLock" in navigator) || wakeLock) return;
    navigator.wakeLock.request("screen").then(function (lock) {
      wakeLock = lock;
      lock.addEventListener("release", function () { wakeLock = null; });
    }).catch(function () { /* not allowed (battery saver) - tracking still works */ });
  }

  function begin() {
    targets = targetsOf(plan);
    fired = {};
    awake = false;
    last = null;
    speed = null;
    running = true;
    el("trip-start").hidden = true;
    el("trip-sim").hidden = true;
    el("trip-stop").hidden = false;
    el("trip-status").hidden = false;
    el("trip-status").textContent = "Starting…";
  }

  function start() {
    if (!plan || running) return;
    if (!("geolocation" in navigator)) {
      el("trip-status").hidden = false;
      el("trip-status").textContent = "This browser cannot share your location, so trip alerts cannot run.";
      return;
    }
    prepare();
    begin();
    el("trip-status").textContent = "Waiting for your location…";
    watchId = navigator.geolocation.watchPosition(function (pos) {
      onFix(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy, pos.timestamp || Date.now());
    }, function (err) {
      stop(err && err.code === 1
        ? "Location is blocked, so trip alerts cannot follow you. Allow location for this site and try again."
        : "Your location could not be found. Trip alerts are off.");
    }, { enableHighAccuracy: true, maximumAge: 5000, timeout: 30000 });
  }

  // Demo mode: a pretend rider moves along the planned route.
  function simulate() {
    if (!plan || running) return;
    prepare();
    begin();
    simulating = true;
    var path = [];
    plan.legs.forEach(function (l) { path = path.concat(l.geometry || []); });
    if (path.length < 2) { stop("This route has no path to follow."); return; }
    var total = 0, seg = [];
    for (var k = 1; k < path.length; k++) {
      var d = haversine(path[k - 1], path[k]);
      seg.push(d);
      total += d;
    }
    var tick = 100, step = total / (SIM_SECONDS * 1000 / tick), idx = 0, into = 0;
    // The pretend clock runs as much faster as the ride is compressed, so the
    // speed worked out from it matches the real trip.
    var scale = Math.max(1, (plan.summary.duration_s || SIM_SECONDS) / SIM_SECONDS);
    var clock = Date.now();
    simTimer = setInterval(function () {
      // A 10-second ride covers hundreds of metres a tick, which could jump
      // straight past the alarm distance. Near each stop it slows down, so
      // the wake-up alarm always rings before "this is your stop".
      var move = step, t = targets[nextTarget()];
      if (t && last) {
        var wake = wakeDistance(), cap = Math.max(50, (wake - ARRIVE_M) / 3);
        if (distanceLeft(last.at, t) < wake + 2 * cap) move = Math.min(move, cap);
      }
      var moved = move;
      while (move > 0 && idx < seg.length) {
        var left = seg[idx] - into;
        if (move < left) { into += move; move = 0; } else { move -= left; idx += 1; into = 0; }
      }
      clock += tick * scale * (moved / step);   // the pretend speed stays the trip's
      if (idx >= seg.length) {
        onFix(path[path.length - 1][0], path[path.length - 1][1], 10, clock);
        clearInterval(simTimer);
        simTimer = null;
        return;
      }
      var a = path[idx], b = path[idx + 1], f = seg[idx] ? into / seg[idx] : 0;
      onFix(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, 10, clock);
    }, tick);
  }

  function stop(message) {
    running = false;
    document.dispatchEvent(new CustomEvent("vazhi:tripend"));
    simulating = false;
    if (watchId != null) navigator.geolocation.clearWatch(watchId);
    watchId = null;
    clearInterval(simTimer);
    simTimer = null;
    stopRinging();
    if (wakeLock) { wakeLock.release(); wakeLock = null; }
    if (meLayer) meLayer.clearLayers();
    el("trip-start").hidden = false;
    el("trip-sim").hidden = false;
    el("trip-stop").hidden = true;
    el("trip-status").hidden = !message;
    el("trip-status").textContent = message || "";
  }

  function init() {
    el("trip-start").addEventListener("click", start);
    el("trip-sim").addEventListener("click", simulate);
    el("trip-stop").addEventListener("click", function () { stop("Trip alerts are off."); });
    el("alarm-ok").addEventListener("click", silence);
    loadSetting();
    buildRing();
    try {                                   // remember "Silent" on this device too
      el("trip-silent").checked = window.localStorage.getItem("vazhi.silent") === "1";
    } catch (e) { /* default: with sound */ }
    el("trip-silent").addEventListener("change", function () {
      try { window.localStorage.setItem("vazhi.silent", el("trip-silent").checked ? "1" : "0"); } catch (e) { /* not kept */ }
    });
    // Phones drop the screen lock when the page is hidden; take it back.
    document.addEventListener("visibilitychange", function () {
      if (running && document.visibilityState === "visible") keepAwake();
    });
  }

  return { init: init, attach: attach, stop: stop, isRunning: function () { return running; },
           // For other reminders (time to leave for the bus): prime() inside
           // the tap that sets the reminder, ring() when it is due.
           prime: prepare,
           ring: function (title, body) { alarm(title, body, true); } };
})();
