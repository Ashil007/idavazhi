/* Outbox: riders' changes made without internet wait on the phone.
 *
 * Renaming a stop, adding a missing one, answering "what is this stop
 * called?", adding a bus time and voting on one all post to the server. On a
 * bus the signal comes and goes, so when a post can't get through it is kept
 * here (localStorage) and sent by itself once the phone is back online: when
 * the browser says it is online again, when Vazhi is opened or brought back to
 * the front, and every minute while anything is waiting.
 *
 *   OUTBOX.post(url, body)  ->  Promise of the server's answer, or
 *                               { queued: true } when it was kept for later.
 *                               Rejects (with the server's message) only when
 *                               the server read it and said no.
 *
 * Sending twice is harmless: a rider's vote or answer replaces their earlier
 * one, and the same bus time told twice is shown once.
 */
var OUTBOX = (function () {
  "use strict";

  var KEY = "vazhi.outbox";
  var MAX = 50;                          // changes kept at most
  var MAX_AGE_MS = 7 * 24 * 3600 * 1000; // older than a week: dropped unsent
  var EVERY_MS = 60 * 1000;
  var flushing = false, timer = null;

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY) || "[]") || []; } catch (e) { return []; }
  }
  function save(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list)); return true; } catch (e) { return false; }
  }

  function send(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body) });
  }

  // The server or the link to it is down (not "no" from Vazhi itself): try later.
  function later(status) { return status === 502 || status === 503 || status === 504; }

  function keep(url, body) {
    var list = load();
    if (list.length >= MAX) {
      return Promise.reject(new Error("No internet, and " + MAX + " changes are already " +
                                      "waiting on this phone. Try again when you're online."));
    }
    list.push({ url: url, body: body, at: Date.now() });
    if (!save(list)) {
      return Promise.reject(new Error("No internet right now - try again when you're online."));
    }
    schedule();
    return Promise.resolve({ queued: true });
  }

  function post(url, body) {
    if (navigator.onLine === false) return keep(url, body);
    return send(url, body).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.ok) return j;
        if (later(r.status)) return keep(url, body);
        throw new Error(j.error || "Could not save that - try again.");
      });
    }, function () { return keep(url, body); });   // no connection at all
  }

  // Send what is waiting, oldest first; stop at the first one that can't get through.
  function flush() {
    if (flushing || navigator.onLine === false) return Promise.resolve();
    var list = load();
    if (!list.length) return Promise.resolve();
    flushing = true;
    var sent = 0;
    function step() {
      var list = load();
      if (!list.length) return Promise.resolve();
      var item = list[0];
      function drop() { var l = load(); l.shift(); save(l); }
      if (Date.now() - item.at > MAX_AGE_MS) { drop(); return step(); }
      return send(item.url, item.body).then(function (r) {
        if (r.ok) { drop(); sent++; return step(); }
        if (later(r.status) || r.status === 429) return;   // keep it; try again later
        drop();                                            // refused (bad or stale): don't retry
        return step();
      }, function () { /* still offline */ });
    }
    return step().then(function () {
      flushing = false;
      if (sent) {
        try {
          window.dispatchEvent(new CustomEvent("vazhi:outbox", { detail: { sent: sent, left: load().length } }));
        } catch (e) { /* old browser: nothing to tell */ }
      }
      schedule();
    }, function () { flushing = false; schedule(); });
  }

  function schedule() {
    if (timer || !load().length) return;
    timer = setTimeout(function () { timer = null; flush(); }, EVERY_MS);
  }

  window.addEventListener("online", function () { flush(); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") flush();
  });
  setTimeout(flush, 2000);

  return { post: post, flush: flush, waiting: function () { return load().length; } };
})();
