/* Vibration patterns for the stop alarm.
 *
 * Four buzzes to choose from, for a quiet bus or a phone in a pocket:
 * Gentle pulse, Heartbeat, Urgent rumble (the alarm's own, and the default)
 * and SOS. Tapping one plays it once so the rider can feel it, and picks it.
 * The choice stays on this phone (localStorage). alerts.js asks
 * VIBES.pattern() each time the alarm rings.
 *
 * Phones without vibration (iPhones, computers) don't see the row. */
"use strict";

var VIBES = (function () {
  var KEY = "vazhi.vibe";
  // Milliseconds: buzz, pause, buzz, ... (navigator.vibrate).
  var PATTERNS = {
    gentle:    { name: "Gentle pulse",  p: [250, 650, 250, 650, 250] },
    heartbeat: { name: "Heartbeat",     p: [110, 110, 180, 650, 110, 110, 180, 650] },
    urgent:    { name: "Urgent rumble", p: [700, 300, 700, 300, 1200] },
    sos:       { name: "SOS",           p: [150, 100, 150, 100, 150, 350, 450, 150, 450, 150, 450, 350,
                                            150, 100, 150, 100, 150] }
  };
  var ORDER = ["gentle", "heartbeat", "urgent", "sos"];
  var choice = "urgent";

  function can() { return typeof navigator.vibrate === "function"; }

  function load() {
    try {
      var v = window.localStorage.getItem(KEY);
      if (PATTERNS[v]) choice = v;
    } catch (e) { /* default */ }
  }

  function save() {
    try { window.localStorage.setItem(KEY, choice); } catch (e) { /* this visit only */ }
  }

  function pattern() { return PATTERNS[choice].p.slice(); }

  // A small drawing of each rhythm: one bar per buzz, its width the buzz's
  // length, so the choices can be told apart without feeling them.
  function bars(p) {
    var x = 0, out = [], total = p.reduce(function (a, b) { return a + b; }, 0), w = 64;
    for (var i = 0; i < p.length; i++) {
      var len = p[i] / total * w;
      if (i % 2 === 0) out.push('<rect x="' + x.toFixed(1) + '" y="3" width="' + Math.max(1.5, len).toFixed(1) +
                                '" height="8" rx="2"/>');
      x += len;
    }
    return '<svg class="vibe__bars" viewBox="0 0 64 14" aria-hidden="true">' + out.join("") + "</svg>";
  }

  function render(box) {
    box.innerHTML = ORDER.map(function (id) {
      return '<button type="button" class="vibe__chip" role="radio" data-vibe="' + id + '" aria-checked="' +
             (id === choice) + '">' + bars(PATTERNS[id].p) + "<span>" + PATTERNS[id].name + "</span></button>";
    }).join("");
  }

  function init() {
    var row = document.getElementById("trip-vibe-row"), box = document.getElementById("trip-vibes");
    if (!row || !box) return;
    load();
    if (!can()) { row.hidden = true; return; }
    row.hidden = false;
    render(box);
    box.addEventListener("click", function (e) {
      var b = e.target.closest("[data-vibe]");
      if (!b) return;
      choice = b.getAttribute("data-vibe");
      save();
      Array.prototype.forEach.call(box.querySelectorAll("[data-vibe]"), function (c) {
        c.setAttribute("aria-checked", String(c === b));
      });
      try { navigator.vibrate(0); navigator.vibrate(pattern()); } catch (err) { /* no buzz here */ }
    });
  }

  return { init: init, pattern: pattern, PATTERNS: PATTERNS };
})();

document.addEventListener("DOMContentLoaded", VIBES.init);
