/* Vazhi - map interaction and journey rendering. */
"use strict";

var MODES = {
  bus:   { label: "Bus",   color: "#22a06b" },
  train: { label: "Train", color: "#e8833a" },
  metro: { label: "Metro", color: "#8b5cf6" }
};

// How the rider reaches and leaves the stops. Colours and dashes keep the two
// access styles distinct from each other and from every vehicle line.
var ACCESS = {
  walk:        { label: "Walk",        color: "#9fb3c8", dash: "1 9", noun: "On foot" },
  two_wheeler: { label: "Two-wheeler", color: "#4fc3d9", dash: "8 8", noun: "By two-wheeler" }
};

var ICONS = {
  walk: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="13" cy="4.5" r="1.8"/><path d="M12 8.5l-2 5.5 3 3 1 4.5M12 8.5l2.5 2.5 3 .5M12 8.5l-3.5 2-1 3M10 14l-2 3.5-2.5 1.5"/></svg>',
  two_wheeler: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="5.5" cy="16.5" r="3"/><circle cx="18.5" cy="16.5" r="3"/><path d="M5.5 16.5h6.5l3-6.5h-4.5M15 10l1.2-3H19M16.2 7l2.3 9.5"/></svg>',
  bus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="4" y="3" width="16" height="13" rx="2"/><path d="M4 9h16M8 20v-2M16 20v-2"/><circle cx="8" cy="13" r="1.1" fill="currentColor" stroke="none"/><circle cx="16" cy="13" r="1.1" fill="currentColor" stroke="none"/></svg>',
  train: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="5" y="3" width="14" height="13" rx="3"/><path d="M5 10h14M8 20l2-3M16 20l-2-3"/><circle cx="9" cy="13" r="1.1" fill="currentColor" stroke="none"/><circle cx="15" cy="13" r="1.1" fill="currentColor" stroke="none"/></svg>',
  metro: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M6 17V7a3 3 0 0 1 3-3h6a3 3 0 0 1 3 3v10"/><path d="M6 11h12M7 21l2-3M17 21l-2-3"/><circle cx="9.5" cy="14" r="1.1" fill="currentColor" stroke="none"/><circle cx="14.5" cy="14" r="1.1" fill="currentColor" stroke="none"/></svg>'
};

var state = {
  mode: null,          // chosen by the user; nothing is assumed
  access: "walk",
  origin: null,
  destination: null,
  active: "origin",
  planning: false,
  source: null
};

var el = function (id) { return document.getElementById(id); };

/* ------------------------------------------------------------------ map */

// All of India until the user's location (or district) is known.
var map = L.map("map", { zoomControl: true, attributionControl: true })
  .setView([22.6, 79.0], 5);

// A plain, delivery-app style base map (see basemap.js).
BASEMAP.init(map);

var routeLayer = L.layerGroup().addTo(map);
var pointLayer = L.layerGroup().addTo(map);
var whereLayer = L.layerGroup().addTo(map);   // "you are here" or the district outline

function pinIcon(color) {
  return L.divIcon({
    className: "",
    html: '<div class="pin" style="width:16px;height:16px;background:' + color + '"></div>',
    iconSize: [16, 16],
    iconAnchor: [8, 8]
  });
}

function stopIcon(color, size) {
  var s = size || 12;
  return L.divIcon({
    className: "",
    html: '<div class="stopdot" style="width:' + s + 'px;height:' + s + 'px;background:' + color + '"></div>',
    iconSize: [s, s],
    iconAnchor: [s / 2, s / 2]
  });
}

function changeIcon(color) {
  return L.divIcon({
    className: "",
    html: '<div class="stopdot stopdot--change" style="width:17px;height:17px;background:' + color + '"></div>',
    iconSize: [17, 17],
    iconAnchor: [8.5, 8.5]
  });
}

function setPoint(role, latlng, name) {
  state[role] = { lat: latlng.lat, lon: latlng.lng };
  var label = name || latlng.lat.toFixed(5) + ", " + latlng.lng.toFixed(5);
  el(role === "origin" ? "origin-input" : "dest-input").value =
    name || "Point on the map (" + label + ")";
  drawPoints();
  prefetch(state[role]);
  if (role === "origin" && !state.destination) setActive("destination");
}

// Ask the server to start downloading this area's stations and track now,
// so they are usually on disk before "Plan journey" is pressed.
function prefetch(p) {
  if (!p || !state.mode) return;
  // Train trips over 300 km come from the timetable, which needs no map
  // download - don't start one on the shared OpenStreetMap service.
  var other = p === state.origin ? state.destination : state.origin;
  if (state.mode === "train" && other &&
      L.latLng(p.lat, p.lon).distanceTo(L.latLng(other.lat, other.lon)) > 300000) return;
  var q = new URLSearchParams({ lat: p.lat, lon: p.lon, mode: state.mode });
  fetch("/api/prefetch?" + q.toString()).catch(function () { /* best effort */ });
}

function drawPoints() {
  pointLayer.clearLayers();
  [["origin", "#4da3ff"], ["destination", "#f2686b"]].forEach(function (pair) {
    var role = pair[0], p = state[role];
    if (!p) return;
    var m = L.marker([p.lat, p.lon], { icon: pinIcon(pair[1]), draggable: true, zIndexOffset: 1000 })
      .addTo(pointLayer)
      .bindTooltip(role === "origin" ? "Start" : "Destination", { direction: "top", offset: [0, -8] });
    m.on("dragend", function (e) { setPoint(role, e.target.getLatLng()); });
  });
}

function setActive(role) {
  state.active = role;
  ["origin", "destination"].forEach(function (r) {
    el("point-" + r).classList.toggle("is-active", r === role);
  });
  el("hint").textContent = "Clicking the map sets the " +
    (role === "origin" ? "start point." : "destination.");
}

map.on("click", function (e) { setPoint(state.active, e.latlng); });

/* ---------------------------------------------------------------- modes */

// One row of toggle buttons that writes the chosen id into state[key].
function buildToggle(wrapId, options, key) {
  var wrap = el(wrapId);
  Object.keys(options).forEach(function (id) {
    var b = document.createElement("button");
    b.className = "mode";
    b.type = "button";
    b.style.setProperty("--mode-color", options[id].color);
    b.innerHTML = ICONS[id] + "<span>" + options[id].label + "</span>";
    b.setAttribute("aria-pressed", String(id === state[key]));
    b.addEventListener("click", function () {
      state[key] = id;
      Array.prototype.forEach.call(wrap.children, function (c, i) {
        c.setAttribute("aria-pressed", String(Object.keys(options)[i] === id));
      });
      if (key === "mode") { prefetch(state.origin); prefetch(state.destination); }
    });
    wrap.appendChild(b);
  });
}

function buildModes() {
  buildToggle("modes", MODES, "mode");
  buildToggle("access", ACCESS, "access");
}

/* --------------------------------------------------------------- random */

function randomInView(role) {
  var b = map.getBounds();
  var pad = 0.12;                       // keep clear of the very edge
  var lat = b.getSouth() + (b.getNorth() - b.getSouth()) * (pad + Math.random() * (1 - 2 * pad));
  var lon = b.getWest() + (b.getEast() - b.getWest()) * (pad + Math.random() * (1 - 2 * pad));
  setPoint(role, L.latLng(lat, lon));
}

document.querySelectorAll("[data-random]").forEach(function (btn) {
  btn.addEventListener("click", function (ev) {
    ev.stopPropagation();
    randomInView(btn.getAttribute("data-random"));
  });
});

document.querySelectorAll(".point").forEach(function (row) {
  row.addEventListener("click", function () { setActive(row.getAttribute("data-role")); });
});

/* --------------------------------------------------------------- search */

// Place search for the Start and Destination boxes: suggestions from every
// named place in Kerala as you type (the server's local index), online search
// as a backup.
function placeSearch(role, input, list) {
  var timer = null, items = [], active = -1, asked = 0;

  // Rank places near where the trip starts (or near the other end, or the
  // part of the map in view) first.
  function near() {
    var p = (role === "destination" ? state.origin : state.destination) || state.origin ||
            { lat: map.getCenter().lat, lon: map.getCenter().lng };
    return "&lat=" + p.lat.toFixed(4) + "&lon=" + p.lon.toFixed(4);
  }
  function close() { list.hidden = true; active = -1; }
  function choose(r) {
    setPoint(role, L.latLng(r.lat, r.lon), r.name);
    close();
    input.blur();
    SHEET.set("half");             // phones: back down, so the map shows the place
    map.setView([r.lat, r.lon], Math.max(map.getZoom(), 14));
  }
  function show(results, empty) {
    items = results;
    active = -1;
    list.innerHTML = results.length ? results.map(function (r, i) {
      return '<li role="option" data-i="' + i + '"><span class="s-name">' + escapeHtml(r.name) +
             '</span><span class="s-detail">' + escapeHtml(r.detail || "") + "</span></li>";
    }).join("") : '<li class="s-empty">' + escapeHtml(empty) + "</li>";
    list.hidden = false;
    Array.prototype.forEach.call(list.querySelectorAll("[data-i]"), function (li) {
      // mousedown, not click: it fires before the input loses focus.
      li.addEventListener("mousedown", function (e) { e.preventDefault(); choose(items[+li.getAttribute("data-i")]); });
    });
  }
  function ask(full) {
    var q = input.value.trim();
    if (q.length < 2) { close(); return; }
    var ticket = ++asked;
    fetch("/api/search?q=" + encodeURIComponent(q) + near() + (full ? "&full=1" : ""))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (ticket !== asked) return;          // an older answer arriving late
        show(d.results || [], "No place found - try another spelling, or tap the map.");
      })
      .catch(function () { if (ticket === asked) show([], "Search failed - check the connection."); });
  }
  input.addEventListener("focus", function () {
    setActive(role);
    // Phones: the keyboard takes half the screen, so raise the sheet and put
    // the box at its top - the suggestions then appear right under it.
    if (SHEET.isPhone()) {
      SHEET.set("full");
      // After the sheet has slid up: scroll the sheet itself so the box sits
      // just under the handle (the row's offsetParent is the fixed sheet).
      setTimeout(function () {
        var row = input.closest(".point");
        document.querySelector(".panel").scrollTo({ top: Math.max(0, row.offsetTop - 34), behavior: "smooth" });
      }, 320);
    }
    // A coordinate or "Your location" is not a search: select it to overtype.
    if (/^(Point on the map|Your location)/.test(input.value)) input.select();
    else if (input.value.trim().length >= 2) ask(false);
  });
  input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { ask(false); }, 150); });
  input.addEventListener("blur", function () { setTimeout(close, 150); });
  input.addEventListener("keydown", function (e) {
    var lis = list.querySelectorAll("[data-i]");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!lis.length) return;
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + lis.length) % lis.length;
      lis.forEach(function (li, i) { li.classList.toggle("is-active", i === active); });
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (active >= 0 && items[active]) choose(items[active]);
      else ask(true);                           // Enter: also search online
    } else if (e.key === "Escape") {
      close();
    }
  });
}
placeSearch("origin", el("origin-input"), el("origin-suggest"));
placeSearch("destination", el("dest-input"), el("dest-suggest"));

/* ------------------------------------------------------------- progress */

function resetStatus() {
  el("status-block").hidden = false;
  el("status").innerHTML = "";
}

function pushStatus(message, kind) {
  var list = el("status");
  var live = list.querySelector(".is-live");
  if (live) live.classList.remove("is-live");
  var li = document.createElement("li");
  li.textContent = message;
  // An explicit "" means a finished line; only a missing kind means "in progress".
  li.className = kind === undefined ? "is-live" : kind;
  list.appendChild(li);
  list.scrollTop = list.scrollHeight;
  // The panel scrolls on its own, and on a short screen the progress list
  // sits below the Plan button: keep the newest line on screen, or a plan
  // that finishes (or fails) looks as if nothing happened.
  li.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

/* ---------------------------------------------------------------- plan */

function planJourney() {
  if (state.planning) return;
  if (!state.mode) {
    resetStatus();
    pushStatus("Choose Bus, Train or Metro first.", "is-error");
    el("modes").scrollIntoView({ block: "nearest", behavior: "smooth" });
    return;
  }
  if (!state.origin || !state.destination) {
    resetStatus();
    pushStatus("Set both a start and a destination first.", "is-error");
    return;
  }

  state.planning = true;
  SHEET.set("half");             // phones: show the progress, keep some map
  el("plan").disabled = true;
  el("plan").textContent = "Planning...";
  el("result-block").hidden = true;
  routeLayer.clearLayers();
  resetStatus();

  var params = new URLSearchParams({
    olat: state.origin.lat, olon: state.origin.lon,
    dlat: state.destination.lat, dlon: state.destination.lon,
    mode: state.mode,
    access: state.access
  });

  var source = new EventSource("/api/plan?" + params.toString());
  var settled = false;          // the stream resolves exactly once
  state.source = source;

  source.addEventListener("progress", function (ev) {
    pushStatus(JSON.parse(ev.data).message);
  });

  source.addEventListener("result", function (ev) {
    if (settled) return;
    finish();
    pushStatus("Done.", "");
    showOptions(JSON.parse(ev.data));
    SHEET.set("half");
    el("result-block").scrollIntoView({ block: "start", behavior: "smooth" });
  });

  // Fires both for an error the planner sent us and for a dropped connection;
  // only the former carries a JSON payload. A dropped connection is reported,
  // not retried: EventSource would otherwise reconnect on its own and silently
  // start the whole plan again from the beginning.
  source.addEventListener("error", function (ev) {
    if (settled) return;
    var message = null;
    try { message = JSON.parse(ev.data).error; } catch (e) { /* transport-level */ }
    finish();
    pushStatus(message || "Lost contact with the local server.", "is-error");
  });

  function finish() {
    settled = true;
    state.planning = false;
    el("plan").disabled = false;
    el("plan").textContent = "Plan journey";
    source.close();
  }
}

el("plan").addEventListener("click", planJourney);

el("clear").addEventListener("click", function () {
  if (state.source) state.source.close();
  state.planning = false;
  state.origin = state.destination = null;
  state.options = null;
  ALERTS.stop();
  el("trip").hidden = true;
  el("plan").disabled = false;
  el("plan").textContent = "Plan journey";
  ["origin", "destination"].forEach(function (r) {
    el(r === "origin" ? "origin-input" : "dest-input").value = "";
  });
  routeLayer.clearLayers();
  pointLayer.clearLayers();
  el("result-block").hidden = true;
  el("status-block").hidden = true;
  el("legend").hidden = true;
  setActive("origin");
});

/* -------------------------------------------------------------- results */

function fmtKm(m) { return m < 1000 ? Math.round(m / 10) * 10 + " m" : (m / 1000).toFixed(1) + " km"; }
function fmtMin(s) {
  var m = Math.round(s / 60);
  return m < 60 ? m + " min" : Math.floor(m / 60) + " h " + String(m % 60).padStart(2, "0");
}

// A plan arrives with its alternatives (other roads, other trains), fastest
// first. Like a map app, the fastest is shown; the others are grey lines and
// cards to switch to.
function showOptions(plan) {
  state.options = [plan].concat(plan.alternatives || []);
  selectOption(0, true);
}

function selectOption(i, fit) {
  state.selected = i;
  renderPlan(state.options[i], fit);
}

function renderOptions(plan) {
  var box = el("options");
  var opts = state.options || [];
  if (opts.length < 2) { box.hidden = true; box.innerHTML = ""; return; }
  box.innerHTML = opts.map(function (o, j) {
    return '<button type="button" class="option' + (o === plan ? " is-selected" : "") +
      '" data-option="' + j + '"><span class="option__label">' +
      escapeHtml(o.option_label || "Option " + (j + 1)) + '</span><span class="option__time">' +
      escapeHtml(o.summary.duration_text) + '</span><span class="option__dist">' +
      escapeHtml(o.summary.distance_text) + (j === 0 ? " &middot; fastest" : "") + "</span></button>";
  }).join("");
  box.hidden = false;
  Array.prototype.forEach.call(box.querySelectorAll("[data-option]"), function (b) {
    b.addEventListener("click", function () { selectOption(Number(b.getAttribute("data-option")), false); });
  });
}

function renderPlan(plan, fit) {
  var color = plan.color;
  var access = ACCESS[plan.access] || ACCESS.walk;

  /* ---- map ---- */
  routeLayer.clearLayers();
  // The other options first, in grey underneath: click one to switch to it.
  (state.options || []).forEach(function (opt, j) {
    if (opt === plan) return;
    opt.legs.forEach(function (leg) {
      if (!leg.geometry || leg.geometry.length < 2) return;
      L.polyline(leg.geometry, { color: "#8d97a8", weight: leg.type === "transit" ? 6 : 4,
                                 opacity: 0.6, lineCap: "round", lineJoin: "round" })
        .addTo(routeLayer)
        .bindTooltip(escapeHtml(opt.option_label || "Another route") + " &middot; " +
                     escapeHtml(opt.summary.duration_text), { sticky: true })
        .on("click", function () { selectOption(j, false); });
    });
  });
  plan.legs.forEach(function (leg) {
    if (!leg.geometry || leg.geometry.length < 2) return;
    var isAccess = leg.type !== "transit";
    L.polyline(leg.geometry, {
      color: isAccess ? access.color : color,
      weight: isAccess ? 4 : 6,
      opacity: isAccess ? 0.95 : 0.9,
      dashArray: isAccess ? access.dash : null,
      lineCap: "round",
      lineJoin: "round"
    }).addTo(routeLayer);
  });

  // Every train or vehicle leg: a long train trip may have two, with a
  // change between them.
  plan.legs.filter(function (l) { return l.type === "transit"; }).forEach(function (transit) {
    var changes = transit.changes || [];
    (transit.stops_passed || []).forEach(function (s) {
      var isChange = changes.indexOf(s.name) !== -1;
      L.marker([s.lat, s.lon], {
        icon: isChange ? changeIcon(color) : stopIcon(color, 9),
        zIndexOffset: isChange ? 400 : 0
      })
        .addTo(routeLayer)
        .bindTooltip(isChange ? "<b>" + escapeHtml(s.name) + "</b><br>Change lines here"
                              : escapeHtml(s.name || "unnamed stop"),
                     { direction: "top", offset: [0, -6] });
    });
    [[transit.from, "Board here"], [transit.to, "Get off here"]].forEach(function (pair) {
      L.marker([pair[0].lat, pair[0].lon], { icon: stopIcon(color, 15), zIndexOffset: 500 })
        .addTo(routeLayer)
        .bindTooltip("<b>" + escapeHtml(pair[0].name || "Unnamed stop") + "</b><br>" + pair[1],
                     { direction: "top", offset: [0, -9] });
    });
  });

  // Fit the map to every option when the plan arrives; switching between
  // options keeps the view where it is.
  if (fit !== false) {
    var all = [];
    (state.options || [plan]).forEach(function (o) {
      o.legs.forEach(function (l) { all = all.concat(l.geometry || []); });
    });
    map.fitBounds(L.latLngBounds(all), SHEET.fitOptions(45));
  }
  renderOptions(plan);
  ALERTS.attach(plan);

  /* ---- summary ---- */
  var s = plan.summary;
  el("summary").innerHTML =
    '<div><b>' + s.duration_text + '</b><span>Total time</span></div>' +
    '<div><b>' + s.distance_text + '</b><span>Total distance</span></div>' +
    '<div><b>' + fmtKm(s.access_m) + '</b><span>' + access.noun + '</span></div>';

  /* ---- itinerary ---- */
  var list = el("itinerary");
  list.innerHTML = "";
  plan.legs.forEach(function (leg) {
    var li = document.createElement("li");
    li.className = "leg leg--" + leg.type;
    li.style.setProperty("--leg-color", leg.type === "transit" ? color :
                         leg.type === "wait" ? "#7d8a9c" : access.color);

    var meta = leg.type === "wait" ? "Wait about " + fmtMin(leg.duration_s)
             : fmtKm(leg.distance_m) + " &middot; about " + fmtMin(leg.duration_s);
    if (leg.type === "transit" && leg.stops_passed && leg.stops_passed.length) {
      meta += " &middot; " + leg.stops_passed.length +
              (leg.stops_passed.length === 1 ? " stop" : " stops") + " en route";
    }
    if (leg.changes && leg.changes.length) {
      meta += " &middot; " + leg.changes.length + " change" + (leg.changes.length > 1 ? "s" : "");
    }
    if (leg.approximate) meta += " &middot; straight-line estimate";

    var html = '<div class="leg__title">' + escapeHtml(leg.label) + "</div>" +
               (leg.detail ? '<div class="leg__detail">' + escapeHtml(leg.detail) + "</div>" : "") +
               '<div class="leg__meta">' + meta + "</div>";

    if (leg.type === "transit" && leg.stops_passed && leg.stops_passed.length) {
      html += '<details class="leg__stops"><summary>' + leg.stops_passed.length +
              (leg.stops_passed.length === 1 ? " stop" : " stops") + " on the way</summary><ul>" +
              leg.stops_passed.map(function (st, i) {
                var isChange = (leg.changes || []).indexOf(st.name) !== -1;
                return "<li" + (isChange ? ' class="is-change"' : "") + ">" + (i + 1) + ". " +
                       escapeHtml(st.name || "unnamed stop") +
                       (isChange ? " &mdash; change here" : "") + "</li>";
              }).join("") + "</ul></details>";
    }
    li.innerHTML = html;
    list.appendChild(li);
  });

  /* ---- notes ---- */
  el("notes").innerHTML = (plan.notes || []).map(function (n) {
    return "<li>" + escapeHtml(n) + "</li>";
  }).join("");

  el("result-block").hidden = false;

  /* ---- legend ---- */
  var legend = el("legend");
  legend.innerHTML =
    '<div><i style="background:' + access.color + '"></i> ' + access.noun + '</div>' +
    '<div><i style="background:' + color + '"></i> ' + escapeHtml(plan.mode_label) + " line</div>";
  legend.hidden = false;
}

function escapeHtml(text) {
  var d = document.createElement("div");
  d.textContent = text == null ? "" : String(text);
  return d.innerHTML;
}

/* ----------------------------------------------------------------- init */

buildModes();
SHEET.init();
ALERTS.init();
setActive("origin");
locateUser();

/* ------------------------------------------------- offline data status */
// The server downloads a region's stations and track at start. Show how far
// it has got, so a slow first trip is explained, and stop polling once done.
function pollOffline() {
  fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
    var p = s.preload || {}, box = el("offline");
    if (!p.region || p.state === "idle") { setTimeout(pollOffline, 3000); return; }
    if (p.state === "running") {
      box.textContent = "Saving " + p.region + " map data to disk (" + p.done + " of " +
        p.total + " tiles" + (p.current ? ", " + p.current : "") +
        "). Trips there get fast once this finishes.";
      box.className = "offline";
      box.hidden = false;
      setTimeout(pollOffline, 3000);
    } else if (p.state === "done") {
      box.textContent = p.region + " stations and track are saved on disk - trips there plan in seconds.";
      box.className = "offline is-ready";
      box.hidden = false;
    } else {
      box.textContent = "Some " + p.region + " map data could not be saved (OpenStreetMap's " +
        "service is busy). It will download when a trip needs it.";
      box.className = "offline";
      box.hidden = false;
    }
  }).catch(function () { /* the server may be restarting; no status then */ });
}
pollOffline();


/* ------------------------------------------------------------- location */
// On load, ask the browser where the user is. Allowed: show it and make it
// the start. Refused or unavailable: ask the server for the district of this
// internet connection and show that instead - roughly right, never a pin.

// Once the user has placed a point or planned a trip, a late answer about
// where they are must not move the map away from it.
function userIsBusy() {
  return !!(state.options || state.origin || state.destination || state.planning);
}

function setWhere(html) {
  var w = el("where");
  w.innerHTML = html;
  w.hidden = !html;
}

function locateUser() {
  if (!("geolocation" in navigator)) { showDistrict("your browser cannot share a location"); return; }
  setWhere("Finding your location&hellip;");
  navigator.geolocation.getCurrentPosition(function (pos) {
    var here = L.latLng(pos.coords.latitude, pos.coords.longitude);
    var acc = Math.round(pos.coords.accuracy || 0);
    whereLayer.clearLayers();
    if (acc > 0) {
      L.circle(here, { radius: acc, color: "#4da3ff", weight: 1, fillOpacity: 0.08, interactive: false })
        .addTo(whereLayer);
    }
    L.marker(here, {
      icon: L.divIcon({ className: "", html: '<div class="me-dot"></div>', iconSize: [14, 14], iconAnchor: [7, 7] }),
      interactive: true, zIndexOffset: 900
    }).addTo(whereLayer).bindTooltip("You are here", { direction: "top", offset: [0, -8] });
    if (!userIsBusy()) {
      map.setView(here, Math.max(map.getZoom(), 15));
      // On a phone, nudge the map so "you are here" sits above the sheet.
      if (SHEET.covered()) map.panBy([0, Math.round(SHEET.covered() / 2)], { animate: false });
    }
    // Your position is the natural start - unless one was set meanwhile.
    if (!state.origin) {
      setPoint("origin", here);
      el("origin-input").value = "Your location";
    }
    setWhere("Showing <b>your location</b>" + (acc ? " (within about " + fmtKm(acc) + ")" : "") +
             ". It is set as your start; click the map to choose a destination.");
  }, function (err) {
    var why = err && err.code === 1 ? "location access is off"
            : "your location could not be found";
    showDistrict(why);
  }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 });
}

function showDistrict(why) {
  setWhere("Finding your area from your internet connection&hellip;");
  fetch("/api/whereami")
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.error || d.lat == null) throw new Error(d.error || "no answer");
      whereLayer.clearLayers();
      var name = d.district || d.city || "your area";
      var label = name + (d.state && d.state !== name ? ", " + d.state : "");
      var busy = userIsBusy();      // never pull the map off the user's own trip
      if (d.outline) {
        var shape = L.geoJSON(d.outline, {
          style: { color: "#4da3ff", weight: 2, dashArray: "6 6", fillOpacity: 0.05 },
          interactive: false
        }).addTo(whereLayer);
        if (!busy) map.fitBounds(shape.getBounds(), SHEET.fitOptions(30));
      } else if (d.bbox) {
        if (!busy) map.fitBounds([[d.bbox[0], d.bbox[1]], [d.bbox[2], d.bbox[3]]], SHEET.fitOptions(30));
      } else if (!busy) {
        map.setView([d.lat, d.lon], 11);
      }
      L.tooltip({ permanent: true, direction: "center", className: "district-label" })
        .setLatLng(d.outline ? L.geoJSON(d.outline).getBounds().getCenter() : [d.lat, d.lon])
        .setContent(escapeHtml(name))
        .addTo(whereLayer);
      setWhere("Showing <b>" + escapeHtml(label) + "</b>, worked out from your internet " +
               "connection because " + escapeHtml(why) + ". Click the map to set your start, " +
               "or allow location access and press Locate.");
    })
    .catch(function () {
      setWhere("Could not tell where you are (" + escapeHtml(why) + "). Click the map, or " +
               "search for a place, to set your start.");
    });
}

el("locate").addEventListener("click", function (ev) {
  ev.stopPropagation();          // the row click would also select the field
  state.origin = null;           // an explicit request: use the position found
  locateUser();
});
