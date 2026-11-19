/* Installing Idavazhi as an app, and telling the rider when they are offline.
 *
 * Android and desktop Chrome/Edge offer an install prompt the page can show
 * from its own button ("beforeinstallprompt"). iPhones have no such prompt:
 * there the button explains Safari's Share > Add to Home Screen instead.
 * Once installed (or opened from the home screen) the button never shows. */
var INSTALL = (function () {
  "use strict";

  var prompt = null;
  function $(id) { return document.getElementById(id); }

  function installed() {
    return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
           window.navigator.standalone === true;          // iOS home-screen app
  }
  function isIOS() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent) ||
           (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);   // iPadOS
  }
  function dismissedRecently() {
    try { return Date.now() - Number(localStorage.getItem("vazhi.installNo") || 0) < 14 * 864e5; }
    catch (e) { return false; }
  }

  function show() {
    if (installed() || dismissedRecently()) return;
    $("install").hidden = false;
  }
  function hide() { $("install").hidden = true; }

  function offline() { $("offline-note").hidden = navigator.onLine !== false; }

  function init() {
    // The service worker makes the app installable and lets it open offline.
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("sw.js").catch(function () { /* works without */ });
    }

    window.addEventListener("beforeinstallprompt", function (e) {
      e.preventDefault();                // show our own button, not the browser's bar
      prompt = e;
      $("install-btn").textContent = /Mobi|Android/i.test(navigator.userAgent)
        ? "Install Idavazhi on this phone" : "Install Idavazhi as an app";
      show();
    });
    window.addEventListener("appinstalled", function () { prompt = null; hide(); });

    // iPhone and iPad (Safari): no prompt to show, so explain the two taps.
    if (isIOS() && !installed()) {
      $("install-btn").textContent = "Add Idavazhi to your home screen";
      show();
    }

    $("install-btn").addEventListener("click", function () {
      if (prompt) {
        prompt.prompt();
        prompt.userChoice.then(function (choice) {
          prompt = null;
          if (choice.outcome === "accepted") hide();
        });
      } else {
        var how = $("install-how");
        how.innerHTML = "In Safari, tap <b>Share</b> (the square with an arrow) and then " +
                        "<b>Add to Home Screen</b>. Idavazhi then opens like an app, full screen.";
        how.hidden = false;
      }
    });
    $("install-close").addEventListener("click", function () {
      try { localStorage.setItem("vazhi.installNo", String(Date.now())); } catch (e) { /* asked again */ }
      hide();
    });

    window.addEventListener("online", offline);
    window.addEventListener("offline", offline);
    offline();
  }

  return { init: init, installed: installed };
})();
