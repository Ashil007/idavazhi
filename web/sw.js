/* Vazhi service worker.
 *
 * - Makes Vazhi installable, and lets it open without internet: the app's own
 *   files (and the map libraries) are kept in a cache. They are always fetched
 *   fresh when online, so an update shows up on the next open; the cache is
 *   only the fallback.
 * - Trip plans, place search, bus times and map tiles always go to the
 *   server, never the cache: stale answers there would mislead.
 * - Trip-alert notifications: Android shows web notifications only through a
 *   service worker; tapping one brings the trip page back to the front. */
"use strict";

var CACHE = "vazhi-shell-v38";
var SHELL = [
  "/", "/app", "home.css", "home.js", "style.css", "manifest.webmanifest",
  "basemap.js", "sheet.js", "drawer.js", "alerts.js", "outbox.js", "times.js", "stops.js", "install.js", "permit.js", "notice.js", "about.js", "app.js",
  "icons/icon-192.png", "icons/icon-512.png", "icons/favicon-32.png",
  // The sea and the basic letters of the map; map tiles stay online-only.
  "map/sea.geojson", "map/region.geojson", "fonts/Noto%20Sans%20Regular/0-255.pbf", "fonts/Noto%20Sans%20Medium/0-255.pbf",
  // The map libraries, served by Vazhi itself (web/vendor).
  "vendor/leaflet/leaflet.css", "vendor/leaflet/leaflet.js",
  "vendor/maplibre/maplibre-gl.css", "vendor/maplibre/maplibre-gl.js",
  "vendor/maplibre/leaflet-maplibre-gl.js", "vendor/qrcode/qrcode.js"
];

self.addEventListener("install", function (event) {
  // One file failing (a library site down) must not stop the install.
  event.waitUntil(caches.open(CACHE).then(function (cache) {
    return Promise.all(SHELL.map(function (url) {
      return cache.add(url).catch(function () { /* cached on first use instead */ });
    }));
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; })
                           .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

function isShell(url) {
  if (url.origin === self.location.origin) {
    return !/^\/(api|tiles)\//.test(url.pathname);
  }
  return false;
}

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (!isShell(url)) return;              // plans, search, tiles: straight to the network
  event.respondWith(fetch(req).then(function (res) {
    if (res.ok) {
      var copy = res.clone();
      caches.open(CACHE).then(function (cache) {
        // A page is kept under its path alone: a shared-trip link
        // (/app?from=...) is the same page as "/app".
        cache.put(req.mode === "navigate" ? url.pathname : req, copy);
      });
    }
    return res;
  }).catch(function () {
    return caches.match(req.mode === "navigate" ? url.pathname : req, { ignoreSearch: req.mode === "navigate" })
      .then(function (hit) { return hit || Response.error(); });
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true })
    .then(function (pages) {
      if (pages.length) return pages[0].focus();
      return self.clients.openWindow("/app");
    }));
});
