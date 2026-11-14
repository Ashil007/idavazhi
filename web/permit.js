/* Location and alarms are required: Vazhi's point is the alarm before your
 * stop, and that needs both your location (to know the stop is near) and
 * notifications (to wake you when the screen is off or another app is open).
 *
 * Until both are allowed, a screen covers the app and asks for them. A
 * browser asks only once: after "Block" it never shows its question again, so
 * the screen then explains how to allow them in the phone's settings, and
 * checks again when the rider comes back from there.
 *
 * Cases where a permission cannot be given at all also get their own words:
 * a page that is not https, an iPhone where Vazhi is not on the Home Screen
 * (Safari has no alarms in a tab), and in-app browsers without notifications.
 */
var PERMIT = (function () {
  "use strict";

  var ready = null;            // called once, when both are allowed
  var geo = "prompt";          // granted | denied | prompt | unsupported
  var asking = false;
  var watched = false;

  function $(id) { return document.getElementById(id); }

  function isIOS() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent) ||
           (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }
  function isAndroid() { return /android/i.test(navigator.userAgent); }
  function installed() {
    return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
           window.navigator.standalone === true;
  }

  /* ------------------------------------------------------------ states */

  function geoState() {
    if (!window.isSecureContext) return Promise.resolve("insecure");
    if (!("geolocation" in navigator)) return Promise.resolve("unsupported");
    if (!navigator.permissions || !navigator.permissions.query) return Promise.resolve(geo);
    return navigator.permissions.query({ name: "geolocation" }).then(function (st) {
      if (!watched) {                      // allowed in settings while we wait
        watched = true;
        st.onchange = check;
      }
      return st.state;
    }).catch(function () { return geo; });
  }

  function noteState() {
    if (!window.isSecureContext) return "insecure";
    if (!("Notification" in window)) return isIOS() && !installed() ? "install" : "unsupported";
    var p = Notification.permission;
    return p === "default" ? "prompt" : p;          // granted | denied | prompt
  }

  /* ------------------------------------------------------------- words */

  var LABEL = {
    granted: ["Allowed", "is-ok"], prompt: ["Not yet", ""], denied: ["Blocked", "is-bad"],
    unsupported: ["Not possible here", "is-bad"], insecure: ["Not possible here", "is-bad"],
    install: ["Needs the Home Screen", "is-bad"]
  };

  function setRow(id, state) {
    var l = LABEL[state] || LABEL.prompt, s = $(id);
    s.textContent = l[0];
    s.className = "permit__state " + l[1];
  }

  function blockedHow() {
    if (isIOS()) {
      return "<b>Location:</b> open <b>Settings &gt; Privacy &amp; Security &gt; Location Services</b>, " +
             "turn it on, then set <b>Safari Websites</b> to <b>While Using the App</b>.<br>" +
             "<b>Alarms:</b> open <b>Settings &gt; Notifications &gt; Vazhi</b> and turn on " +
             "<b>Allow Notifications</b>.";
    }
    if (isAndroid() && installed()) {
      return "Touch and hold the Vazhi icon on your home screen, tap <b>App info</b> (&#9432;), " +
             "and allow <b>Location</b> and <b>Notifications</b>. Then come back here.";
    }
    if (isAndroid()) {
      return "Tap the <b>tune or lock icon</b> (&#127898; or &#128274;) next to the website address (or the <b>three dots menu &#8942; &rarr; Info &#9432;</b>), tap <b>Permissions</b>, and change <b>Location</b> and <b>Notifications</b> to <b>Allow</b>. Then come back here.";
    }
    return "Click the icon at the left of the address bar, and set <b>Location</b> and " +
           "<b>Notifications</b> to <b>Allow</b>.";
  }

  function render(g, n) {
    setRow("permit-geo-state", g);
    setRow("permit-note-state", n);

    var help = "", button = "", again = false;
    if (g === "insecure" || n === "insecure") {
      help = "This page is not secure (it is not <b>https</b>), so the phone allows neither. " +
             "Open Vazhi from its <b>https://</b> link.";
    } else if (g === "unsupported" || n === "unsupported") {
      help = "This browser cannot " + (g === "unsupported" ? "share your location" : "show alarms") +
             ". If you opened Vazhi from WhatsApp, Instagram or another app, open the link in " +
             (isIOS() ? "<b>Safari</b>" : "<b>Chrome</b>") + " instead.";
    } else if (g === "denied" || n === "denied") {
      help = "Your phone blocked " +
             (g === "denied" && n === "denied" ? "both" : g === "denied" ? "location" : "alarms") +
             ", and it will not ask again. To allow " + (g === "denied" && n === "denied" ? "them" : "it") +
             ":<br>" + blockedHow();
      again = true;
    } else if (n === "install") {
      help = "On iPhone, alarms work only when Vazhi is on your Home Screen: tap <b>Share</b> " +
             "(the square with an arrow), then <b>Add to Home Screen</b>, and open Vazhi from there.";
      if (g === "prompt") button = "Allow location";
    } else {
      button = g === "prompt" && n === "prompt" ? "Allow location and alarms"
             : g === "prompt" ? "Allow location" : "Allow alarms";
    }

    $("permit-help").innerHTML = help;
    $("permit-help").hidden = !help;
    $("permit-allow").textContent = button;
    $("permit-allow").hidden = !button;
    $("permit-again").hidden = !again;
  }

  /* ------------------------------------------------------- check, ask */

  function check() {
    return geoState().then(function (g) {
      var n = noteState();
      if (g === "granted" && n === "granted") {
        open(false);
        if (ready) { var go = ready; ready = null; go(); }
        return;
      }
      render(g, n);
      open(true);
    });
  }

  function open(show) {
    $("permit").hidden = !show;
    document.documentElement.classList.toggle("permit-open", show);
    if (show && !$("permit").contains(document.activeElement)) {
      var b = $("permit-allow").hidden ? $("permit-again") : $("permit-allow");
      if (!b.hidden) b.focus();
    }
  }

  // Inside the tap: alarms first (some browsers allow that question only
  // straight after a tap), then location.
  function ask() {
    if (asking) return;
    asking = true;
    $("permit-allow").disabled = true;
    var notes = noteState() === "prompt"
      ? Promise.resolve(Notification.requestPermission()).catch(function () { return null; })
      : Promise.resolve();
    notes.then(function () {
      return new Promise(function (done) {
        if (!("geolocation" in navigator) || geo === "granted") { done(); return; }
        navigator.geolocation.getCurrentPosition(function () {
          geo = "granted"; done();
        }, function (err) {
          // Allowed but no fix yet (indoors, GPS off) still counts as allowed.
          geo = err && err.code === 1 ? "denied" : "granted"; done();
        }, { enableHighAccuracy: false, timeout: 20000, maximumAge: 600000 });
      });
    }).then(function () {
      asking = false;
      $("permit-allow").disabled = false;
      return check();
    });
  }

  function init(onReady) {
    ready = onReady;
    $("permit-allow").addEventListener("click", ask);
    $("permit-again").addEventListener("click", function () {
      // Some phones only notice a change made in settings after a reload.
      check().then(function () { if (!$("permit").hidden) window.location.reload(); });
    });
    // Back from the phone's settings: look again.
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible" && !$("permit").hidden) check();
    });
    check();
  }

  return { init: init, check: check };
})();
