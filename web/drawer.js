/* Phones: the planned trip opens in a drawer that slides in from the left,
 * while planning (where from, where to, Bus/Train/Metro) stays in the bottom
 * sheet. "Map" (or a swipe to the left) slides the drawer away to show the
 * route; the "Trip" tab on the left edge brings it back.
 *
 * The trip's own block (#result-block) is moved into the drawer on a phone
 * and back into the side panel on a wide screen, so there is one copy of it
 * and every button in it keeps working.
 *
 * States: "closed" (no trip), "open", "away" (slid off, tab showing).
 */
var DRAWER = (function () {
  "use strict";

  var phone = window.matchMedia("(max-width: 860px)");
  var drawer, body, tab, result, home, state = "closed";

  function $(id) { return document.getElementById(id); }

  function place() {
    if (phone.matches) {
      if (result.parentNode !== body) body.appendChild(result);
    } else if (result.parentNode !== home.parentNode) {
      home.parentNode.insertBefore(result, home);
    }
    render();
  }

  function render() {
    var on = phone.matches;
    drawer.hidden = !on || state === "closed";
    drawer.classList.toggle("is-away", state !== "open");
    drawer.setAttribute("aria-hidden", String(state !== "open"));
    tab.hidden = !on || state !== "away";
    document.documentElement.classList.toggle("drawer-open", on && state === "open");
  }

  function open() {
    var wasClosed = state === "closed" || drawer.hidden;
    state = "open";
    if (!phone.matches) { render(); return; }
    if (wasClosed) {
      // Start off-screen, then slide in.
      drawer.hidden = false;
      drawer.classList.add("is-away");
      body.scrollTop = 0;
      void drawer.offsetWidth;            // lay it out off-screen first...
      render();                           // ...so this slides it in
    } else {
      render();
    }
  }

  function away() { if (state !== "closed") { state = "away"; render(); } }
  function close() { state = "closed"; render(); }

  function init() {
    drawer = $("drawer");
    body = $("drawer-body");
    tab = $("drawer-tab");
    result = $("result-block");
    home = result.nextElementSibling;          // where it sits in the side panel

    $("drawer-hide").addEventListener("click", away);
    tab.addEventListener("click", open);

    // Swipe left on the drawer to slide it away.
    var x0 = null, y0 = 0;
    drawer.addEventListener("touchstart", function (e) {
      x0 = e.touches[0].clientX; y0 = e.touches[0].clientY;
    }, { passive: true });
    drawer.addEventListener("touchend", function (e) {
      if (x0 == null) return;
      var dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
      x0 = null;
      if (dx < -70 && Math.abs(dy) < Math.abs(dx) * 0.6) away();
    }, { passive: true });

    if (phone.addEventListener) phone.addEventListener("change", place);
    place();
  }

  return { init: init, open: open, away: away, close: close,
           isOpen: function () { return state === "open" && phone.matches; } };
})();
