/* Bus times told by riders, under every bus ride in a plan.
 *
 * Kerala's KSRTC and private buses have no open timetable, so riders add the
 * times they know and confirm or flag each other's. The server matches times
 * by boarding stop and direction (transitnav/timings.py), so a time one rider
 * adds shows up for everyone boarding there going the same way. */
var TIMES = (function () {
  "use strict";

  var OPERATORS = [["ksrtc", "KSRTC"], ["private", "Private bus"], ["swift", "KSRTC Swift"],
                   ["kurtc", "KURTC (city AC)"], ["other", "Other"]];
  var DAYS = [["daily", "Every day"], ["not_sunday", "Not on Sundays"], ["sunday", "Sundays only"]];

  function esc(t) {
    var d = document.createElement("div");
    d.textContent = t == null ? "" : String(t);
    return d.innerHTML;
  }

  // A random id for this browser, so a rider gets one vote per time. The
  // server keeps only a hash of it; nothing else identifies the rider.
  function device() {
    var id = null;
    try { id = localStorage.getItem("vazhi.device"); } catch (e) { /* private mode */ }
    if (!id || !/^[A-Za-z0-9_-]{16,64}$/.test(id)) {
      var b = new Uint8Array(16);
      (window.crypto || window.msCrypto).getRandomValues(b);
      id = Array.prototype.map.call(b, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
      try { localStorage.setItem("vazhi.device", id); } catch (e) { /* kept for this visit */ }
    }
    return id;
  }

  function clock(min) {
    var h = Math.floor(min / 60), m = min % 60;
    return (h % 12 || 12) + ":" + ("0" + m).slice(-2) + (h < 12 ? " am" : " pm");
  }
  function runsOn(t, weekday) {
    if (t.days_id === "daily") return true;
    return t.days_id === "sunday" ? weekday === 0 : weekday !== 0;
  }
  function inText(mins) {
    if (mins < 1) return "now";
    if (mins < 60) return "in " + mins + " min";
    return "in " + Math.floor(mins / 60) + " h" + (mins % 60 ? " " + (mins % 60) + " min" : "");
  }

  function row(t, soon) {
    var bits = [esc(t.operator), "toward " + esc(t.toward)];
    if (t.route) bits.push(esc(t.route));
    if (t.days_id !== "daily") bits.push(esc(t.days));
    return '<li class="times__row" data-ids="' + t.ids.join(",") + '">' +
      '<div class="times__when"><b>' + clock(t.minute) + "</b>" +
      (soon != null ? '<span class="times__soon">' + inText(soon) + "</span>" : "") + "</div>" +
      '<div class="times__what">' + bits.join(" &middot; ") +
      '<span class="times__trust">' + t.ok + (t.ok === 1 ? " rider" : " riders") + " confirmed" +
      (t.wrong ? " &middot; " + t.wrong + " said wrong" : "") + "</span></div>" +
      '<div class="times__vote">' +
      '<button type="button" data-vote="1" class="' + (t.mine === 1 ? "is-on" : "") +
      '" title="I have seen this bus at this time">Seen it</button>' +
      '<button type="button" data-vote="-1" class="' + (t.mine === -1 ? "is-on is-bad" : "") +
      '" title="This bus does not run at this time">Wrong</button></div></li>';
  }

  function form(leg) {
    return '<details class="times__add"><summary>Add a bus time you know</summary>' +
      '<form class="times__form">' +
      '<label>Leaves ' + esc(leg.from.name || "this stop") + ' at <input type="time" name="time" required /></label>' +
      '<label>Bus <select name="operator">' + OPERATORS.map(function (o) {
        return '<option value="' + o[0] + '">' + o[1] + "</option>"; }).join("") + "</select></label>" +
      '<label>Runs <select name="days">' + DAYS.map(function (o) {
        return '<option value="' + o[0] + '">' + o[1] + "</option>"; }).join("") + "</select></label>" +
      '<label class="times__wide">Board on the bus <input name="route" maxlength="60" ' +
      'placeholder="e.g. Aluva - Fort Kochi (optional)" /></label>' +
      '<p class="times__hint">For buses from here toward ' + esc(leg.to.name || "your stop") +
      ". Everyone boarding here that way will see it.</p>" +
      '<button type="submit" class="times__send">Add time</button>' +
      '<p class="times__msg" role="status"></p></form></details>';
  }

  function render(box, leg, times) {
    var now = new Date(), nowMin = now.getHours() * 60 + now.getMinutes(), today = now.getDay();
    var upcoming = times.filter(function (t) { return runsOn(t, today) && t.minute >= nowMin - 1; });
    var head = '<div class="times__head">Bus times at ' + esc(leg.from.name || "this stop") +
               ' <span>added by riders</span></div>';
    var body;
    if (!times.length) {
      body = '<p class="times__none">No rider has added a time here yet. If you know when a bus ' +
             "leaves, add it - it helps everyone boarding here.</p>";
    } else if (upcoming.length) {
      body = '<ul class="times__list">' + upcoming.slice(0, 4).map(function (t) {
        return row(t, t.minute - nowMin); }).join("") + "</ul>";
    } else {
      var tomorrow = times.filter(function (t) { return runsOn(t, (today + 1) % 7); })[0];
      body = '<p class="times__none">No more reported buses today.' +
             (tomorrow ? " First tomorrow: <b>" + clock(tomorrow.minute) + "</b> (" +
                         esc(tomorrow.operator) + ")." : "") + "</p>";
    }
    if (times.length > Math.min(4, upcoming.length)) {
      body += '<details class="times__all"><summary>All ' + times.length +
              (times.length === 1 ? " time" : " times") + ' reported here</summary><ul class="times__list">' +
              times.map(function (t) { return row(t, null); }).join("") + "</ul></details>";
    }
    box.innerHTML = head + body + form(leg);

    Array.prototype.forEach.call(box.querySelectorAll("[data-vote]"), function (b) {
      b.addEventListener("click", function () {
        var ids = b.closest("[data-ids]").getAttribute("data-ids").split(",").map(Number);
        b.disabled = true;
        post("/api/timings/vote", { ids: ids, vote: Number(b.getAttribute("data-vote")), device: device() })
          .then(function () { load(box, leg); })
          .catch(function (err) { b.disabled = false; alert(err.message); });
      });
    });
    var f = box.querySelector(".times__form");
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      var msg = f.querySelector(".times__msg"), send = f.querySelector(".times__send");
      send.disabled = true;
      msg.textContent = "Adding…";
      post("/api/timings", {
        board: leg.from, toward: leg.to, time: f.elements.time.value,
        operator: f.elements.operator.value, days: f.elements.days.value,
        route: f.elements.route.value, device: device()
      }).then(function () {
        return load(box, leg, "Thanks - added for everyone boarding here.");
      }).catch(function (err) {
        send.disabled = false;
        msg.textContent = err.message;
      });
    });
  }

  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body) })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) throw new Error(j.error || "Could not save that - try again.");
          return j;
        });
      });
  }

  function load(box, leg, thanks) {
    var q = "/api/timings?blat=" + leg.from.lat + "&blon=" + leg.from.lon +
            "&alat=" + leg.to.lat + "&alon=" + leg.to.lon + "&device=" + device();
    return fetch(q).then(function (r) { return r.json(); }).then(function (j) {
      if (!box.isConnected) return;           // the plan changed meanwhile
      render(box, leg, j.times || []);
      if (thanks) {
        var d = box.querySelector(".times__msg");
        if (d) { box.querySelector(".times__add").open = true; d.textContent = thanks; }
      }
    }).catch(function () {
      box.innerHTML = '<p class="times__none">Could not load riders\' bus times right now.</p>';
    });
  }

  return {
    // A bus leg's box in the itinerary: fill it with the times riders told.
    attach: function (box, leg) {
      box.innerHTML = '<p class="times__none">Looking for bus times riders added…</p>';
      load(box, leg);
    },
    device: device
  };
})();
