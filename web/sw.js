/* Vazhi Transit service worker - only for trip-alert notifications.
 * Android shows web notifications only through a service worker; tapping
 * one brings the open trip page back to the front. Nothing is cached. */
"use strict";

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (event) { event.waitUntil(self.clients.claim()); });

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true })
    .then(function (pages) {
      if (pages.length) return pages[0].focus();
      return self.clients.openWindow("/");
    }));
});
