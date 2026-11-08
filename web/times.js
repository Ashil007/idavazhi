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

  function row(t, soon, mine) {
    var bits = [esc(t.operator), "toward " + esc(t.toward)];
    if (t.route) bits.push(esc(t.route));
    if (t.days_id !== "daily") bits.push(esc(t.days));
    return '<li class="times__row' + (mine ? " is-yours" : "") + '" data-ids="' + t.ids.join(",") + '">' +
      '<div class="times__when"><b>' + clock(t.minute) + "</b>" +
      (soon != null ? '<span class="times__soon">' + inText(soon) + "</span>" : "") +
      (mine ? '<span class="times__yours">your bus</span>' : "") + "</div>" +
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

  function render(box, leg, times, plan) {
    var pick = plan ? catchable(plan, leg, times) : null;
    if (plan && isFirstBus(plan, leg)) leave(plan, leg, pick);
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
        return row(t, t.minute - nowMin, pick && pick.t === t); }).join("") + "</ul>";
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
          .then(function () { load(box, leg, null, plan); })
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
        return load(box, leg, "Thanks - added for everyone boarding here.", plan);
      }).catch(function (err) {
        send.disabled = false;
        msg.textContent = err.message;
      });
    });
  }

  /* ----------------------------------------------- leave by, arrive at */

  var BUFFER_S = 120;          // be at the stop two minutes before the bus
  var REMIND_EARLY_S = 300;    // the reminder rings five minutes before "leave by"
  var current = null;          // the plan the leave-by line is for
  var reminder = null;         // {key, timer, at}

  function isFirstBus(plan, leg) {
    return plan === current && plan.legs.filter(function (l) { return l.type === "transit"; })[0] === leg;
  }

  // The first reported bus the rider can still reach if they set off now,
  // as {t, dep, leaveBy, arrive} (Dates), or null. Looks at today and
  // tomorrow, so a late-evening search finds the first bus of the day.
  function catchable(plan, leg, times) {
    var i = plan.legs.indexOf(leg);
    if (i < 0 || !times.length) return null;
    var before = 0, after = 0;
    plan.legs.forEach(function (l, j) {
      if (j < i) before += l.duration_s || 0;
      if (j > i) after += l.duration_s || 0;
    });
    var ride = Math.max(0, (leg.duration_s || 0) - (leg.wait_s || 0));
    var now = new Date(), best = null;
    var earliest = now.getTime() + (before + BUFFER_S) * 1000;
    [0, 1].forEach(function (dayOff) {
      var base = new Date(now.getFullYear(), now.getMonth(), now.getDate() + dayOff);
      times.forEach(function (t) {
        if (!runsOn(t, base.getDay())) return;
        var dep = new Date(base.getTime() + t.minute * 60000);
        if (dep.getTime() < earliest || (best && dep >= best.dep)) return;
        best = { t: t, dep: dep };
      });
    });
    if (!best) return null;
    best.leaveBy = new Date(best.dep.getTime() - (before + BUFFER_S) * 1000);
    best.arrive = new Date(best.dep.getTime() + (ride + after) * 1000);
    return best;
  }

  // "KSRTC bus", "Private bus" (not "Private bus bus").
  function busName(t) { return /bus$/i.test(t.operator) ? t.operator : t.operator + " bus"; }

  function hm(d) {
    var h = d.getHours(), m = d.getMinutes();
    var day = d.toDateString() === new Date().toDateString() ? "" : " tomorrow";
    return (h % 12 || 12) + ":" + ("0" + m).slice(-2) + (h < 12 ? " am" : " pm") + day;
  }

  function leave(plan, leg, pick) {
    var box = document.getElementById("leave");
    if (!pick) { box.hidden = true; box.innerHTML = ""; cancelIfNot(null); return; }
    var mins = Math.round((pick.leaveBy - Date.now()) / 60000);
    var key = pick.t.ids.join(",") + "@" + pick.dep.getTime();
    var on = reminder && reminder.key === key;
    box.innerHTML =
      '<div class="leave__head"><b>' + (mins <= 1 ? "Leave now" : "Leave by " + hm(pick.leaveBy)) + "</b>" +
      (mins > 1 ? "<span>" + inText(mins) + "</span>" : "") + "</div>" +
      '<div class="leave__body">to catch the <b>' + hm(pick.dep) + "</b> " + esc(busName(pick.t)) +
      " at " + esc(leg.from.name || "the stop") + " &middot; arrive about <b>" + hm(pick.arrive) +
      "</b></div>" +
      '<div class="leave__note">From a bus time riders added (' + pick.t.ok + " confirmed). " +
      "The total time above still allows a typical wait.</div>" +
      (mins > 1 ? '<button type="button" class="leave__remind' + (on ? " is-on" : "") + '">' +
                  (on ? "Reminder at " + hm(new Date(reminder.at)) + " - tap to cancel"
                      : "Remind me when to leave") + "</button>" : "");
    box.hidden = false;
    cancelIfNot(key);
    var btn = box.querySelector(".leave__remind");
    if (btn) btn.addEventListener("click", function () {
      if (reminder && reminder.key === key) cancelIfNot(null);
      else remind(key, pick, leg);
      leave(plan, leg, pick);
    });
  }

  // Keep a reminder only while the plan still shows the same bus.
  function cancelIfNot(key) {
    if (reminder && reminder.key !== key) {
      clearTimeout(reminder.timer);
      reminder = null;
    }
  }

  function remind(key, pick, leg) {
    cancelIfNot(null);
    ALERTS.prime();                       // sound and notifications need this tap
    var at = Math.max(Date.now() + 5000, pick.leaveBy.getTime() - REMIND_EARLY_S * 1000);
    var title = "Time to leave for your bus";
    var body = "The " + hm(pick.dep) + " " + busName(pick.t) + " leaves " +
               (leg.from.name || "the stop") + ". Leave by " + hm(pick.leaveBy) + ".";
    reminder = { key: key, at: at, timer: setTimeout(function () {
      reminder = null;
      ALERTS.ring(title, body);
      if (current) leave(current, leg, pick);
    }, at - Date.now()) };
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

  function load(box, leg, thanks, plan) {
    var q = "/api/timings?blat=" + leg.from.lat + "&blon=" + leg.from.lon +
            "&alat=" + leg.to.lat + "&alon=" + leg.to.lon + "&device=" + device();
    return fetch(q).then(function (r) { return r.json(); }).then(function (j) {
      if (!box.isConnected) return;           // the plan changed meanwhile
      render(box, leg, j.times || [], plan);
      if (thanks) {
        var d = box.querySelector(".times__msg");
        if (d) { box.querySelector(".times__add").open = true; d.textContent = thanks; }
      }
    }).catch(function () {
      box.innerHTML = '<p class="times__none">Could not load riders\' bus times right now.</p>';
    });
  }

  return {
    // A new plan (or option) is on screen: the leave-by line waits for its times.
    reset: function (plan) {
      current = plan;
      var box = document.getElementById("leave");
      if (box) { box.hidden = true; box.innerHTML = ""; }
    },
    // A bus leg's box in the itinerary: fill it with the times riders told.
    attach: function (box, leg, plan) {
      box.innerHTML = '<p class="times__none">Looking for bus times riders added…</p>';
      load(box, leg, null, plan);
    },
    device: device
  };
})();
