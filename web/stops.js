/* Bus stop names and missing stops, from riders (server: transitnav/stopnames.py).
 *
 * - Under a bus ride: "Wrong stop name?" lets a rider give a stop's real name.
 * - "Add a missing bus stop": tap the map where buses stop and name it.
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
        "Answers like yours keep Vazhi's stops right. A name is used once " + q.needed +
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
