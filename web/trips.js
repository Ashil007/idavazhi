/* Saved trips: one tap for the daily ride.
 *
 * A planned trip can be saved as Home, Work or a name of the rider's own
 * ("Saved trip" button under the plan). Saved trips show as chips under the
 * search boxes; a tap fills in both ends, the mode and how the rider reaches
 * the stop, and plans it.
 *
 * Everything stays on this phone: the list in localStorage, no account and
 * nothing sent anywhere.
 *
 * Offline: saving also keeps a copy of the planned route (its path, stops and
 * times) and the map tiles along it, in the browser's CacheStorage. With no
 * internet, tapping the chip opens that copy, and the map, GPS, distance and
 * stop alarm all work as usual (sw.js answers the map from the copy). */
"use strict";

var TRIPS = (function () {
  var KEY = "vazhi.trips";
  var STORE = "vazhi-offline-trips";        // CacheStorage: route copies and map tiles
  var MAX = 8;
  var KINDS = {
    home: { label: "Home", icon: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v9.5h13V10"/><path d="M10 19.5v-5h4v5"/>' },
    work: { label: "Work", icon: '<rect x="3" y="7.5" width="18" height="12" rx="2"/><path d="M8.5 7.5V5.5a1.5 1.5 0 0 1 1.5-1.5h4a1.5 1.5 0 0 1 1.5 1.5v2M3 13h18"/>' },
    custom: { label: "", icon: '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>' }
  };
  var $ = function (id) { return document.getElementById(id); };
  var editing = false, picked = "home";

  /* ------------------------------------------------------------ storage */

  function load() {
    try {
      var v = JSON.parse(window.localStorage.getItem(KEY));
      return Array.isArray(v) ? v.filter(function (t) { return t && t.from && t.to; }) : [];
    } catch (e) { return []; }
  }

  function save(list) {
    try { window.localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX))); return true; }
    catch (e) { return false; }
  }

  function near(a, b) { return Math.abs(a.lat - b.lat) < 3e-4 && Math.abs(a.lon - b.lon) < 3e-4; }

  // The trip on screen right now, as it would be saved.
  function current() {
    if (!state.origin || !state.destination || !state.mode) return null;
    function end(p, box) {
      var name = ($(box).value || "").trim();
      if (/^Point on the map/.test(name)) name = "";
      return { lat: +p.lat.toFixed(5), lon: +p.lon.toFixed(5), name: name };
    }
    return { from: end(state.origin, "origin-input"), to: end(state.destination, "dest-input"),
             mode: state.mode, access: state.access === "cab" ? "walk" : state.access };
  }

  function find(t) {
    if (!t) return null;
    return load().filter(function (s) { return s.mode === t.mode && near(s.from, t.from) && near(s.to, t.to); })[0] || null;
  }

  function labelOf(t) { return t.kind === "custom" ? t.label : KINDS[t.kind].label; }
  function short(name) {
    name = (name || "").split(",")[0].trim();
    return name.length > 22 ? name.slice(0, 21) + "…" : name;
  }
  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function icon(kind) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
           'stroke-linejoin="round" aria-hidden="true">' + KINDS[kind].icon + "</svg>";
  }

  /* -------------------------------------------------------------- chips */

  function renderChips() {
    var wrap = $("saved-trips"), row = $("saved-trips-row");
    if (!wrap || !row) return;
    var list = load();
    wrap.hidden = !list.length;
    if (!list.length) { editing = false; wrap.classList.remove("is-editing"); }
    row.innerHTML = list.map(function (t) {
      var to = short(t.to.name) || "Saved place";
      return '<button type="button" class="tripchip tripchip--' + t.kind + '" data-trip="' + esc(t.id) + '" ' +
             'aria-label="' + esc((editing ? "Remove " : "Plan ") + labelOf(t) + " trip, to " + to) + '">' +
             icon(t.kind) + '<span class="tripchip__t"><b>' + esc(labelOf(t)) + "</b><span>" + esc(to) + "</span></span>" +
             (t.offline ? '<span class="tripchip__off" title="Works offline">offline</span>' : "") +
             '<span class="tripchip__x" aria-hidden="true">&times;</span></button>';
    }).join("") +
    '<button type="button" class="tripchip tripchip--edit" id="saved-trips-edit">' + (editing ? "Done" : "Edit") + "</button>";
  }

  function onChip(e) {
    if (e.target.closest("#saved-trips-edit")) {
      editing = !editing;
      $("saved-trips").classList.toggle("is-editing", editing);
      renderChips();
      return;
    }
    var b = e.target.closest("[data-trip]");
    if (!b) return;
    var id = b.getAttribute("data-trip");
    var t = load().filter(function (x) { return x.id === id; })[0];
    if (!t) return;
    if (editing) { remove(id); return; }
    go(t);
  }

  // Set a choice without planning yet (choose() in app.js re-plans a shown trip).
  function setChoice(key, id) {
    state[key] = id;
    document.querySelectorAll('.mode[data-key="' + key + '"]').forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-id") === id));
    });
  }

  function go(t) {
    if (ALERTS.isRunning()) {
      if (!window.confirm("Your stop alarm is on. Plan your " + labelOf(t) + " trip instead?")) return;
      ALERTS.stop("Trip alerts are off.");
    }
    setChoice("mode", t.mode);
    setChoice("access", t.access || "walk");
    setPoint("origin", L.latLng(t.from.lat, t.from.lon), t.from.name || null);
    setPoint("destination", L.latLng(t.to.lat, t.to.lon), t.to.name || null);
    if (typeof showDestClear === "function") showDestClear();
    if (navigator.onLine === false) { openOffline(t); return; }
    planJourney();
  }

  /* ------------------------------------------------------------- offline */

  function canStore() { return "caches" in window && window.isSecureContext !== false; }
  function copyUrl(id) { return "/offline/trip/" + encodeURIComponent(id) + ".json"; }

  // No internet: show the copy kept when the trip was saved.
  function openOffline(t) {
    if (!t.offline || !canStore()) {
      NOTICE.show({ tone: "info", title: "You are offline",
                    text: "This saved trip has no offline copy yet. Plan it once with internet and save it again." });
      return;
    }
    caches.open(STORE).then(function (c) { return c.match(copyUrl(t.id)); })
      .then(function (res) { return res ? res.json() : null; })
      .then(function (plan) {
        if (!plan || !plan.legs) throw new Error("no copy");
        document.querySelector(".panel").classList.add("has-result");
        el("status-block").hidden = true;
        showOptions(plan);
        if (SHEET.isPhone()) { SHEET.set("peek"); document.querySelector(".panel").scrollTop = 0; DRAWER.open(); }
        NOTICE.show({ tone: "info", title: "Your saved copy",
                      text: "You are offline, so this is the route saved on " +
                            new Date(t.copied || t.saved).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) +
                            ". The map, your location and the stop alarm work without internet." });
      })
      .catch(function () {
        NOTICE.show({ tone: "info", title: "You are offline",
                      text: "The offline copy of this trip was cleared by the browser. Plan it again with internet." });
      });
  }

  // Map tiles along the route, so the map still shows it offline. Tiles at
  // the zooms the trip is seen at; a band one tile wide either side.
  function tilesFor(plan) {
    var pts = [];
    plan.legs.forEach(function (l) { pts = pts.concat(l.geometry || []); });
    if (!pts.length) return [];
    var out = {}, n = 0, LIMIT = 700;
    function add(z, x, y) {
      var max = Math.pow(2, z);
      if (x < 0 || y < 0 || x >= max || y >= max) return;
      var k = z + "/" + x + "/" + y;
      if (!out[k] && n < LIMIT) { out[k] = true; n++; }
    }
    function tx(lon, z) { return Math.floor((lon + 180) / 360 * Math.pow(2, z)); }
    function ty(lat, z) {
      var r = lat * Math.PI / 180;
      return Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * Math.pow(2, z));
    }
    var lats = pts.map(function (p) { return p[0]; }), lons = pts.map(function (p) { return p[1]; });
    var s = Math.min.apply(null, lats), nn = Math.max.apply(null, lats);
    var w = Math.min.apply(null, lons), e = Math.max.apply(null, lons);
    for (var z = 6; z <= 9; z++) {                      // the whole area, coarse
      for (var x = tx(w, z) - 1; x <= tx(e, z) + 1; x++)
        for (var y = ty(nn, z) - 1; y <= ty(s, z) + 1; y++) add(z, x, y);
    }
    for (z = 10; z <= 14; z++) {                        // along the line, fine
      var last = null;
      for (var i = 0; i < pts.length; i++) {
        var px = tx(pts[i][1], z), py = ty(pts[i][0], z), key = px + "," + py;
        if (key === last) continue;
        last = key;
        for (var dx = -1; dx <= 1; dx++) for (var dy = -1; dy <= 1; dy++) add(z, px + dx, py + dy);
      }
    }
    return Object.keys(out).map(function (k) { return "/tiles/" + k + ".pbf"; });
  }

  function keepOffline(t, plan, say) {
    if (!canStore()) { say("Saved. (This browser can't keep it offline.)"); return Promise.resolve(false); }
    // A copy without the other route options keeps it small.
    var copy = JSON.parse(JSON.stringify(plan));
    delete copy.alternatives;
    var tiles = tilesFor(copy), done = 0, ok = 0;
    return caches.open(STORE).then(function (c) {
      return c.put(copyUrl(t.id), new Response(JSON.stringify(copy), { headers: { "Content-Type": "application/json" } }))
        .then(function () {
          say("Saved. Keeping the map for offline…");
          var queue = tiles.slice();
          function worker() {
            var url = queue.shift();
            if (!url) return Promise.resolve();
            return c.match(url).then(function (hit) {
              if (hit) { ok++; return null; }
              return fetch(url).then(function (res) {
                if (res.ok && res.status === 200) { ok++; return c.put(url, res); }
                if (res.status === 204) ok++;           // sea: nothing to draw
                return null;
              });
            }).catch(function () { /* a tile missed: the rest still help */ })
              .then(function () {
                done++;
                if (done % 20 === 0) say("Saved. Keeping the map for offline… " + Math.round(done / tiles.length * 100) + "%");
                return worker();
              });
          }
          return Promise.all([worker(), worker(), worker(), worker(), worker(), worker()]);
        });
    }).then(function () {
      var list = load();
      list.forEach(function (x) { if (x.id === t.id) { x.offline = true; x.copied = Date.now(); } });
      save(list);
      renderChips();
      say("Saved, and ready offline: the route and " + ok + " map pieces are on this phone.");
      return true;
    }).catch(function () {
      say("Saved. The offline copy didn't fit on this phone.");
      return false;
    });
  }

  function remove(id) {
    var list = load().filter(function (t) { return t.id !== id; });
    save(list);
    renderChips();
    updateSaveButton();
    if (!canStore()) return;
    caches.open(STORE).then(function (c) {
      if (!list.length) return caches.delete(STORE);    // the tiles go with the last trip
      return c.delete(copyUrl(id));
    }).catch(function () {});
  }

  /* --------------------------------------------------- the save button */

  function updateSaveButton() {
    var btn = $("trip-save");
    if (!btn) return;
    var s = find(current());
    btn.classList.toggle("is-saved", !!s);
    btn.setAttribute("aria-pressed", String(!!s));
    $("trip-save-text").textContent = s ? "Saved as " + labelOf(s) : "Save trip";
  }

  function openBox() {
    var box = $("trip-save-box"), s = find(current());
    box.hidden = false;
    $("trip-save-msg").textContent = "";
    $("trip-save-pick").hidden = !!s;
    $("trip-save-have").hidden = !s;
    if (s) {
      $("trip-save-have-text").textContent = "This trip is saved as " + labelOf(s) + (s.offline ? ", and works offline." : ".");
      return;
    }
    var list = load();
    // Home and Work are offered first, unless already taken by another trip.
    picked = !list.some(function (t) { return t.kind === "home"; }) ? "home"
           : !list.some(function (t) { return t.kind === "work"; }) ? "work" : "custom";
    pick(picked);
  }

  function pick(kind) {
    picked = kind;
    Array.prototype.forEach.call(document.querySelectorAll("#trip-save-kinds [data-kind]"), function (b) {
      b.setAttribute("aria-checked", String(b.getAttribute("data-kind") === kind));
    });
    var name = $("trip-save-name");
    name.hidden = kind !== "custom";
    var taken = load().filter(function (t) { return t.kind === kind && kind !== "custom"; })[0];
    $("trip-save-msg").textContent = taken ? "This replaces your " + KINDS[kind].label + " trip to " +
                                             (short(taken.to.name) || "a saved place") + "." : "";
    if (kind === "custom") { name.focus(); }
  }

  function commit() {
    var t = current(), plan = state.options && state.options[state.selected || 0];
    if (!t || !plan) return;
    var label = "";
    if (picked === "custom") {
      label = ($("trip-save-name").value || "").trim().slice(0, 24);
      if (!label) { $("trip-save-msg").textContent = "Give it a name, like Gym or College."; $("trip-save-name").focus(); return; }
    }
    var list = load().filter(function (x) {
      return !(picked !== "custom" && x.kind === picked) && !(x.mode === t.mode && near(x.from, t.from) && near(x.to, t.to));
    });
    if (list.length >= MAX) { $("trip-save-msg").textContent = "You can keep " + MAX + " trips. Remove one first (Edit, under the search boxes)."; return; }
    t.id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    t.kind = picked;
    t.label = label;
    t.saved = Date.now();
    t.offline = false;
    // Home and Work first, then the rider's own, newest first.
    var rank = { home: 0, work: 1, custom: 2 };
    list.push(t);
    list.sort(function (a, b) { return rank[a.kind] - rank[b.kind] || b.saved - a.saved; });
    if (!save(list)) { $("trip-save-msg").textContent = "This phone's storage is full, so the trip couldn't be saved."; return; }
    renderChips();
    updateSaveButton();
    $("trip-save-pick").hidden = true;
    $("trip-save-have").hidden = false;
    var say = function (m) { $("trip-save-have-text").textContent = m; };
    say("Saved as " + labelOf(t) + ".");
    keepOffline(t, plan, say);
  }

  /* ---------------------------------------------------------------- init */

  function init() {
    if (!$("saved-trips") || !$("trip-save")) return;
    renderChips();
    $("saved-trips-row").addEventListener("click", onChip);
    $("trip-save").addEventListener("click", function () {
      var box = $("trip-save-box");
      if (box.hidden) openBox(); else box.hidden = true;
    });
    $("trip-save-kinds").addEventListener("click", function (e) {
      var b = e.target.closest("[data-kind]");
      if (b) pick(b.getAttribute("data-kind"));
    });
    $("trip-save-name").addEventListener("keydown", function (e) { if (e.key === "Enter") commit(); });
    $("trip-save-ok").addEventListener("click", commit);
    $("trip-save-cancel").addEventListener("click", function () { $("trip-save-box").hidden = true; });
    $("trip-save-remove").addEventListener("click", function () {
      var s = find(current());
      if (s) remove(s.id);
      $("trip-save-box").hidden = true;
    });
    // A new plan on screen (app.js redraws the summary): is it a saved one?
    var summary = $("summary");
    if (summary && "MutationObserver" in window) {
      new MutationObserver(function () { $("trip-save-box").hidden = true; updateSaveButton(); })
        .observe(summary, { childList: true });
    }
    // Another tab saved or removed a trip.
    window.addEventListener("storage", function (e) { if (e.key === KEY) { renderChips(); updateSaveButton(); } });
  }

  return { init: init, list: load, tilesFor: tilesFor };
})();

TRIPS.init();
