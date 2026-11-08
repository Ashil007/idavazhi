/* Phone layout: the control panel becomes a bottom sheet over a full-screen
 * map, like a phone map app. Three resting heights - peek (just the top),
 * "half" (about 40% of the screen, so most of the map stays in view) and
 * full - chosen by dragging the handle or tapping it. On a wide screen the
 * panel stays a side panel and none of this applies.
 *
 * The sheet is always full height and slid down; the part below the screen
 * edge is given back as padding at the bottom (--sheet-hidden), so whatever
 * the height, everything in it can be scrolled into view.
 */
"use strict";

var SHEET = (function () {
  var phone = window.matchMedia("(max-width: 860px)");
  var PEEK_PX = 168;            // the handle and the two search boxes
  var HALF = 0.4;               // share of the screen at the middle height
  var panel = null, state = "half";

  function shown(s) {
    var full = panel.offsetHeight;
    if (s === "peek") return Math.min(PEEK_PX, full);
    if (s === "half") return Math.min(Math.round(window.innerHeight * HALF), full);
    return full;
  }

  function place(offset) {
    offset = Math.max(0, offset);
    panel.style.transform = "translateY(" + offset + "px)";
    panel.style.setProperty("--sheet-hidden", offset + "px");
  }

  function apply() {
    if (!panel) return;
    if (!phone.matches) {
      panel.style.transform = "";
      panel.style.removeProperty("--sheet-hidden");
      return;
    }
    place(panel.offsetHeight - shown(state));
  }

  function set(s) {
    state = s;
    apply();
  }

  function nearest(visible) {
    var best = "half", gap = Infinity;
    ["peek", "half", "full"].forEach(function (s) {
      var d = Math.abs(shown(s) - visible);
      if (d < gap) { gap = d; best = s; }
    });
    return best;
  }

  function init() {
    panel = document.querySelector(".panel");
    var handle = document.getElementById("sheet-handle");
    if (!panel || !handle) return;

    var startY = 0, startOffset = 0, dragging = false, moved = 0;
    handle.addEventListener("pointerdown", function (e) {
      if (!phone.matches) return;
      dragging = true;
      moved = 0;
      startY = e.clientY;
      startOffset = panel.offsetHeight - shown(state);
      panel.classList.add("is-dragging");
      handle.setPointerCapture(e.pointerId);
    });
    handle.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      var dy = e.clientY - startY;
      moved = Math.max(moved, Math.abs(dy));
      place(Math.min(startOffset + dy, panel.offsetHeight - shown("peek")));
    });
    function end(e) {
      if (!dragging) return;
      dragging = false;
      panel.classList.remove("is-dragging");
      if (moved < 6) {
        // A tap: step up, and from the top back down to peek.
        set(state === "peek" ? "half" : state === "half" ? "full" : "peek");
        return;
      }
      var offset = startOffset + (e.clientY - startY);
      set(nearest(panel.offsetHeight - offset));
    }
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);

    window.addEventListener("resize", apply);
    if (phone.addEventListener) phone.addEventListener("change", apply);
    apply();
  }

  // How much of the screen the sheet covers from the bottom (0 on a wide
  // screen), so the map can fit routes into the part still visible.
  function covered() {
    return panel && phone.matches ? shown(state) : 0;
  }

  // Leaflet fitBounds options that keep `pad` px clear and stay above the sheet.
  function fitOptions(pad) {
    return { paddingTopLeft: [pad, pad], paddingBottomRight: [pad, pad + covered()] };
  }

  return {
    init: init,
    covered: covered,
    fitOptions: fitOptions,
    set: function (s) { if (panel && phone.matches) set(s); else state = s; },
    isPhone: function () { return phone.matches; }
  };
})();
