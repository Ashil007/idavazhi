/* Bus stop names and missing stops, from riders (server: transitnav/stopnames.py).
 *
 * - Under a bus ride: "Wrong stop name?" lets a rider give a stop's real name.
 * - "Add a missing bus stop": tap the map where buses stop and name it.
 * - "No bus runs here?": a rider says the map has a stop where no bus runs.
 *   Once 10 riders say so, Idavazhi stops boarding or leaving buses there.
 * - "Which buses stop here?": private, KSRTC or both, and up to where each
 *   kind goes. Used once 5 riders agree; a place shows under the ride once
 *   2 riders name it ("Unnichira: private buses go up to Kaloor").
 * - When a rider's trip uses a stop that others have renamed, or passes near
 *   a stop riders added, a small card asks them about it. A name is used for
 *   everyone once 30 riders back it (and more than back any other name). */
var STOPS = (function () {
  "use strict";

  var NEEDED = 30;
  var askLayer = null, addLayer = null;
  var picking = false;
  var placing = null;          // adding a missing place: { name, done }
  var queue = [], showing = null;

  function $(id) { return document.getElementById(id); }
  function esc(t) {
    var d = document.createElement("div");
    d.textContent = t == null ? "" : String(t);
    return d.innerHTML;
  }
  function device() { return TIMES.device(); }
  function isMapStop(id) { return /^(node|way|relation)\/\d+$/.test(id || ""); }

  // Without internet the change waits on the phone (outbox.js) and is sent
  // when it is back online; the answer is then { queued: true }.
  function post(url, body) { return OUTBOX.post(url, body); }
  var SAVED = "No internet right now - it's saved on this phone and will be sent when you're back online.";

  // Questions this browser has waved away ("Not sure", or closed): not asked
  // again for a week.
  function skipped(key) {
    try {
      var all = JSON.parse(localStorage.getItem("vazhi.stopSkips") || "{}");
      return all[key] && Date.now() - all[key] < 7 * 864e5;
    } catch (e) { return false; }
  }
  function skip(key) {
    try {
      var all = JSON.parse(localStorage.getItem("vazhi.stopSkips") || "{}");
      all[key] = Date.now();
      localStorage.setItem("vazhi.stopSkips", JSON.stringify(all));
    } catch (e) { /* asked again next time */ }
  }

  /* ------------------------------------------------ wrong stop name? */

  function fixLinks(li, leg) {
    var stops = [leg.from, leg.to].filter(function (s) { return s && s.id; });
    if (!stops.length) return;
    var box = document.createElement("div");
    box.className = "stopfix";
    function links() {
      box.innerHTML = "Wrong stop name? " + stops.map(function (s, i) {
        return '<button type="button" data-i="' + i + '">' + esc(s.name || "unnamed stop") + "</button>";
      }).join(" &middot; ");
      Array.prototype.forEach.call(box.querySelectorAll("button"), function (b) {
        b.addEventListener("click", function () { form(stops[Number(b.getAttribute("data-i"))]); });
      });
    }
    function form(stop) {
      box.innerHTML = '<form class="stopfix__form"><label>What is <b>' + esc(stop.name || "this stop") +
        "</b> really called?" + (stop.map_name ? ' <span>(map: ' + esc(stop.map_name) + ")</span>" : "") +
        '<input name="name" maxlength="60" required placeholder="The name on the stop, or what people call it" /></label>' +
        '<div class="stopfix__btns"><button type="submit">Send</button><button type="button" class="ghostlink">Cancel</button></div>' +
        '<p class="stopfix__msg" role="status"></p></form>';
      var f = box.querySelector("form");
      f.elements.name.focus();
      f.querySelector(".ghostlink").addEventListener("click", links);
      f.addEventListener("submit", function (e) {
        e.preventDefault();
        var msg = f.querySelector(".stopfix__msg");
        msg.textContent = "Sending…";
        var name = f.elements.name.value;
        var req = isMapStop(stop.id)
          ? post("/api/stops/rename", { key: stop.id, name: name, lat: stop.lat, lon: stop.lon,
                                        map_name: stop.map_name || stop.name || "", device: device() })
          : post("/api/stops/answer", { key: stop.id, name: name, device: device() });
        req.then(function (j) {
          box.innerHTML = (j.queued ? esc(SAVED) + " " : "Thanks! ") +
            "Riders who use this stop will be asked what it is called. " +
            "Everyone sees <b>" + esc(name) + "</b> once " + NEEDED + " riders agree.";
        }).catch(function (err) { msg.textContent = err.message; });
      });
    }
    ridersSay(li, stops);
    links();
    li.appendChild(box);
    noBus(li, stops);
    busKinds(li, stops);
  }

  /* ------------------------------------------ which buses stop here? */

  var KIND_SAYS = { private: "only private buses stop here", ksrtc: "only KSRTC buses stop here",
                    both: "private and KSRTC buses stop here" };
  var KIND_ASK = [["private", "Private only"], ["ksrtc", "KSRTC only"], ["both", "Both"]];
  var KIND_SHORT = { private: "private", ksrtc: "KSRTC", both: "both" };
  var FAST_ASK = [["yes", "Yes"], ["no", "No"], ["", "Not sure"]];

  // What riders agree on about the ride's stops: whose buses stop there
  // and up to where each kind goes.
  function ridersSay(li, stops) {
    var lines = [];
    stops.forEach(function (s) {
      var parts = [];
      if (s.buses && KIND_SAYS[s.buses]) parts.push(KIND_SAYS[s.buses]);
      if (s.private_to && s.private_to.length) parts.push("private buses go up to " + s.private_to.join(", "));
      if (s.ksrtc_to && s.ksrtc_to.length) parts.push("KSRTC buses go up to " + s.ksrtc_to.join(", "));
      if (s.fast === "yes") parts.push("fast buses stop here");
      if (s.fast === "no") parts.push("fast buses don't stop here");
      if (parts.length) lines.push("<b>" + esc(s.name || "This stop") + "</b>: " + esc(parts.join("; ")));
    });
    if (!lines.length) return;
    var box = document.createElement("div");
    box.className = "busesay";
    box.innerHTML = lines.join("<br>") + " <span>(riders say)</span>";
    li.appendChild(box);
  }

  // Stops this browser has said which buses use: { key: kind }.
  function saidBuses() {
    try { return JSON.parse(localStorage.getItem("vazhi.busKinds") || "{}"); } catch (e) { return {}; }
  }
  function rememberBuses(key, kind) {
    try {
      var all = saidBuses();
      if (kind) all[key] = kind; else delete all[key];
      localStorage.setItem("vazhi.busKinds", JSON.stringify(all));
    } catch (e) { /* only the label is lost */ }
  }

  function busKinds(li, stops) {
    var box = document.createElement("div");
    box.className = "stopfix";
    function links() {
      var said = saidBuses();
      box.innerHTML = "Which buses stop here? " + stops.map(function (s, i) {
        return '<button type="button" data-i="' + i + '">' + esc(s.name || "unnamed stop") +
               (said[s.id] ? " (you said " + KIND_SHORT[said[s.id]] + ")" : "") + "</button>";
      }).join(" &middot; ");
      Array.prototype.forEach.call(box.querySelectorAll("button"), function (b) {
        b.addEventListener("click", function () { form(stops[Number(b.getAttribute("data-i"))]); });
      });
    }
    function form(stop) {
      var said = saidBuses()[stop.id];
      box.innerHTML = '<form class="stopfix__form busform"><fieldset><legend>Which buses stop at <b>' +
        esc(stop.name || "this stop") + "</b>?</legend>" +
        KIND_ASK.map(function (k) {
          return '<label class="busform__kind"><input type="radio" name="kind" value="' + k[0] + '"' +
                 (said === k[0] ? " checked" : "") + " />" + k[1] + "</label>";
        }).join("") + "</fieldset>" +
        '<label data-kind="private">Private buses from here go up to' +
        '<input name="private_to" maxlength="200" placeholder="e.g. Kaloor, Kakkanad" /></label>' +
        '<label data-kind="ksrtc">KSRTC buses from here go up to' +
        '<input name="ksrtc_to" maxlength="200" placeholder="e.g. Alappuzha, Aluva" /></label>' +
        '<fieldset><legend>Do fast buses stop here? <span class="busform__hint">Fast Passenger, ' +
        "Super Fast, Limited Stop</span></legend>" +
        FAST_ASK.map(function (a) {
          return '<label class="busform__kind"><input type="radio" name="fast" value="' + a[0] + '"' +
                 (a[0] === "" ? " checked" : "") + " />" + a[1] + "</label>";
        }).join("") + "</fieldset>" +
        '<div class="stopfix__btns"><button type="submit">Send</button>' +
        (said ? '<button type="button" class="ghostlink" data-back>Take it back</button>' : "") +
        '<button type="button" class="ghostlink" data-cancel>Cancel</button></div>' +
        '<p class="stopfix__msg" role="status"></p></form>';
      var f = box.querySelector("form");
      var msg = f.querySelector(".stopfix__msg");
      // Only the "up to where" boxes for the buses that stop here.
      function sync() {
        var k = f.elements.kind.value;
        f.querySelector('[data-kind="private"]').hidden = !(k === "private" || k === "both");
        f.querySelector('[data-kind="ksrtc"]').hidden = !(k === "ksrtc" || k === "both");
      }
      Array.prototype.forEach.call(f.elements.kind, function (r) { r.addEventListener("change", sync); });
      sync();
      f.querySelector("[data-cancel]").addEventListener("click", links);
      var back = f.querySelector("[data-back]");
      if (back) back.addEventListener("click", function () {
        msg.textContent = "Sending…";
        post("/api/stops/buses", { key: stop.id, kind: null, device: device() })
          .then(function () { rememberBuses(stop.id, null); links(); })
          .catch(function (err) { msg.textContent = err.message; });
      });
      f.addEventListener("submit", function (e) {
        e.preventDefault();
        var kind = f.elements.kind.value;
        if (!kind) { msg.textContent = "Pick private, KSRTC or both."; return; }
        msg.textContent = "Sending…";
        post("/api/stops/buses", { key: stop.id, kind: kind, device: device(),
                                   private_to: f.elements.private_to.value,
                                   ksrtc_to: f.elements.ksrtc_to.value,
                                   fast: f.elements.fast.value })
          .then(function (j) {
            rememberBuses(stop.id, kind);
            var so = j.votes ? j.votes[kind] : 0;
            box.innerHTML = (j.queued ? esc(SAVED) + " " : "Thanks! ") + (j.kind === kind
              ? "Enough riders agree: Idavazhi now plans with this."
              : "Idavazhi plans with this once " + (j.needed || 5) + " riders agree" +
                (so ? " (" + so + " so far)" : "") + ".") +
              " A place shows under the ride once 2 riders name it." +
              ' <button type="button">Change</button>';
            box.querySelector("button").addEventListener("click", function () { form(stop); });
          })
          .catch(function (err) { msg.textContent = err.message; });
      });
    }
    links();
    li.appendChild(box);
  }

  /* ------------------------------------------------ no bus runs here? */

  // Stops this browser has said no bus runs at: { key: time }.
  function saidNoBus() {
    try { return JSON.parse(localStorage.getItem("vazhi.noBus") || "{}"); } catch (e) { return {}; }
  }
  function rememberNoBus(key, on) {
    try {
      var all = saidNoBus();
      if (on) all[key] = Date.now(); else delete all[key];
      localStorage.setItem("vazhi.noBus", JSON.stringify(all));
    } catch (e) { /* only the label is lost */ }
  }

  function noBus(li, stops) {
    var box = document.createElement("div");
    box.className = "stopfix";
    function links() {
      var said = saidNoBus();
      box.innerHTML = "No bus runs here? " + stops.map(function (s, i) {
        return '<button type="button" data-i="' + i + '">' + esc(s.name || "unnamed stop") +
               (said[s.id] ? " (you said no bus)" : "") + "</button>";
      }).join(" &middot; ");
      Array.prototype.forEach.call(box.querySelectorAll("button"), function (b) {
        b.addEventListener("click", function () { ask(stops[Number(b.getAttribute("data-i"))]); });
      });
    }
    function ask(stop) {
      var said = saidNoBus()[stop.id];
      box.innerHTML = '<div class="stopfix__form">' + (said
        ? "You said no bus stops at <b>" + esc(stop.name || "this stop") + "</b>."
        : "Is it true that no bus stops at <b>" + esc(stop.name || "this stop") +
          "</b>? Say so only if you know the road.") +
        '<div class="stopfix__btns"><button type="button" class="stopfix__yes">' +
        (said ? "Take it back" : "Yes, no bus runs here") + '</button>' +
        '<button type="button" class="ghostlink">Cancel</button></div>' +
        '<p class="stopfix__msg" role="status"></p></div>';
      box.querySelector(".ghostlink").addEventListener("click", links);
      box.querySelector(".stopfix__yes").addEventListener("click", function () {
        var msg = box.querySelector(".stopfix__msg");
        msg.textContent = "Sending…";
        post("/api/stops/nobus", { key: stop.id, no_bus: said ? null : true, device: device() })
          .then(function (j) {
            rememberNoBus(stop.id, !said);
            if (said) { links(); return; }
            box.innerHTML = (j.queued ? esc(SAVED) + " " : "Thanks! ") + (j.used
              ? "Enough riders agree: Idavazhi no longer plans buses from or to this stop."
              : "Idavazhi stops using this stop once " + (j.needed || 10) + " riders say no bus runs here" +
                (j.no_bus ? " (" + j.no_bus + " so far)" : "") + ".") +
              ' <button type="button">Undo</button>';
            box.querySelector("button").addEventListener("click", function () { ask(stop); });
          })
          .catch(function (err) { msg.textContent = err.message; });
      });
    }
    links();
    li.appendChild(box);
  }

  /* ----------------------------------------------- add a missing stop */

  function startAdding() {
    picking = true;
    SHEET.set("peek");                       // phones: show the map to tap on
    card('<div class="ask__title">Add a missing bus stop</div>' +
         '<p class="ask__text">Tap the map where buses stop. Zoom in close so it is in the right place.</p>' +
         '<div class="ask__btns"><button type="button" class="ask__skip" data-cancel>Cancel</button></div>');
    $("ask").querySelector("[data-cancel]").addEventListener("click", function () { picking = false; hideCard(); });
  }

  /* --------------------------------------------- add a missing place */

  // A search found nothing (say "VPK Distributors"): the rider taps where it
  // is and names it; then everyone's search finds it (userplaces.py).
  var KINDS = [["shop", "Shop"], ["office", "Office"], ["food", "Restaurant or hotel"],
               ["health", "Hospital or clinic"], ["school", "School or college"],
               ["worship", "Place of worship"], ["home", "House"], ["other", "Something else"]];
  function addPlace(name, done) {
    picking = false;
    placing = { name: name, done: done };
    SHEET.set("peek");
    card('<div class="ask__title">Add \u201c' + esc(name) + '\u201d</div>' +
         '<p class="ask__text">Tap the map where it is. Zoom in close so it lands on the right building.</p>' +
         '<div class="ask__btns"><button type="button" class="ask__skip" data-cancel>Cancel</button></div>');
    $("ask").querySelector("[data-cancel]").addEventListener("click", function () { placing = null; hideCard(); });
  }
  function placeClick(latlng) {
    var job = placing;
    placing = null;
    hideCard();
    if (!addLayer) addLayer = L.layerGroup().addTo(map);
    addLayer.clearLayers();
    var mark = L.circleMarker(latlng, { radius: 9, color: "#fff", weight: 3, fillColor: "#f5b642",
                                        fillOpacity: 1 }).addTo(addLayer);
    var html = '<form class="addstop"><label>Name<input name="name" maxlength="80" required value="' +
      esc(job.name) + '" /></label><label>What is it?<select name="kind">' +
      KINDS.map(function (k) { return '<option value="' + k[0] + '">' + k[1] + "</option>"; }).join("") +
      '</select></label><button type="submit">Add place</button>' +
      '<p class="addstop__msg" role="status"></p></form>';
    mark.bindPopup(html, { minWidth: 230, closeOnClick: false }).openPopup();
    var f = mark.getPopup().getElement().querySelector("form");
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      var msg = f.querySelector(".addstop__msg"), name = f.elements.name.value.trim();
      msg.textContent = "Adding\u2026";
      post("/api/places/add", { lat: latlng.lat, lon: latlng.lng, name: name,
                                kind: f.elements.kind.value, device: device() })
        .then(function (j) {
          mark.setPopupContent('<div class="addstop">' + (j.queued ? esc(SAVED)
            : j.merged ? "A rider already added it here. Thanks for checking!"
            : "Added. Anyone searching for \u201c" + esc(name) + "\u201d will find it now.") + "</div>");
          if (job.done) job.done(latlng, name);
        })
        .catch(function (err) { msg.textContent = err.message; });
    });
    mark.on("popupclose", function () { setTimeout(function () { addLayer.clearLayers(); }, 0); });
  }

  // Called by the map's click handler first; true when the click was ours.
  function onMapClick(latlng) {
    if (placing) { placeClick(latlng); return true; }
    if (!picking) return false;
    picking = false;
    hideCard();
    if (!addLayer) addLayer = L.layerGroup().addTo(map);
    addLayer.clearLayers();
    var mark = L.circleMarker(latlng, { radius: 9, color: "#fff", weight: 3, fillColor: "#22a06b",
                                        fillOpacity: 1 }).addTo(addLayer);
    var html = '<form class="addstop"><label>Name of this bus stop' +
      '<input name="name" maxlength="60" required placeholder="e.g. Pathadipalam" /></label>' +
      '<button type="submit">Add stop</button><p class="addstop__msg" role="status"></p></form>';
    mark.bindPopup(html, { minWidth: 220, closeOnClick: false }).openPopup();
    var f = mark.getPopup().getElement().querySelector("form");
    f.elements.name.focus();
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      var msg = f.querySelector(".addstop__msg");
      msg.textContent = "Adding…";
      post("/api/stops/add", { lat: latlng.lat, lon: latlng.lng, name: f.elements.name.value,
                               device: device() })
        .then(function (j) {
          mark.setPopupContent('<div class="addstop">' + (j.queued ? esc(SAVED) + " " : j.merged
            ? "A rider already added a stop here - your answer counts for it. "
            : "Added. ") + "Riders planning trips near here will be asked about it, and it is " +
            "used for trips once " + (j.needed || NEEDED) + " riders confirm it.</div>");
        })
        .catch(function (err) { msg.textContent = err.message; });
    });
    mark.on("popupclose", function () { setTimeout(function () { addLayer.clearLayers(); }, 0); });
    return true;
  }

  /* ------------------------------------------ ask riders about stops */

  function card(html) {
    var c = $("ask");
    c.innerHTML = '<button type="button" class="ask__close" aria-label="Close">&times;</button>' + html;
    c.hidden = false;
    c.querySelector(".ask__close").addEventListener("click", function () {
      if (showing) skip(showing.key);
      picking = false;
      placing = null;
      hideCard();
      next();
    });
  }
  function hideCard() {
    $("ask").hidden = true;
    if (askLayer) askLayer.clearLayers();
    showing = null;
  }

  function point(q) {
    if (!askLayer) askLayer = L.layerGroup().addTo(map);
    askLayer.clearLayers();
    L.circleMarker([q.lat, q.lon], { radius: 16, color: "#f5b642", weight: 3, fill: false,
                                     className: "ask-ring" }).addTo(askLayer);
  }

  // Move the map so the stop in question is not hidden behind the card (or,
  // on phones, the sheet): into the middle of the map that can be seen.
  function reveal(q) {
    var box = map.getContainer().getBoundingClientRect();
    var card = $("ask").getBoundingClientRect();
    var p = map.latLngToContainerPoint([q.lat, q.lon]);
    var x = p.x + box.left, y = p.y + box.top, pad = 30;
    var bottom = box.bottom - SHEET.covered();
    var hidden = (x > card.left - pad && x < card.right + pad && y > card.top - pad && y < card.bottom + pad) ||
                 y > bottom - pad || y < box.top + pad || x < box.left + pad || x > box.right - pad;
    if (!hidden) return;
    var top = SHEET.isPhone() ? card.bottom : box.top;
    var targetY = (top + bottom) / 2, targetX = SHEET.isPhone() ? (box.left + box.right) / 2
                                                                 : (box.left + card.left) / 2;
    map.panBy([x - targetX, y - targetY]);
  }

  function show(q) {
    showing = q;
    point(q);
    var opts = q.options.slice(0, 4);
    var html;
    if (q.kind === "name") {
      html = '<div class="ask__title">Quick question about your trip</div>' +
        '<p class="ask__text">What is the bus stop marked on the map called? Riders gave different names.</p>' +
        '<div class="ask__opts">' + opts.map(function (o, i) {
          return '<button type="button" data-name="' + i + '">' + esc(o.name) + "<span>" +
            (o.map ? "on the map" : "") + (o.map && o.votes ? ", " : "") +
            (o.votes ? o.votes + (o.votes === 1 ? " rider" : " riders") : "") + "</span></button>";
        }).join("") + "</div>";
    } else {
      html = '<div class="ask__title">Is there a bus stop here?</div>' +
        '<p class="ask__text">Riders say buses stop at the place marked on the map. Is that right?</p>' +
        '<div class="ask__opts">' + opts.map(function (o, i) {
          return '<button type="button" data-name="' + i + '">Yes, it is called ' + esc(o.name) +
                 (o.votes > 1 ? "<span>" + o.votes + " riders</span>" : "") + "</button>";
        }).join("") +
        '<button type="button" data-no>No, buses do not stop there</button></div>';
    }
    html += '<form class="ask__other" hidden><input name="name" maxlength="60" placeholder="Its name" required />' +
      '<button type="submit">Send</button></form>' +
      '<div class="ask__btns"><button type="button" class="ask__skip" data-other>' +
      (q.kind === "name" ? "Another name" : "Yes, but another name") + "</button>" +
      '<button type="button" class="ask__skip" data-unsure>Not sure</button></div>' +
      '<p class="ask__msg" role="status"></p>';
    card(html);
    reveal(q);
    var c = $("ask");
    Array.prototype.forEach.call(c.querySelectorAll("[data-name]"), function (b) {
      b.addEventListener("click", function () {
        send(q, { name: opts[Number(b.getAttribute("data-name"))].name, exists: q.kind === "new" ? true : undefined });
      });
    });
    var no = c.querySelector("[data-no]");
    if (no) no.addEventListener("click", function () { send(q, { exists: false }); });
    c.querySelector("[data-other]").addEventListener("click", function () {
      var f = c.querySelector(".ask__other");
      f.hidden = false;
      f.elements.name.focus();
    });
    c.querySelector(".ask__other").addEventListener("submit", function (e) {
      e.preventDefault();
      send(q, { name: e.target.elements.name.value, exists: q.kind === "new" ? true : undefined });
    });
    c.querySelector("[data-unsure]").addEventListener("click", function () {
      skip(q.key);
      hideCard();
      next();
    });
  }

  function send(q, answer) {
    var msg = $("ask").querySelector(".ask__msg");
    msg.textContent = "Sending…";
    var body = { key: q.key, device: device() };
    if (answer.name) body.name = answer.name;
    if (answer.exists !== undefined) body.exists = answer.exists;
    post("/api/stops/answer", body).then(function (j) {
      skip(q.key);
      $("ask").innerHTML = '<div class="ask__title">Thank you!</div><p class="ask__text">' +
        (j.queued ? esc(SAVED) + " " : "") +
        "Answers like yours keep Idavazhi's stops right. A name is used once " + q.needed +
        " riders agree on it.</p>";
      setTimeout(function () { if (!showing || showing === q) { hideCard(); next(); } }, 2600);
    }).catch(function (err) { msg.textContent = err.message; });
  }

  function next() {
    while (queue.length) {
      var q = queue.shift();
      if (!skipped(q.key)) { show(q); return; }
    }
  }

  // A plan (or another option of it) is on the screen.
  function afterPlan(plan) {
    if (plan.mode !== "bus" || picking) return;
    var keys = [], pts = [];
    plan.legs.forEach(function (l) {
      if (l.type !== "transit") return;
      [l.from, l.to].forEach(function (s) {
        if (!s) return;
        if (s.id && keys.indexOf(s.id) === -1) keys.push(s.id);
        pts.push(s.lat.toFixed(5) + "," + s.lon.toFixed(5));
      });
    });
    [plan.origin, plan.destination].forEach(function (p) {
      if (p) pts.push(p.lat.toFixed(5) + "," + p.lon.toFixed(5));
    });
    fetch("/api/stops/ask?keys=" + encodeURIComponent(keys.join(",")) +
          "&points=" + encodeURIComponent(pts.join(";")) + "&device=" + device())
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (showing) return;                 // already asking about something
        queue = (j.questions || []).filter(function (q) { return !skipped(q.key); });
        next();
      })
      .catch(function () { /* no questions this time */ });
  }

  function init() {
    var b = $("add-stop");
    if (b) b.addEventListener("click", startAdding);
  }

  return { init: init, fixLinks: fixLinks, afterPlan: afterPlan, onMapClick: onMapClick, addPlace: addPlace };
})();
