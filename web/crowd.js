/* How crowded is it? Seats, standing or packed.
 *
 * On a ride: once the stop alarm is on and the bus has been moving for a
 * minute, a small card asks "How crowded is this bus right now?" with three
 * buttons and a cross. One tap sends the answer - with the boarding stop,
 * the next few stops and the direction, nothing about the rider - and the
 * card goes. It asks once per ride; the cross stops it for the whole trip.
 *
 * Planning: each route option gets a small pill. What riders said in the
 * last 45 minutes ("Packed · 6 min ago") when there is something; otherwise
 * what is usual at this hour on a weekday, marked "Usually", from the rush
 * hours: 08:00-10:30 and 16:30-19:30 are busy; 11:30-15:30 and after 20:30
 * usually have seats. */
"use strict";

var CROWD = (function () {
  var LEVELS = {
    seats:    { t: "Seats",    say: "Seats free" },
    standing: { t: "Standing", say: "Standing room" },
    packed:   { t: "Packed",   say: "Packed" }
  };
  var NOUN = { bus: "bus", train: "train", metro: "metro" };
  var MOVING_MS = 60000;      // ask after this long moving on a ride
  var MOVING_MPS = 2.5;       // 9 km/h: faster than walking
  var NEAR_M = 200;           // on the ride's line, not walking to the stop
  var $ = function (id) { return document.getElementById(id); };
  var cache = {};             // query -> {at, data}
  var ride = null;            // this trip's state while the alarm is on

  /* -------------------------------------------------------------- helpers */

  function dist(a, b) {
    var R = 6371000, r = Math.PI / 180;
    var dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  function firstRide(plan) {
    return (plan && plan.legs || []).filter(function (l) {
      return l.type === "transit" && l.from && l.to && l.from.lat != null && l.to.lat != null;
    })[0] || null;
  }

  // What the hour usually brings, on a weekday; null when there is no rule.
  function usual(now) {
    now = now || new Date();
    var day = now.getDay();
    if (day === 0 || day === 6) return null;
    var m = now.getHours() * 60 + now.getMinutes();
    if ((m >= 480 && m < 630) || (m >= 990 && m < 1170)) return "packed";
    if ((m >= 690 && m < 930) || m >= 1230 || m < 300) return "seats";
    return null;
  }

  function ask(leg, mode) {
    var q = new URLSearchParams({ mode: mode, lat: leg.from.lat, lon: leg.from.lon, tlat: leg.to.lat, tlon: leg.to.lon });
    var k = q.toString(), hit = cache[k];
    if (hit && Date.now() - hit.at < 60000) return Promise.resolve(hit.data);
    if (navigator.onLine === false) return Promise.resolve({ level: null });
    return fetch("/api/crowd?" + k).then(function (r) { return r.ok ? r.json() : { level: null }; })
      .catch(function () { return { level: null }; })
      .then(function (d) { cache[k] = { at: Date.now(), data: d }; return d; });
  }

  function pill(d) {
    var level = d && d.level, from = "riders";
    if (!level) { level = usual(); from = "usual"; }
    if (!level) return null;
    var span = document.createElement("span");
    span.className = "crowd crowd--" + level + (from === "usual" ? " crowd--usual" : "");
    if (from === "riders") {
      var ago = d.minutes_ago < 1 ? "just now" : d.minutes_ago + " min ago";
      span.textContent = LEVELS[level].t + " · " + ago;
      span.title = d.reports + (d.reports === 1 ? " rider" : " riders") + " said so in the last 45 minutes";
    } else {
      span.textContent = level === "packed" ? "Usually busy now" : "Usually seats now";
      span.title = "Nobody has said lately; this is what weekday rush hours are usually like";
    }
    return span;
  }

  /* ------------------------------------------------- pills on the options */

  var drawing = 0;
  function drawPills() {
    var opts = state.options || [], token = ++drawing;
    var line = $("crowd-line");
    if (!opts.length) { if (line) line.hidden = true; return; }
    opts.forEach(function (plan, j) {
      var leg = firstRide(plan);
      if (!leg) return;
      ask(leg, plan.mode || state.mode).then(function (d) {
        if (token !== drawing) return;
        var p = pill(d);
        // On each option card when there are several ...
        var card = document.querySelector('#options [data-option="' + j + '"]');
        if (card && !card.querySelector(".crowd") && p) card.appendChild(p.cloneNode(true));
        // ... and under the summary for the one on screen.
        if (j === (state.selected || 0) && line) {
          line.innerHTML = "";
          if (p) {
            var noun = NOUN[plan.mode] || "ride";
            var label = document.createElement("span");
            label.className = "crowd-line__label";
            label.textContent = "How full the " + noun + " is from " + (leg.from.name || "your stop");
            line.appendChild(label);
            line.appendChild(p);
          }
          line.hidden = !p;
        }
      });
    });
  }

  /* ------------------------------------------------------ the ride card */

  function rideNow(at, plan) {
    // The ride the rider is on: the transit leg whose line passes nearest.
    var best = null, bestD = Infinity;
    (plan.legs || []).forEach(function (leg) {
      if (leg.type !== "transit" || !leg.geometry || !leg.from || !leg.to) return;
      var g = leg.geometry, step = Math.max(1, Math.floor(g.length / 300));
      for (var i = 0; i < g.length; i += step) {
        var d = dist(at, g[i]);
        if (d < bestD) { bestD = d; best = leg; }
      }
    });
    return bestD <= NEAR_M ? best : null;
  }

  function showCard(leg, mode) {
    var card = $("crowd-card");
    if (!card) return;
    ride.asking = leg;
    $("crowd-card-q").textContent = "How crowded is this " + (NOUN[mode] || "ride") + " right now?";
    $("crowd-card-buttons").hidden = false;
    $("crowd-card-thanks").hidden = true;
    card.hidden = false;
  }

  function hideCard() { var c = $("crowd-card"); if (c) c.hidden = true; if (ride) ride.asking = null; }

  function answer(level) {
    if (!ride || !ride.asking) return;
    var leg = ride.asking, plan = ride.plan;
    ride.asked.push(leg);
    $("crowd-card-buttons").hidden = true;
    $("crowd-card-thanks").hidden = false;
    // The stops still ahead on this ride, nearest first.
    var here = ride.at, ahead = (leg.stops_passed || []).filter(function (s) { return s.lat != null; });
    if (here && ahead.length) {
      var toEnd = dist(here, [leg.to.lat, leg.to.lon]);
      ahead = ahead.filter(function (s) { return dist([s.lat, s.lon], [leg.to.lat, leg.to.lon]) < toEnd; }).slice(0, 5);
    }
    if (ride.simulating) {
      $("crowd-card-thanks").textContent = "Thanks! (Demo ride: nothing was sent.)";
    } else {
      $("crowd-card-thanks").textContent = "Thanks! Riders waiting further along will see it.";
      fetch("/api/crowd", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: plan.mode, level: level, lat: leg.from.lat, lon: leg.from.lon,
                               tlat: leg.to.lat, tlon: leg.to.lon,
                               ahead: ahead.map(function (s) { return { lat: s.lat, lon: s.lon }; }) })
      }).catch(function () { /* no signal: it would be old by the time there is */ });
      cache = {};
    }
    window.setTimeout(hideCard, 2600);
  }

  function onProgress(e) {
    var d = e.detail || {};
    if (!ride || ride.off || !d.at || !d.plan) return;
    ride.plan = d.plan;
    ride.at = d.at;
    var now = Date.now();
    var leg = rideNow(d.at, d.plan);
    var moving = d.speed != null && d.speed > MOVING_MPS && leg && ride.asked.indexOf(leg) < 0;
    if (!moving) { if (!ride.asking) ride.since = null; return; }
    if (ride.since == null || ride.leg !== leg) { ride.since = now; ride.leg = leg; }
    // A demo ride squeezes the trip into seconds; ask after a few of them.
    var wait = ride.simulating ? 2500 : MOVING_MS;
    // Never over a ringing alarm: the question waits until it is answered.
    var alarm = $("alarm");
    if (alarm && !alarm.hidden) return;
    // Nor on the sleep screen: it waits until the rider wakes the screen.
    if (window.SLEEP && SLEEP.isOn()) return;
    if (!ride.asking && now - ride.since >= wait) showCard(leg, d.plan.mode);
  }

  /* ---------------------------------------------------------------- init */

  function init() {
    var summary = $("summary"), options = $("options");
    if (summary && "MutationObserver" in window) {
      new MutationObserver(drawPills).observe(summary, { childList: true });
      if (options) new MutationObserver(function () {
        // The option cards are redrawn when another one is picked.
        if (options.querySelector("[data-option]") && !options.querySelector(".crowd")) drawPills();
      }).observe(options, { childList: true });
    }
    var card = $("crowd-card");
    if (card) {
      card.addEventListener("click", function (e) {
        var b = e.target.closest("[data-crowd]");
        if (b) answer(b.getAttribute("data-crowd"));
      });
      $("crowd-card-close").addEventListener("click", function () {
        if (ride) ride.off = true;           // not again this trip
        hideCard();
      });
    }
    document.addEventListener("vazhi:tripstart", function (e) {
      ride = { simulating: !!(e.detail && e.detail.simulating), asked: [], since: null, off: false, asking: null };
    });
    document.addEventListener("vazhi:tripend", function () { ride = null; hideCard(); });
    document.addEventListener("vazhi:progress", onProgress);
    // An alarm takes the screen: put the question away (asked again later).
    var alarm = $("alarm");
    if (alarm && "MutationObserver" in window) {
      new MutationObserver(function () {
        if (!alarm.hidden && ride && ride.asking) { hideCard(); ride.since = null; }
      }).observe(alarm, { attributes: true, attributeFilter: ["hidden"] });
    }
  }

  return { init: init, usual: usual };
})();

CROWD.init();
