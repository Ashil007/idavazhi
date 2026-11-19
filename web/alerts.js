/* Trip alerts: "wake me before my stop".
 *
 * After a journey is planned the rider can start trip alerts. The page then
 * follows their position (or, in demo mode, a pretend position moving along
 * the route) and, for every place they must get off - a change of train or
 * the final stop:
 *   - well before it (WAKE_M, or the distance the rider picked): a real
 *     alarm - a looping alarm-clock sound and vibration that go on until
 *     "I'm awake" is tapped, and a notification - the passenger may be asleep;
 *   - on arrival (ARRIVE_M): "this is your stop". It rings like the first
 *     alarm until tapped if that one was never answered; after "I'm awake"
 *     it is a short chime.
 *
 * A web page can only do this while it is open; the screen is kept on while
 * tracking, because phones pause hidden pages.
 */
"use strict";

var ALERTS = (function () {
  var WAKE_M = 2000;        // "auto" (Default): wake the rider this far before the stop
  var SETTING = "vazhi.alarmDistance";   // the rider's own choice, kept on this device
  var ARRIVE_M = 150;       // "this is your stop"
  var SIM_SECONDS = 10;     // demo: the whole trip in about this long

  var plan = null, targets = [], fired = {};
  var watchId = null, simTimer = null, running = false, simulating = false;
  var last = null, speed = null;                  // previous fix and m/s
  var audio = null, beepTimer = null, wakeLock = null, swReg = null;
  var siren = null;        // the looping alarm sound (an <audio> element)
  var awake = false;       // the rider tapped "I'm awake" during this ride
  var TONES = {
    classic: { name: "Classic Digital", desc: "Digital clock 4-beep pulse" },
    chime:   { name: "Harmonic Bell",   desc: "Soft dual acoustic chime" },
    marimba: { name: "Morning Marimba", desc: "Cheerful 4-note melody" },
    radar:   { name: "Sonar Pulse",     desc: "Rhythmic radar sweep" },
    siren:   { name: "Emergency Siren", desc: "Urgent alternating siren" },
    cosmic:  { name: "Cosmic Synth",    desc: "Futuristic space arpeggio" },
    harp:    { name: "Gentle Harp",     desc: "Cascading soothing strings" },
    birds:   { name: "Forest Birds",    desc: "Crisp natural chirping" }
  };
  var pendingTone = "classic";
  var TONE_SETTING = "vazhi.alarmTone";
  var VOL_SETTING = "vazhi.alarmVolume";
  var currentTone = "classic";
  var currentVolume = 1.0;
  var toneUrls = {};
  var previewTimer = null;

  // Spoken announcements - "Arriving Vyttila in 2 minutes" - from the phone's
  // own speech engine: no server, and it works offline.
  var VOICE_SETTING = "vazhi.voice";
  var SOON_S = 120;           // spoken warnings come this long before each alarm
  var SAY_EVERY_MS = 12000;   // while the alarm rings, the stop's name is said again this often
  var voiceTimer = null;      // that repeat
  var talking = false;        // the phone is speaking (the alarm sound waits)

  var el = function (id) { return document.getElementById(id); };

  function haversine(a, b) {
    var R = 6371000, toRad = Math.PI / 180;
    var dLat = (b[0] - a[0]) * toRad, dLon = (b[1] - a[1]) * toRad;
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(a[0] * toRad) * Math.cos(b[0] * toRad) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  function fmtDist(m) { return m < 1000 ? Math.round(m / 10) * 10 + " m" : (m / 1000).toFixed(1) + " km"; }

  /* ---------------------------------------------------------- the plan */

  // Every place the rider has to get off: each change, then the last stop.
  function targetsOf(p) {
    var rides = p.legs.filter(function (l) { return l.type === "transit"; });
    return rides.map(function (leg, i) {
      var next = rides[i + 1];
      // Everything the rider covers until getting off here: the walk or
      // ride to the first stop and every ride up to this stop.
      var upto = p.legs.slice(0, p.legs.indexOf(leg) + 1), path = [];
      upto.forEach(function (l) { path = path.concat(l.geometry || []); });
      return {
        name: (leg.to && leg.to.name) || "your stop",
        at: [leg.to.lat, leg.to.lon],
        route: routeOf(path),
        final: !next,
        nameMl: (leg.to && leg.to.name_ml) || null,     // for the Malayalam voice
        nextLabel: next ? next.label : null,
        vehicle: leg.label
      };
    });
  }

  // A path with the distance left to its end from every point.
  function routeOf(path) {
    var left = new Array(path.length);
    left[path.length - 1] = 0;
    for (var k = path.length - 2; k >= 0; k--) left[k] = left[k + 1] + haversine(path[k], path[k + 1]);
    return { path: path, left: left };
  }

  // Off the route by more than this (the bus took another road), the
  // straight-line distance is used instead.
  var OFF_ROUTE_M = 500;

  // How far is left to the stop along the route, the way the trip's
  // "Total distance" counts it - not as the crow flies, which is shorter
  // on any road that bends. Straight line when off the route.
  function distanceLeft(here, t) {
    var straight = haversine(here, t.at), r = t.route;
    if (!r || r.path.length < 2) return straight;
    var best = null;
    for (var k = 0; k < r.path.length - 1; k++) {
      var a = r.path[k], b = r.path[k + 1];
      var kx = 111320 * Math.cos((a[0] + b[0]) / 2 * Math.PI / 180), ky = 111320;
      var bx = (b[1] - a[1]) * kx, by = (b[0] - a[0]) * ky;
      var px = (here[1] - a[1]) * kx, py = (here[0] - a[0]) * ky;
      var seg = bx * bx + by * by;
      var f = seg ? Math.max(0, Math.min(1, (px * bx + py * by) / seg)) : 0;
      var off = Math.hypot(px - bx * f, py - by * f);
      if (best === null || off < best.off) {
        best = { off: off, left: r.left[k + 1] + Math.sqrt(seg) * (1 - f) };
      }
    }
    return best.off > OFF_ROUTE_M ? straight : best.off + best.left;
  }

  function updateStartButton() {
    var btn = el("trip-start");
    if (!btn) return;
    if (running) {
      btn.textContent = "Stop trip alert";
      btn.classList.add("is-running");
      btn.setAttribute("aria-label", "Stop trip alert");
    } else {
      var t = (plan && targetsOf(plan)) || [];
      var stopName = t.length ? t[t.length - 1].name : "my stop";
      btn.textContent = "Wake me before " + stopName;
      btn.classList.remove("is-running");
      btn.setAttribute("aria-label", "Wake me before " + stopName);
    }
  }

  // Called whenever a plan (or another route option) is shown.
  function attach(p) {
    plan = p;
    var box = el("trip");
    if (!box) return;
    var t = targetsOf(p);
    if (!t.length) { box.hidden = true; return; }
    box.hidden = false;
    updateStartButton();
    showVoiceExample();
    if (running) {            // switched route options mid-trip: follow the new one
      targets = t;
      fired = {};
      update();
    }
  }

  /* ---------------------------------------------------- sound, buzz, note */

  function ensureAudio() {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!audio) audio = new Ctx();
    if (audio.state === "suspended") audio.resume();
    return audio;
  }

  // One "beep-beep-beep" - three short tones, like an alarm clock.
  function beep() {
    var ctx = ensureAudio();
    if (!ctx) return;
    var vol = Math.max(0.01, currentVolume);
    [0, 0.22, 0.44].forEach(function (offset) {
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "square";
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, ctx.currentTime + offset);
      g.gain.exponentialRampToValueAtTime(0.25 * vol, ctx.currentTime + offset + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + offset + 0.16);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + offset);
      o.stop(ctx.currentTime + offset + 0.18);
    });
  }

  // The alarm sound synthesized as a looping WAV file. Offers multiple tones:
  // Classic (piercing digital watch), Chime (soft harmonic bell), Radar (sonar pulse),
  // Marimba (melodic ascending riff), Siren (urgent two-tone alert).
  function alarmTone(toneKey) {
    var key = toneKey || currentTone;
    if (toneUrls[key]) return toneUrls[key];
    var rate = 22050, secs = 1.4, n = Math.floor(rate * secs);
    var buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    function str(o, t) { for (var i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); }
    str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
    str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, "data"); v.setUint32(40, n * 2, true);
    for (var i = 0; i < n; i++) {
      var t = i / rate, x = 0;
      if (key === "chime") {
        var p1 = t, p2 = t - 0.55;
        if (p1 >= 0 && p1 < 0.6) {
          var env1 = Math.exp(-6 * p1);
          x += env1 * (0.5 * Math.sin(2 * Math.PI * 1046.5 * p1) + 0.35 * Math.sin(2 * Math.PI * 1318.5 * p1) + 0.15 * Math.sin(2 * Math.PI * 1568 * p1));
        }
        if (p2 >= 0 && p2 < 0.7) {
          var env2 = Math.exp(-5 * p2);
          x += env2 * (0.5 * Math.sin(2 * Math.PI * 1318.5 * p2) + 0.35 * Math.sin(2 * Math.PI * 1568 * p2) + 0.2 * Math.sin(2 * Math.PI * 2093 * p2));
        }
      } else if (key === "radar") {
        var sub = t % 0.65;
        if (sub < 0.35) {
          var freq = 700 + 900 * (sub / 0.35);
          var env = Math.sin(Math.PI * Math.min(1, sub / 0.35)) * Math.exp(-3 * sub);
          x = env * (0.8 * Math.sin(2 * Math.PI * freq * sub) + 0.2 * Math.sin(4 * Math.PI * freq * sub));
        }
      } else if (key === "marimba") {
        var noteIdx = Math.floor(t / 0.16);
        var subT = t - noteIdx * 0.16;
        var freqs = [523.25, 659.25, 783.99, 1046.5];
        if (noteIdx < 4 && subT < 0.25) {
          var f = freqs[noteIdx];
          var env = Math.exp(-12 * subT);
          x = env * (0.75 * Math.sin(2 * Math.PI * f * subT) + 0.25 * Math.sin(2 * Math.PI * 2 * f * subT));
        }
      } else if (key === "siren") {
        var step = Math.floor(t / 0.14);
        var sf = (step % 2 === 0) ? 960 : 1240;
        var su = t - step * 0.14;
        if (step < 7 && su < 0.13) {
          var env = Math.min(1, su / 0.008, (0.13 - su) / 0.008);
          x = env * (0.7 * Math.sin(2 * Math.PI * sf * t) + 0.3 * Math.sin(6 * Math.PI * sf * t));
        }
      } else if (key === "cosmic") {
        var step = Math.floor(t / 0.18);
        var freqs = [440, 554.37, 659.25, 880];
        if (step < 4) {
          var sub = t - step * 0.18;
          var f = freqs[step];
          var env = Math.sin(Math.PI * Math.min(1.0, sub / 0.15)) * Math.exp(-2.5 * sub);
          x = env * (0.6 * Math.sin(2 * Math.PI * f * sub) + 0.3 * Math.sin(4 * Math.PI * f * sub) + 0.1 * Math.sin(6 * Math.PI * f * sub));
        }
      } else if (key === "harp") {
        var step = Math.floor(t / 0.12);
        var notes = [587.33, 739.99, 880.0, 1108.73, 1479.98];
        if (step < 5) {
          var sub = t - step * 0.12;
          var f = notes[step];
          var env = Math.exp(-9.0 * sub);
          x = env * (0.7 * Math.sin(2 * Math.PI * f * sub) + 0.3 * Math.sin(4 * Math.PI * f * sub));
        }
      } else if (key === "birds") {
        if (t < 0.3) {
          var f = 2200 + 1200 * Math.sin(2 * Math.PI * 7 * t);
          var env = Math.sin(Math.PI * (t / 0.3)) * Math.exp(-2 * t);
          x = env * Math.sin(2 * Math.PI * f * t);
        } else if (t > 0.45 && t < 0.8) {
          var t2 = t - 0.45;
          var f = 2600 + 1000 * Math.sin(2 * Math.PI * 9 * t2);
          var env = Math.sin(Math.PI * (t2 / 0.35)) * Math.exp(-2 * t2);
          x = env * Math.sin(2 * Math.PI * f * t2);
        }
      } else {
        var k = Math.floor(t / 0.16), u = t - k * 0.16;
        if (k < 4 && u < 0.11) {
          var env = Math.min(1, u / 0.005, (0.11 - u) / 0.005);
          var w = 2 * Math.PI * 1760 * t;
          x = env * (0.62 * Math.sin(w) + 0.25 * Math.sin(3 * w) + 0.13 * Math.sin(5 * w));
        }
      }
      v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x)) * 32000, true);
    }
    toneUrls[key] = URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
    return toneUrls[key];
  }

  function ensureSiren() {
    if (!siren && typeof Audio !== "undefined") {
      siren = new Audio(alarmTone(currentTone));
      siren.loop = true;
      siren.preload = "auto";
      siren.volume = currentVolume;
    }
    return siren;
  }

  // Phones only let a page play sound it was allowed to start from a tap:
  // play the alarm muted for a moment in the tap that starts the trip.
  function unlockSiren() {
    var s = ensureSiren();
    if (!s) return;
    s.muted = true;
    var done = function () { if (!ringing) { s.pause(); s.currentTime = 0; } s.muted = false; };
    var p = s.play();
    if (p && p.then) p.then(done, function () { s.muted = false; }); else done();
  }

  var ringing = false;
  var buzzTimer = null;     // the alarm's repeating vibration

  // Ring until silence(): the looping sound (or, if the browser will not
  // play it, the page's own beeps), with vibration, over and over. With
  // `say` (a function giving the words) and the voice on, the stop's name is
  // said first and again every SAY_EVERY_MS, the alarm sound pausing for it.
  function ring(say) {
    stopRinging();
    ringing = true;
    // The rider's buzz (vibes.js: Gentle pulse, Heartbeat, Urgent rumble,
    // SOS), repeated for as long as the alarm rings.
    var buzz = (window.VIBES && VIBES.pattern()) || [700, 300, 700, 300, 1200];
    var buzzEvery = Math.max(1400, buzz.reduce(function (a, b) { return a + b; }, 0) + 600);
    vibrate(buzz);
    if (bgAudio) { try { bgAudio.pause(); } catch (e) {} }
    var s = silent() ? null : ensureSiren();
    var beepToo = !s;
    function play() {
      if (!ringing || talking) return;
      if (s) {
        s.muted = false;
        s.volume = currentVolume;
        s.currentTime = 0;
        var p = s.play();
        if (p && p.catch) p.catch(function () { beepToo = true; if (ringing && !silent()) beep(); });
      } else if (!silent()) {
        beep();
      }
    }
    function announce() {
      if (!say || !voiceOn()) return false;
      if (s) s.pause();
      talking = true;
      say(play);
      return true;
    }
    if (!announce()) play();
    beepTimer = setInterval(function () {
      if (beepToo && !silent() && !talking) beep();
    }, 1400);
    buzzTimer = setInterval(function () { vibrate(buzz); }, buzzEvery);
    if (say) voiceTimer = setInterval(announce, SAY_EVERY_MS);
  }

  function stopRinging() {
    ringing = false;
    clearInterval(beepTimer);
    beepTimer = null;
    clearInterval(buzzTimer);
    buzzTimer = null;
    clearInterval(voiceTimer);
    voiceTimer = null;
    stopSpeaking();
    if (siren) { siren.pause(); siren.currentTime = 0; }
    if (navigator.vibrate) navigator.vibrate(0);
    // ring() paused the silent background sound that keeps the page awake;
    // start it again, or the phone may stop Idavazhi before the next stop.
    if (running && bgAudio) { try { bgAudio.play().catch(function () {}); } catch (e) {} }
  }

  function vibrate(pattern) {
    if (navigator.vibrate) navigator.vibrate(pattern);
  }

  /* ------------------------------------------------------------ voice */

  // The alarm speaks pure Malayalam (transitnav/voice.py; the server's copy
  // from GET /api/voice/lines replaces this one). The natural voice is
  // stitched on the server from saved pieces - fixed phrases and the stop's
  // name - so a new stop costs the voice service only its name.
  var ML = {
    prewake: "ശ്രദ്ധിക്കുക. {name} അടുത്തുവരുന്നു. രണ്ടു നിമിഷത്തിനുള്ളിൽ ഉണർത്തുമണി മുഴങ്ങും.",
    wake: "ഉണരൂ! നിങ്ങൾ ഇറങ്ങേണ്ട സ്ഥലം, {name}, അടുത്തെത്തി. ഇറങ്ങാൻ ഒരുങ്ങുക.",
    wake_change: "ഉണരൂ! {name} അടുത്തെത്തി. അവിടെ ഇറങ്ങി മാറിക്കയറണം.",
    soon: "രണ്ടു നിമിഷത്തിനുള്ളിൽ {name} എത്തും. ഇറങ്ങാൻ ഒരുങ്ങുക.",
    soon_change: "രണ്ടു നിമിഷത്തിനുള്ളിൽ {name} എത്തും. അവിടെ ഇറങ്ങി മാറിക്കയറണം.",
    arrive: "{name} എത്തി. ദയവായി ഇവിടെ ഇറങ്ങുക.",
    arrive_change: "{name} എത്തി. ഇവിടെ ഇറങ്ങി മാറിക്കയറുക.",
    leave: "വണ്ടി പിടിക്കാൻ പുറപ്പെടാൻ നേരമായി."
  };
  // Only for a phone with no Malayalam voice of its own, when the natural
  // clip could not be fetched: an English voice reading Malayalam letters
  // says nothing, and a warning in English beats silence.
  var EN = {
    prewake: "Your alarm for {name} rings in 2 minutes.",
    wake: "Wake up! {name} is coming up. Get ready to get off.",
    wake_change: "Wake up! {name} is coming up. You change there.",
    soon: "Arriving {name} in 2 minutes. Get ready to get off.",
    soon_change: "Arriving {name} in 2 minutes. Get off there to change.",
    arrive: "This is {name}. Please get off here.",
    arrive_change: "This is {name}. Get off here and change.",
    leave: "It's time to leave for your bus."
  };
  var clipsOn = false;        // the server has a natural voice (Sarvam or ElevenLabs)
  var clips = {};             // "kind|stop" -> the clip, kept in memory for the ride
  var clipAudio = null;       // plays the clips
  var clipVoice = "";         // the server's voice tag: a new voice means new clip addresses

  function canSpeak() {
    return "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";
  }

  // Voice is on unless the rider turned it off, or chose "Vibrate only".
  function voiceOn() {
    var box = el("trip-voice");
    return (clipsOn || canSpeak()) && !silent() && (!box || box.checked);
  }

  // The phone's own voice, used when there is no natural clip: any
  // Malayalam voice (a woman's if the phone names one). Failing that, for
  // the English lines, a gentle Indian English woman's voice, looked for by name:
  //   Edge: Neerja (natural, needs internet) - Windows: Heera
  //   iPhone, iPad and Mac: Isha, Veena - and any voice named "female".
  // Failing those, any Indian English voice that is not a known man's
  // (Android names its voices by language only), then any English woman's
  // voice, then any English voice.
  var WOMAN = /neerja|heera|isha|veena|kajal|aditi|sobhana|female|woman/i;
  var MAN = /\b(ravi|rishi|prabhat|hemant|midhun|male|man)\b/i;

  function pickVoice(lang) {
    if (!canSpeak()) return null;
    var list = window.speechSynthesis.getVoices() || [];
    var online = navigator.onLine !== false;
    function find(test) {
      for (var i = 0; i < list.length; i++) {
        var v = list[i];
        // A voice from the internet stops working when the signal does.
        if ((online || v.localService) && test(v)) return v;
      }
      return null;
    }
    if (lang === "ml") {
      function malayalam(v) { return /^ml/i.test(v.lang); }
      return find(function (v) { return malayalam(v) && WOMAN.test(v.name); }) ||
             find(function (v) { return malayalam(v) && !MAN.test(v.name); }) ||
             find(malayalam);
    }
    function indian(v) { return /^en[-_]IN$/i.test(v.lang); }
    function english(v) { return /^en/i.test(v.lang); }
    return find(function (v) { return indian(v) && /neerja/i.test(v.name); }) ||
           find(function (v) { return indian(v) && WOMAN.test(v.name); }) ||
           find(function (v) { return indian(v) && !MAN.test(v.name); }) ||
           find(function (v) { return english(v) && WOMAN.test(v.name) && !MAN.test(v.name); }) ||
           find(function (v) { return english(v) && /zira|samantha|karen|serena|moira|tessa|fiona|susan|hazel/i.test(v.name); }) ||
           find(indian) ||
           find(english);
  }

  // The language the phone's voice speaks: Malayalam if the phone has a
  // Malayalam voice, otherwise English.
  function phoneLang() {
    return pickVoice("ml") ? "ml" : "en";
  }

  // Say `text` in the phone's voice. `done` runs when it has been said,
  // failed, or taken too long (some phones never report the end).
  function speak(text, lang, done) {
    var finished = false;
    function end() {
      if (finished) return;
      finished = true;
      talking = false;
      if (done) done();
    }
    if (!canSpeak() || !text) { end(); return; }
    try {
      var synth = window.speechSynthesis;
      synth.cancel();
      var u = new SpeechSynthesisUtterance(text);
      var v = pickVoice(lang);
      if (v) { u.voice = v; u.lang = v.lang; } else { u.lang = lang === "ml" ? "ml-IN" : "en-IN"; }
      // A little slower and a touch higher: calm and clear.
      u.rate = 0.9;
      u.pitch = 1.08;
      u.volume = currentVolume;
      u.onend = end;
      u.onerror = end;
      talking = true;
      synth.speak(u);
      if (synth.paused) synth.resume();
      setTimeout(end, 3000 + text.length * 120);
    } catch (e) { end(); }
  }

  // Play a natural clip; `cb(ok)` once it has played, or false if it could not.
  function playClip(url, cb) {
    var finished = false;
    function end(ok) {
      if (finished) return;
      finished = true;
      talking = false;
      cb(ok);
    }
    try {
      if (!clipAudio) clipAudio = new Audio();
      clipAudio.pause();
      clipAudio.muted = false;
      clipAudio.src = url;
      clipAudio.volume = currentVolume;
      clipAudio.onended = function () { end(true); };
      clipAudio.onerror = function () { end(false); };
      talking = true;
      var p = clipAudio.play();
      if (p && p.catch) p.catch(function () { end(false); });
      setTimeout(function () { end(true); }, 15000);    // every line is far shorter
    } catch (e) { end(false); }
  }

  function stopSpeaking() {
    talking = false;
    if (clipAudio) { try { clipAudio.pause(); } catch (e) { /* not playing */ } }
    if (canSpeak()) { try { window.speechSynthesis.cancel(); } catch (e) { /* nothing to stop */ } }
  }

  // Phones only play sound or speak for a page that was tapped: play
  // silence, quietly, in the tap that starts the trip, so the
  // announcements are allowed later.
  function unlockVoice() {
    try {
      if (!clipAudio) clipAudio = new Audio();
      clipAudio.src = getSilentWavUrl();
      clipAudio.muted = true;
      var p = clipAudio.play();
      var done = function () { if (!talking) clipAudio.pause(); clipAudio.muted = false; };
      if (p && p.then) p.then(done, function () { clipAudio.muted = false; }); else done();
    } catch (e) { /* no audio here */ }
    if (!canSpeak()) return;
    try {
      window.speechSynthesis.getVoices();
      var u = new SpeechSynthesisUtterance(" ");
      u.volume = 0;
      window.speechSynthesis.speak(u);
    } catch (e) { /* no voice on this phone */ }
  }

  // A change of train or bus has its own lines ("... and change").
  function kindFor(base, t) {
    return t && !t.final && ML[base + "_change"] ? base + "_change" : base;
  }

  // The words of one line about stop `t`: Malayalam, with the stop's
  // Malayalam name when the map has one ("en" only for the fallback above).
  function lineFor(kind, t, lang) {
    var text = (lang === "en" ? EN : ML)[kind] || "";
    var name = t ? ((lang !== "en" && t.nameMl) || t.name) : "";
    return text.replace("{name}", name);
  }

  function clipKey(kind, t) {
    return kind + "|" + (t ? t.name + "|" + (t.nameMl || "") : "");
  }

  // Fetch one natural clip into memory, so it plays later with no signal.
  // `cb(url)` gets it, or null when there is none.
  function loadClip(kind, t, cb) {
    var key = clipKey(kind, t);
    if (clips[key]) { if (cb) cb(clips[key]); return; }
    if (!clipsOn || !window.fetch) { if (cb) cb(null); return; }
    var q = "kind=" + kind + (clipVoice ? "&v=" + clipVoice : "");
    if (t) q += "&name=" + encodeURIComponent(t.name) + (t.nameMl ? "&ml=" + encodeURIComponent(t.nameMl) : "");
    fetch("/api/voice?" + q).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.blob();
    }).then(function (blob) {
      clips[key] = URL.createObjectURL(blob);
      if (cb) cb(clips[key]);
    }).catch(function () { if (cb) cb(null); });
  }

  // Every line of this trip, fetched when the alarm is switched on.
  function loadTripClips() {
    if (!clipsOn) return;
    targets.forEach(function (t) {
      ["prewake", "wake", "soon", "arrive"].forEach(function (base) { loadClip(kindFor(base, t), t); });
    });
  }

  // Say one line: the natural clip when there is one, otherwise the phone's
  // own voice with the same words. `done` runs after either.
  function say(kind, t, done) {
    function phone() { var lang = phoneLang(); speak(lineFor(kind, t, lang), lang, done); }
    var url = clips[clipKey(kind, t)];
    if (!url) { phone(); return; }
    playClip(url, function (ok) { if (ok) { if (done) done(); } else phone(); });
  }

  // say(), ready to hand to an alarm.
  function sayer(kind, t) {
    return function (done) { say(kind, t, done); };
  }

  // "Hear it": the line the rider hears two minutes before their stop.
  function testVoice() {
    var all = (plan && targetsOf(plan)) || [];
    var t = all.length ? all[all.length - 1] : { name: "Vyttila", final: true };
    var btn = el("trip-voice-test");
    stopPreview();
    stopSpeaking();
    if (btn) { btn.disabled = true; btn.textContent = "…"; }
    loadClip("soon", t, function () {
      if (btn) { btn.disabled = false; btn.textContent = "Hear it"; }
      say("soon", t);
    });
  }

  // The example under "Say the stop name": this trip's last stop.
  function showVoiceExample() {
    var box = el("trip-voice-example");
    if (!box) return;
    var all = (plan && targetsOf(plan)) || [];
    var t = all.length ? all[all.length - 1] : { name: "Vyttila", nameMl: "വൈറ്റില", final: true };
    box.textContent = "“" + lineFor("soon", t, "ml").split(". ")[0] + "”";
  }

  // The words (and whether natural clips are on) from the server.
  function loadLines() {
    if (!window.fetch) return;
    fetch("/api/voice/lines").then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
      if (!data || !data.lines || !data.lines.soon) return;
      ML = data.lines;
      clipsOn = !!data.enabled;
      clipVoice = data.voice || "";
      var row = el("trip-voice-row");
      if (row && clipsOn) row.hidden = false;
      showVoiceExample();
      if (running) loadTripClips();
    }).catch(function () { /* offline: the words above stand */ });
  }

  function notify(title, body) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    var opts = { body: body, tag: "vazhi-trip", renotify: true, requireInteraction: true,
                 vibrate: [500, 200, 500, 200, 900], silent: false };
    // Android shows notifications only through a service worker.
    if (swReg && swReg.showNotification) swReg.showNotification(title, opts);
    else { try { new Notification(title, opts); } catch (e) { /* not allowed here */ } }
  }

  function silent() { return !!(el("trip-silent") && el("trip-silent").checked); }

  // The full-screen alarm. Loud ones ring until "I'm awake" (only buzz,
  // with "Vibrate only" on - for a quiet bus or a sleeping baby); the rest
  // chime once.
  // Play one cycle of the selected alarm tone for arrival / gentle alerts
  function playArrivalSound() {
    var s = ensureSiren();
    if (!s) {
      if (!silent()) beep();
      return;
    }
    s.pause();
    s.currentTime = 0;
    s.src = alarmTone(currentTone);
    s.volume = currentVolume;
    s.muted = false;
    var p = s.play();
    if (p && p.catch) p.catch(function () { if (!silent()) beep(); });
    setTimeout(function () {
      if (!ringing && s) {
        s.pause();
        s.currentTime = 0;
      }
    }, 1400);
  }

  // The full-screen alarm. Loud ones ring until "I'm awake" (only buzz,
  // with "Vibrate only" on - for a quiet bus or a sleeping baby); the rest
  // chime once with the selected tone.
  // `say` (optional) gives the words to speak: after the chime for a gentle
  // alarm, before and between the rings for a loud one.
  function alarm(title, body, loud, say) {
    el("alarm-title").textContent = title;
    el("alarm-body").textContent = body;
    el("alarm").hidden = false;
    el("alarm").classList.toggle("is-loud", !!loud);
    notify(title, body);
    if (loud) { ring(say); return; }
    stopRinging();
    vibrate([300, 150, 300]);
    if (silent()) return;
    playArrivalSound();
    if (say && voiceOn()) {
      setTimeout(function () { if (!ringing) say(); }, 1500);
    }
  }

  function silence() {
    stopRinging();
    awake = true;
    el("alarm").hidden = true;
    if (targets.length && fired[(targets.length - 1) + ":arrive"]) {
      var demo = simulating, where = targets[targets.length - 1].name;
      stop("You have arrived. Trip alerts are off.");
      // The trip is over: about.js asks how it went (and offers a bus ticket).
      if (!demo) {
        document.dispatchEvent(new CustomEvent("vazhi:arrived", { detail: { demo: false, stop: where } }));
      }
    }
  }

  /* ----------------------------------------------------- tracking */

  // "auto" (Default): 2 km. Otherwise exactly the distance the rider picked.
  /* ------------------------------------------- tone & volume controls */

  function previewTone(toneKey) {
    var s = ensureSiren();
    if (!s) return;
    stopRinging();
    if (previewTimer) {
      clearTimeout(previewTimer);
      previewTimer = null;
    }
    s.pause();
    s.currentTime = 0;
    s.src = alarmTone(toneKey);
    s.muted = false;
    s.volume = currentVolume;
    var p = s.play();
    if (p && p.catch) p.catch(function () {});
    previewTimer = setTimeout(function () {
      previewTimer = null;
      s.pause();
      s.currentTime = 0;
    }, 2400);
  }

  function stopPreview() {
    if (previewTimer) {
      clearTimeout(previewTimer);
      previewTimer = null;
    }
    if (siren) {
      siren.pause();
      siren.currentTime = 0;
    }
  }

  function setTone(toneKey) {
    if (!TONES[toneKey]) toneKey = "classic";
    currentTone = toneKey;
    pendingTone = toneKey;
    try { window.localStorage.setItem(TONE_SETTING, toneKey); } catch (e) {}
    if (siren) {
      siren.src = alarmTone(toneKey);
      siren.volume = currentVolume;
    }
    updateToneLabels();
  }

  function updateToneLabels() {
    var name = (TONES[currentTone] && TONES[currentTone].name) || "Classic Digital";
    var dropLabel = el("sound-drop-label");
    if (dropLabel) dropLabel.textContent = name;
  }

  function setVolume(val) {
    currentVolume = Math.max(0.05, Math.min(1, Number(val)));
    try { window.localStorage.setItem(VOL_SETTING, String(currentVolume)); } catch (e) {}
    if (siren) siren.volume = currentVolume;
    var slider = el("trip-volume");
    if (slider) slider.value = String(Math.round(currentVolume * 100));
    var valEl = el("trip-volume-val");
    if (valEl) valEl.textContent = Math.round(currentVolume * 100) + "%";
  }

  function loadSoundSettings() {
    try {
      var savedTone = window.localStorage.getItem(TONE_SETTING);
      if (savedTone && TONES[savedTone]) currentTone = pendingTone = savedTone;
      var savedVol = window.localStorage.getItem(VOL_SETTING);
      if (savedVol != null && !isNaN(Number(savedVol))) {
        currentVolume = Math.max(0.05, Math.min(1, Number(savedVol)));
      }
    } catch (e) {}
  }

  function renderDropdownItems() {
    var list = el("sound-drop-list");
    if (!list) return;
    list.innerHTML = "";
    Object.keys(TONES).forEach(function (k) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "sound__drop-item";
      item.setAttribute("role", "radio");
      item.setAttribute("data-tone", k);
      item.setAttribute("aria-checked", String(k === pendingTone));
      
      var info = document.createElement("div");
      info.className = "sound__item-info";
      var name = document.createElement("span");
      name.className = "sound__item-name";
      name.textContent = TONES[k].name;
      var desc = document.createElement("span");
      desc.className = "sound__item-desc";
      desc.textContent = TONES[k].desc;
      info.appendChild(name);
      info.appendChild(desc);

      var icon = document.createElement("span");
      icon.className = "sound__item-icon";
      icon.innerHTML = "&#9658;"; // play/checked icon

      item.appendChild(info);
      item.appendChild(icon);

      // Clicking any ringtone tests it immediately!
      item.addEventListener("click", function () {
        pendingTone = k;
        Array.prototype.forEach.call(list.children, function (c) {
          c.setAttribute("aria-checked", String(c.getAttribute("data-tone") === k));
        });
        previewTone(k);
      });

      list.appendChild(item);
    });
  }

  function closeDropdown() {
    stopPreview();
    var drop = el("sound-dropdown");
    var btn = el("sound-drop-btn");
    if (drop) {
      drop.hidden = true;
      drop.style.display = "none";
    }
    if (btn) btn.setAttribute("aria-expanded", "false");
    pendingTone = currentTone; // reset unapplied preview
  }

  function openDropdown() {
    var drop = el("sound-dropdown");
    var btn = el("sound-drop-btn");
    if (!drop || !btn) return;
    pendingTone = currentTone;
    renderDropdownItems();
    drop.hidden = false;
    drop.style.display = "flex";
    btn.setAttribute("aria-expanded", "true");
  }

  function buildSoundControls() {
    updateToneLabels();

    var dropBtn = el("sound-drop-btn");
    if (dropBtn) {
      dropBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        var drop = el("sound-dropdown");
        if (drop && !drop.hidden) closeDropdown();
        else openDropdown();
      });
    }

    function bindDismiss(buttonEl) {
      if (!buttonEl) return;
      function onDismiss(e) {
        if (e) {
          e.preventDefault();
          e.stopPropagation();
        }
        closeDropdown();
      }
      buttonEl.addEventListener("click", onDismiss);
      buttonEl.addEventListener("pointerdown", onDismiss);
    }
    bindDismiss(el("sound-close-btn"));
    bindDismiss(el("sound-cancel-btn"));

    var applyBtn = el("sound-apply-btn");
    if (applyBtn) {
      function onApply(e) {
        if (e) {
          e.preventDefault();
          e.stopPropagation();
        }
        stopPreview();
        setTone(pendingTone); // locks up the selected tone
        closeDropdown();      // dropdown disappears
      }
      applyBtn.addEventListener("click", onApply);
      applyBtn.addEventListener("pointerdown", onApply);
    }

    // Close when tapping anywhere outside the dropdown
    function handleOutside(e) {
      var drop = el("sound-dropdown");
      var btn = el("sound-drop-btn");
      if (!drop || drop.hidden || drop.style.display === "none") return;
      if (!drop.contains(e.target) && (!btn || !btn.contains(e.target))) {
        closeDropdown();
      }
    }
    document.addEventListener("pointerdown", handleOutside, true);
    document.addEventListener("touchstart", handleOutside, { capture: true, passive: true });
    document.addEventListener("click", handleOutside, true);
    document.addEventListener("keydown", function (e) {
      if ((e.key === "Escape" || e.key === "Esc") && !e.defaultPrevented) {
        var drop = el("sound-dropdown");
        if (drop && !drop.hidden) closeDropdown();
      }
    });

    var volSlider = el("trip-volume");
    if (volSlider) {
      volSlider.value = String(Math.round(currentVolume * 100));
      volSlider.addEventListener("input", function () {
        setVolume(Number(volSlider.value) / 100);
      });
      volSlider.addEventListener("change", function () {
        previewTone(currentTone);
      });
    }
    var valEl = el("trip-volume-val");
    if (valEl) valEl.textContent = Math.round(currentVolume * 100) + "%";

    var silentBox = el("trip-silent");
    var updateSilentUI = function () {
      var box = el("trip-sound-box");
      if (box) box.classList.toggle("is-disabled", silent());
    };
    if (silentBox) silentBox.addEventListener("change", updateSilentUI);
    updateSilentUI();
  }

  function wakeDistance() {
    var chosen = el("trip-distance") ? el("trip-distance").value : "auto";
    if (chosen !== "auto" && Number(chosen) > 0) return Number(chosen);
    return WAKE_M;
  }

  function loadSetting() {
    var box = el("trip-distance");
    if (!box) return;
    try {
      var saved = window.localStorage.getItem(SETTING);
      if (saved && box.querySelector('option[value="' + saved + '"]')) box.value = saved;
    } catch (e) { /* storage blocked: the default stays */ }
    box.addEventListener("change", function () {
      try { window.localStorage.setItem(SETTING, box.value); } catch (e) { /* not kept */ }
      update();                       // a running trip uses the new distance at once
    });
  }

  /* The alarm distance, in large type: the choice in big numbers, and one
   * button per distance under it (the <select> behind them keeps the value). */
  var RING = {
    auto: ["2", "km", "Default \u00b7 before your stop", "Default"],
    500: ["500", "m", "before your stop", "500 m"],
    1000: ["1", "km", "before your stop", "1 km"],
    3000: ["3", "km", "before your stop", "3 km"],
    5000: ["5", "km", "before your stop", "5 km"],
    10000: ["10", "km", "before your stop", "10 km"]
  };

  function buildRing() {
    var box = el("trip-distance"), chips = el("ring-chips");
    if (!box || !chips) return;
    Array.prototype.forEach.call(box.options, function (o) {
      var r = RING[o.value];
      if (!r) return;
      var b = document.createElement("button");
      b.type = "button";
      b.className = "ring__chip";
      b.setAttribute("role", "radio");
      b.setAttribute("data-value", o.value);
      b.textContent = r[3];
      b.addEventListener("click", function () {
        box.value = o.value;
        box.dispatchEvent(new Event("change"));
      });
      chips.appendChild(b);
    });
    box.addEventListener("change", showRing);
    showRing();
  }

  function showRing() {
    var v = el("trip-distance").value, r = RING[v] || RING.auto;
    el("ring-num").textContent = r[0];
    el("ring-unit").textContent = r[1];
    el("ring-sub").textContent = r[2];
    Array.prototype.forEach.call(el("ring-chips").children, function (b) {
      b.setAttribute("aria-checked", String(b.getAttribute("data-value") === v));
    });
  }

  function nextTarget() {
    for (var i = 0; i < targets.length; i++) {
      if (!fired[i + ":arrive"]) return i;
    }
    return -1;
  }

  function onFix(lat, lon, accuracy, when) {
    var here = [lat, lon];
    // For "Share where this train is" (livetrain.js), which decides itself
    // whether to send anything.
    document.dispatchEvent(new CustomEvent("vazhi:fix", {
      detail: { lat: lat, lon: lon, accuracy: accuracy, simulating: simulating } }));
    if (last) {
      var dt = (when - last.t) / 1000;
      if (dt > 0.5) {
        var v = haversine(last.at, here) / dt;
        speed = speed == null ? v : speed * 0.6 + v * 0.4;   // smoothed
      }
    }
    last = { at: here, t: when };
    showMe(here, accuracy);
    update();
  }

  function update() {
    if (!running || !last) return;
    var i = nextTarget();
    if (i < 0) return;
    var t = targets[i];
    var dist = distanceLeft(last.at, t);
    var secs = speed && speed > 1 ? dist / speed : null;
    var wakeAt = wakeDistance();

    updateBackgroundStatus(t, dist, secs);

    if (dist <= ARRIVE_M && !fired[i + ":arrive"]) {
      fired[i + ":arrive"] = fired[i + ":wake"] = fired[i + ":soon"] = fired[i + ":prewake"] = true;
      var here = sayer(kindFor("arrive", t), t);
      // Still asleep (the first alarm never answered): ring until tapped.
      if (t.final) alarm("This is your stop", "Get off at " + t.name + ".", !awake, here);
      else alarm("Change here", "Get off at " + t.name + " and change to " + t.nextLabel + ".", !awake, here);
      awake = false;   // the next ride may be slept through too
    } else if (dist <= wakeAt && !fired[i + ":wake"]) {
      fired[i + ":wake"] = fired[i + ":prewake"] = true;
      awake = false;   // until "I'm awake" is tapped for this alarm
      alarm(t.final ? "Wake up — your stop is coming" : "Wake up — you change soon",
            t.name + " is " + fmtDist(dist) + " away" +
            (secs ? ", about " + Math.max(1, Math.round(secs / 60)) + " min" : "") +
            ". Get ready to get off.", true, sayer(kindFor("wake", t), t));
    } else if (secs != null && (dist - ARRIVE_M) / speed <= SOON_S && !fired[i + ":soon"] &&
               !ringing && !talking && voiceOn()) {
      // About two minutes before the stop alarm: "Arriving Vyttila in 2
      // minutes", with a short buzz and no alarm screen. It waits while the
      // alarm rings or the phone is still speaking, so nothing is cut off.
      fired[i + ":soon"] = true;
      vibrate([200]);
      say(kindFor("soon", t), t);
    } else if (secs != null && dist > wakeAt && !fired[i + ":prewake"] && !ringing && !talking &&
               voiceOn() && (dist - wakeAt) / speed <= SOON_S) {
      // About two minutes before the wake-up alarm rings: a spoken heads-up,
      // with a short buzz and no alarm screen.
      fired[i + ":prewake"] = true;
      vibrate([200]);
      say("prewake", t);
    }
    var eta = secs ? " · about " + Math.max(1, Math.round(secs / 60)) + " min" : "";
    // For the sleep screen (sleep.js) and the crowding question (crowd.js).
    document.dispatchEvent(new CustomEvent("vazhi:progress", { detail: {
      name: t.name, final: t.final, dist: dist, secs: secs, speed: speed,
      simulating: simulating, at: last.at, plan: plan } }));
    el("trip-status").innerHTML = (simulating ? "<b>Demo ride</b> · " : "<b>Tracking</b> · ") +
      (t.final ? "your stop " : "change at ") + "<b>" + escape(t.name) + "</b>: " +
      fmtDist(dist) + eta;
  }

  function escape(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function getLocationLayer() {
    if (!window.userLocationLayer && typeof map !== "undefined" && map) {
      window.userLocationLayer = L.layerGroup().addTo(map);
    }
    return window.userLocationLayer;
  }

  function showMe(here, accuracy) {
    var layer = getLocationLayer();
    if (!layer) return;
    layer.clearLayers();
    if (typeof whereLayer !== "undefined" && whereLayer && whereLayer !== layer) {
      try { whereLayer.clearLayers(); } catch (e) {}
    }
    if (accuracy && accuracy > 15) {
      L.circle(here, { radius: accuracy, color: "#4da3ff", weight: 1, fillOpacity: 0.08,
                       interactive: false }).addTo(layer);
    }
    L.marker(here, { icon: L.divIcon({ className: "", html: '<div class="me-dot"></div>',
                                       iconSize: [14, 14], iconAnchor: [7, 7] }),
                     zIndexOffset: 2000, interactive: false }).addTo(layer);
  }

  /* ------------------------------------------------------ start / stop */

  function prepare() {
    ensureAudio();                      // must start inside the tap that began the trip
    unlockSiren();
    unlockVoice();
    loadClip("leave", null);          // for "Time to leave for your bus"
    if ("serviceWorker" in navigator && !swReg) {
      navigator.serviceWorker.register("sw.js").then(function (r) { swReg = r; })
        .catch(function () { /* notifications fall back to the page */ });
    }
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
    keepAwake();
  }

  function keepAwake() {
    if (!("wakeLock" in navigator) || wakeLock) return;
    navigator.wakeLock.request("screen").then(function (lock) {
      wakeLock = lock;
      lock.addEventListener("release", function () { wakeLock = null; });
    }).catch(function () { /* not allowed (battery saver) - tracking still works */ });
  }

  var bgAudio = null;
  var bgLockRelease = null;
  var silentWavUrl = null;

  function getSilentWavUrl() {
    if (silentWavUrl) return silentWavUrl;
    var rate = 8000, secs = 1.0, n = Math.floor(rate * secs);
    var buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    function str(o, t) { for (var i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); }
    str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
    str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, "data"); v.setUint32(40, n * 2, true);
    for (var i = 0; i < n; i++) {
      v.setInt16(44 + i * 2, 0, true);
    }
    silentWavUrl = URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
    return silentWavUrl;
  }

  var lastStatusUpdate = 0;

  function updateBackgroundStatus(t, dist, secs) {
    if (!running || !t) return;
    var distText = fmtDist(dist);
    var etaText = (secs && secs > 10) ? " · ~" + Math.max(1, Math.round(secs / 60)) + " min" : "";
    var title = distText + " to " + t.name;
    var subtitle = (t.final ? "Next stop: " : "Change at ") + t.name + etaText;

    if ("mediaSession" in navigator) {
      try {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: title,
          artist: subtitle,
          album: "Idavazhi • Live Trip Alerts",
          artwork: [
            { src: "icons/icon-192.png", sizes: "192x192", type: "image/png" },
            { src: "icons/icon-512.png", sizes: "512x512", type: "image/png" }
          ]
        });
      } catch (e) {}
    }

    var now = Date.now();
    if (now - lastStatusUpdate >= 2500 || lastStatusUpdate === 0) {
      lastStatusUpdate = now;
      if (swReg && swReg.showNotification && Notification.permission === "granted") {
        try {
          swReg.showNotification(title, {
            body: subtitle + " · Active in background",
            icon: "icons/icon-192.png",
            badge: "icons/favicon-32.png",
            tag: "vazhi-trip-status",
            silent: true,
            renotify: false
          });
        } catch (e) {}
      }
    }
  }

  function startBackgroundKeepAlive() {
    lastStatusUpdate = 0;
    try {
      if (!bgAudio) {
        bgAudio = new Audio(getSilentWavUrl());
        bgAudio.loop = true;
        bgAudio.volume = 0.05;
      }
      bgAudio.play().catch(function () {});
    } catch (e) {}

    var dest = (targets && targets.length) ? targets[targets.length - 1].name : "your stop";
    if ("mediaSession" in navigator) {
      try {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: "Idavazhi • Trip Alert Active",
          artist: "Wake me before " + dest,
          album: "Following your trip in background",
          artwork: [
            { src: "icons/icon-192.png", sizes: "192x192", type: "image/png" },
            { src: "icons/icon-512.png", sizes: "512x512", type: "image/png" }
          ]
        });
        navigator.mediaSession.setActionHandler("stop", function () {
          stop("Trip alerts are off.");
        });
        navigator.mediaSession.setActionHandler("pause", function () {
          stop("Trip alerts are off.");
        });
      } catch (e) {}
    }

    if (navigator.locks && navigator.locks.request) {
      try {
        navigator.locks.request("vazhi-tracking", function () {
          return new Promise(function (resolve) {
            bgLockRelease = resolve;
          });
        }).catch(function () {});
      } catch (e) {}
    }

    if (swReg && swReg.showNotification && Notification.permission === "granted") {
      try {
        swReg.showNotification("Idavazhi is following your ride", {
          body: "Alarm set for " + dest + ". Active in background.",
          icon: "icons/icon-192.png",
          badge: "icons/favicon-32.png",
          tag: "vazhi-trip-status",
          silent: true,
          renotify: false
        });
      } catch (e) {}
    }
  }

  function stopBackgroundKeepAlive() {
    lastStatusUpdate = 0;
    if (bgAudio) {
      try { bgAudio.pause(); } catch (e) {}
      bgAudio = null;
    }
    if ("mediaSession" in navigator) {
      try { navigator.mediaSession.metadata = null; } catch (e) {}
    }
    if (bgLockRelease) {
      try { bgLockRelease(); } catch (e) {}
      bgLockRelease = null;
    }
    if (swReg && swReg.getNotifications) {
      try {
        swReg.getNotifications({ tag: "vazhi-trip-status" }).then(function (notes) {
          notes.forEach(function (n) { n.close(); });
        }).catch(function () {});
      } catch (e) {}
    }
  }

  function begin() {
    var layer = getLocationLayer();
    if (layer) layer.clearLayers();
    if (typeof whereLayer !== "undefined" && whereLayer && whereLayer !== layer) {
      try { whereLayer.clearLayers(); } catch (e) {}
    }
    targets = targetsOf(plan);
    fired = {};
    awake = false;
    last = null;
    speed = null;
    running = true;
    updateStartButton();
    loadTripClips();          // the trip's spoken lines, while there is signal
    startBackgroundKeepAlive();
    el("trip-start").hidden = false;
    el("trip-sim").hidden = true;
    el("trip-stop").hidden = true;
    el("trip-status").hidden = false;
    el("trip-status").textContent = "Starting…";
    // After start() or simulate() has said which kind of ride this is.
    window.setTimeout(function () {
      if (running) document.dispatchEvent(new CustomEvent("vazhi:tripstart",
        { detail: { simulating: simulating, plan: plan } }));
    }, 0);
  }

  function start() {
    if (!plan || running) return;
    if (!("geolocation" in navigator)) {
      el("trip-status").hidden = false;
      el("trip-status").textContent = "This browser cannot share your location, so trip alerts cannot run.";
      return;
    }
    prepare();
    begin();
    el("trip-status").textContent = "Waiting for your location…";
    watchId = navigator.geolocation.watchPosition(function (pos) {
      onFix(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy, pos.timestamp || Date.now());
    }, function (err) {
      stop(err && err.code === 1
        ? "Location is blocked, so trip alerts cannot follow you. Allow location for this site and try again."
        : "Your location could not be found. Trip alerts are off.");
    }, { enableHighAccuracy: true, maximumAge: 5000, timeout: 30000 });
  }

  // Demo mode: a pretend rider moves along the planned route.
  function simulate() {
    if (!plan || running) return;
    prepare();
    begin();
    simulating = true;
    var path = [];
    plan.legs.forEach(function (l) { path = path.concat(l.geometry || []); });
    if (path.length < 2) { stop("This route has no path to follow."); return; }
    var total = 0, seg = [];
    for (var k = 1; k < path.length; k++) {
      var d = haversine(path[k - 1], path[k]);
      seg.push(d);
      total += d;
    }
    var tick = 100, step = total / (SIM_SECONDS * 1000 / tick), idx = 0, into = 0;
    // The pretend clock runs as much faster as the ride is compressed, so the
    // speed worked out from it matches the real trip.
    var scale = Math.max(1, (plan.summary.duration_s || SIM_SECONDS) / SIM_SECONDS);
    var clock = Date.now();
    simTimer = setInterval(function () {
      // On a real ride the lines are minutes apart; squeezed into 10 seconds
      // they would cut each other off. The pretend bus waits while a line is
      // being said, and while an alarm rings until "I'm awake".
      if (talking || ringing) return;
      // A 10-second ride covers hundreds of metres a tick, which could jump
      // straight past the alarm distance. Near each stop it slows down, so
      // the wake-up alarm always rings before "this is your stop".
      var move = step, t = targets[nextTarget()];
      if (t && last) {
        var wake = wakeDistance(), cap = Math.max(50, (wake - ARRIVE_M) / 3);
        if (distanceLeft(last.at, t) < wake + 2 * cap) move = Math.min(move, cap);
      }
      var moved = move;
      while (move > 0 && idx < seg.length) {
        var left = seg[idx] - into;
        if (move < left) { into += move; move = 0; } else { move -= left; idx += 1; into = 0; }
      }
      clock += tick * scale * (moved / step);   // the pretend speed stays the trip's
      if (idx >= seg.length) {
        onFix(path[path.length - 1][0], path[path.length - 1][1], 10, clock);
        clearInterval(simTimer);
        simTimer = null;
        return;
      }
      var a = path[idx], b = path[idx + 1], f = seg[idx] ? into / seg[idx] : 0;
      onFix(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, 10, clock);
    }, tick);
  }

  function stop(message) {
    running = false;
    document.dispatchEvent(new CustomEvent("vazhi:tripend"));
    simulating = false;
    if (watchId != null) navigator.geolocation.clearWatch(watchId);
    watchId = null;
    clearInterval(simTimer);
    simTimer = null;
    stopRinging();
    stopBackgroundKeepAlive();
    if (wakeLock) { wakeLock.release(); wakeLock = null; }
    var layer = getLocationLayer();
    if (layer) layer.clearLayers();
    if (typeof whereLayer !== "undefined" && whereLayer && whereLayer !== layer) {
      try { whereLayer.clearLayers(); } catch (e) {}
    }
    updateStartButton();
    el("trip-start").hidden = false;
    el("trip-sim").hidden = false;
    el("trip-stop").hidden = true;
    el("trip-status").hidden = !message;
    el("trip-status").textContent = message || "";
  }

  function init() {
    el("trip-start").addEventListener("click", function () {
      if (running) {
        stop("Trip alerts are off.");
      } else {
        start();
      }
    });
    el("trip-sim").addEventListener("click", simulate);
    el("trip-stop").addEventListener("click", function () { stop("Trip alerts are off."); });
    el("alarm-ok").addEventListener("click", silence);
    loadSetting();
    buildRing();
    loadSoundSettings();
    buildSoundControls();
    try {                                   // remember "Silent" on this device too
      el("trip-silent").checked = window.localStorage.getItem("vazhi.silent") === "1";
    } catch (e) { /* default: with sound */ }
    el("trip-silent").addEventListener("change", function () {
      try { window.localStorage.setItem("vazhi.silent", el("trip-silent").checked ? "1" : "0"); } catch (e) { /* not kept */ }
    });
    // "Say the stop name": on unless the rider turned it off. Hidden where
    // nothing can speak: no speech engine and (until the server says
    // otherwise) no natural clips.
    var voiceBox = el("trip-voice");
    if (voiceBox) {
      el("trip-voice-row").hidden = !canSpeak();
      try {
        voiceBox.checked = window.localStorage.getItem(VOICE_SETTING) !== "0";
        window.localStorage.removeItem("vazhi.voiceLang");   // the old language choice
      } catch (e) { /* default: on */ }
      voiceBox.addEventListener("change", function () {
        try { window.localStorage.setItem(VOICE_SETTING, voiceBox.checked ? "1" : "0"); } catch (e) { /* not kept */ }
        if (!voiceBox.checked) stopSpeaking();
      });
      el("trip-voice-test").addEventListener("click", testVoice);
      if (canSpeak()) window.speechSynthesis.getVoices();   // some browsers load voices on first ask
      showVoiceExample();
      loadLines();
    }
    // Phones drop the screen lock when the page is hidden; take it back.
    // Also clean up any duplicate or stale markers when reopening the page.
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") {
        if (running) {
          keepAwake();
          if (last) showMe(last.at, null);
        }
      }
    });
  }

  return { init: init, attach: attach, stop: stop, isRunning: function () { return running; },
           // For other reminders (time to leave for the bus): prime() inside
           // the tap that sets the reminder, ring() when it is due.
           prime: prepare,
           ring: function (title, body) {
             alarm(title, body, true, sayer("leave", null));
           } };
})();
