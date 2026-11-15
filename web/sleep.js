/* Sleep screen: a black screen for a long ride.
 *
 * While the stop alarm is on, "Sleep screen" covers the app with pure black
 * (#000 - on an AMOLED phone those pixels are off and use no power) and only
 * the essentials in dim grey: the next stop, how far it is, the speed and
 * whether the GPS is getting fixes. Everything keeps running underneath:
 * the screen stays on (alerts.js holds the wake lock), positions keep coming
 * and the alarm still rings - it opens above this screen and wakes it.
 * A tap anywhere brings the app back.
 *
 * The text drifts a little every minute so no pixel stays lit for hours. */
"use strict";

var SLEEP = (function () {
  var box = null, btn = null, on = false, lastFix = 0, accuracy = null, drift = null, tick = null;
  var $ = function (id) { return document.getElementById(id); };

  function fmtDist(m) {
    if (m == null) return "—";
    return m < 1000 ? Math.round(m / 10) * 10 + " m" : (m / 1000).toFixed(m < 10000 ? 1 : 0) + " km";
  }

  function gpsText() {
    if (!lastFix) return { t: "Waiting for GPS", ok: false };
    var age = (Date.now() - lastFix) / 1000;
    if (age > 45) return { t: "GPS lost " + Math.round(age / 60 || 1) + " min ago", ok: false };
    if (accuracy != null && accuracy > 60) return { t: "GPS weak · ±" + Math.round(accuracy) + " m", ok: false };
    return { t: "GPS good" + (accuracy != null ? " · ±" + Math.round(accuracy) + " m" : ""), ok: true };
  }

  function showGps() {
    var g = gpsText(), el = $("sleep-gps");
    if (!el) return;
    el.textContent = g.t;
    el.classList.toggle("is-bad", !g.ok);
  }

  function enter() {
    if (on || !box) return;
    on = true;
    box.hidden = false;
    document.documentElement.classList.add("is-sleeping");
    moveText();
    drift = window.setInterval(moveText, 60000);
    tick = window.setInterval(showGps, 5000);
    showGps();
    // Full screen hides the phone's own bars too, where the browser allows it.
    if (box.requestFullscreen && !document.fullscreenElement) {
      box.requestFullscreen({ navigationUI: "hide" }).catch(function () { /* the black screen is enough */ });
    }
  }

  function exit() {
    if (!on) return;
    on = false;
    box.hidden = true;
    document.documentElement.classList.remove("is-sleeping");
    window.clearInterval(drift);
    window.clearInterval(tick);
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () {});
  }

  // A few pixels this way or that, so the same pixels aren't lit all night.
  function moveText() {
    var inner = $("sleep-inner");
    if (!inner) return;
    var x = Math.round((Math.random() - 0.5) * 40), y = Math.round((Math.random() - 0.5) * 80);
    inner.style.transform = "translate(" + x + "px," + y + "px)";
  }

  function init() {
    box = $("sleep");
    btn = $("trip-sleep");
    if (!box || !btn) return;
    btn.addEventListener("click", enter);
    box.addEventListener("click", exit);
    box.addEventListener("keydown", function (e) { if (e.key === "Escape" || e.key === "Enter" || e.key === " ") exit(); });

    document.addEventListener("vazhi:tripstart", function () {
      lastFix = 0; accuracy = null;
      btn.hidden = false;
      $("sleep-stop").textContent = "Your stop";
      $("sleep-dist").textContent = "—";
      $("sleep-speed").textContent = "";
    });
    document.addEventListener("vazhi:tripend", function () { btn.hidden = true; exit(); });
    document.addEventListener("vazhi:fix", function (e) {
      lastFix = Date.now();
      accuracy = e.detail && e.detail.accuracy;
      if (on) showGps();
    });
    document.addEventListener("vazhi:progress", function (e) {
      var d = e.detail || {};
      $("sleep-label").textContent = d.final ? "Your stop" : "Change at";
      $("sleep-stop").textContent = d.name || "Your stop";
      $("sleep-dist").textContent = fmtDist(d.dist);
      var kmh = d.speed != null ? Math.round(d.speed * 3.6) : null;
      var mins = d.secs ? Math.max(1, Math.round(d.secs / 60)) : null;
      $("sleep-speed").textContent = [mins ? "about " + mins + " min" : "", kmh != null ? kmh + " km/h" : ""]
        .filter(Boolean).join(" · ");
    });
    // The alarm opens above this screen; once it rings, the app comes back
    // so "I'm awake" and the trip are right there.
    var alarm = $("alarm");
    if (alarm && "MutationObserver" in window) {
      new MutationObserver(function () { if (!alarm.hidden) exit(); })
        .observe(alarm, { attributes: true, attributeFilter: ["hidden"] });
    }
  }

  return { init: init, enter: enter, exit: exit, isOn: function () { return on; } };
})();

document.addEventListener("DOMContentLoaded", SLEEP.init);
