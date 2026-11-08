/* Trip alerts: "wake me before my stop".
 *
 * After a journey is planned the rider can start trip alerts. The page then
 * follows their position (or, in demo mode, a pretend position moving along
 * the route) and, for every place they must get off - a change of train or
 * the final stop:
 *   - well before it (WAKE_M, or WAKE_S at the current speed): a loud alarm
 *     that repeats until "I'm awake" is tapped, with vibration and a
 *     notification - the passenger may be asleep;
 *   - on arrival (ARRIVE_M): "this is your stop", notification and vibration.
 *
 * A web page can only do this while it is open; the screen is kept on while
 * tracking, because phones pause hidden pages.
 */
"use strict";

var ALERTS = (function () {
  var WAKE_M = 2000;        // "auto": wake the rider this far before the stop...
  var WAKE_S = 240;         // ...or this many seconds before, at current speed
  var SETTING = "vazhi.alarmDistance";   // the rider's own choice, kept on this device
  var ARRIVE_M = 150;       // "this is your stop"
  var SIM_SECONDS = 70;     // demo: the whole trip in about this long

  var plan = null, targets = [], fired = {};
  var watchId = null, simTimer = null, running = false, simulating = false;
  var last = null, speed = null;                  // previous fix and m/s
  var meLayer = null, audio = null, beepTimer = null, wakeLock = null, swReg = null;

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
      return {
        name: (leg.to && leg.to.name) || "your stop",
        at: [leg.to.lat, leg.to.lon],
        final: !next,
        nextLabel: next ? next.label : null,
        vehicle: leg.label
      };
    });
  }

  // Called whenever a plan (or another route option) is shown.
  function attach(p) {
    plan = p;
    var box = el("trip");
    if (!box) return;
    var t = targetsOf(p);
    if (!t.length) { box.hidden = true; return; }
    box.hidden = false;
    el("trip-start").textContent = "⏰ Wake me before " + t[t.length - 1].name;
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

  function vibrate(pattern) {
    if (navigator.vibrate) navigator.vibrate(pattern);
  }

  function notify(title, body) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    var opts = { body: body, tag: "vazhi-trip", renotify: true, requireInteraction: true,
                 vibrate: [500, 200, 500, 200, 900] };
    // Android shows notifications only through a service worker.
    if (swReg && swReg.showNotification) swReg.showNotification(title, opts);
    else { try { new Notification(title, opts); } catch (e) { /* not allowed here */ } }
  }

  function silent() { return !!(el("trip-silent") && el("trip-silent").checked); }

  // The full-screen alarm: loud ones beep and buzz until "I'm awake"
  // (only buzz, with "Silent" ticked - for a quiet bus or a sleeping baby).
  function alarm(title, body, loud) {
    el("alarm-title").textContent = title;
    el("alarm-body").textContent = body;
    el("alarm").hidden = false;
    el("alarm").classList.toggle("is-loud", !!loud);
    notify(title, body);
    vibrate(loud ? [600, 250, 600, 250, 1200] : [300, 150, 300]);
    clearInterval(beepTimer);
    if (!silent()) beep();
    if (loud) {
      beepTimer = setInterval(function () { if (!silent()) beep(); vibrate([600, 250, 600]); }, 1600);
    }
  }

  function silence() {
    clearInterval(beepTimer);
    beepTimer = null;
    if (navigator.vibrate) navigator.vibrate(0);
    el("alarm").hidden = true;
    if (targets.length && fired[(targets.length - 1) + ":arrive"]) stop("You have arrived. Trip alerts are off.");
  }

  /* ----------------------------------------------------- tracking */

  // "auto": 2 km, or 4 minutes ahead at the current speed if that is
  // farther. Otherwise exactly the distance the rider picked.
  function wakeDistance() {
    var chosen = el("trip-distance") ? el("trip-distance").value : "auto";
    if (chosen !== "auto" && Number(chosen) > 0) return Number(chosen);
    return Math.max(WAKE_M, speed ? speed * WAKE_S : 0);
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

  function nextTarget() {
    for (var i = 0; i < targets.length; i++) {
      if (!fired[i + ":arrive"]) return i;
    }
    return -1;
  }

  function onFix(lat, lon, accuracy, when) {
    var here = [lat, lon];
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
    var dist = haversine(last.at, t.at);
    var secs = speed && speed > 1 ? dist / speed : null;
    var wakeAt = wakeDistance();

    if (dist <= ARRIVE_M && !fired[i + ":arrive"]) {
      fired[i + ":arrive"] = fired[i + ":wake"] = true;
      if (t.final) alarm("This is your stop", "Get off at " + t.name + ".", false);
      else alarm("Change here", "Get off at " + t.name + " and change to " + t.nextLabel + ".", false);
    } else if (dist <= wakeAt && !fired[i + ":wake"]) {
      fired[i + ":wake"] = true;
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
    var tick = 400, step = total / (SIM_SECONDS * 1000 / tick), idx = 0, into = 0;
    // The pretend clock runs as much faster as the ride is compressed, so the
    // speed worked out from it - and "4 minutes before" - match the real trip.
    var scale = Math.max(1, (plan.summary.duration_s || SIM_SECONDS) / SIM_SECONDS);
    var clock = Date.now();
    simTimer = setInterval(function () {
      var move = step;
      while (move > 0 && idx < seg.length) {
        var left = seg[idx] - into;
        if (move < left) { into += move; move = 0; } else { move -= left; idx += 1; into = 0; }
      }
      clock += tick * scale;
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
    simulating = false;
    if (watchId != null) navigator.geolocation.clearWatch(watchId);
    watchId = null;
    clearInterval(simTimer);
    simTimer = null;
    clearInterval(beepTimer);
    beepTimer = null;
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
