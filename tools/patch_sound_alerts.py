import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
alerts_path = os.path.join(ROOT, "web", "alerts.js")

with open(alerts_path, "r", encoding="utf-8") as f:
    text = f.read()

# 1. Add variable declarations
target_vars = """  var meLayer = null, audio = null, beepTimer = null, wakeLock = null, swReg = null;
  var siren = null;        // the looping alarm sound (an <audio> element)
  var awake = false;       // the rider tapped "I'm awake" during this ride"""

new_vars = """  var meLayer = null, audio = null, beepTimer = null, wakeLock = null, swReg = null;
  var siren = null;        // the looping alarm sound (an <audio> element)
  var awake = false;       // the rider tapped "I'm awake" during this ride
  var TONES = {
    classic: { name: "Classic", desc: "Digital clock beeps" },
    chime:   { name: "Chime",   desc: "Soft harmonic bell" },
    radar:   { name: "Radar",   desc: "Sonar pulse" },
    marimba: { name: "Marimba", desc: "Melodic chime" },
    siren:   { name: "Siren",   desc: "Urgent two-tone alert" }
  };
  var TONE_SETTING = "vazhi.alarmTone";
  var VOL_SETTING = "vazhi.alarmVolume";
  var currentTone = "classic";
  var currentVolume = 1.0;
  var toneUrls = {};
  var previewTimer = null;"""

# 2. Replace beep() and alarmTone() and ensureSiren()
target_sound = """  // One "beep-beep-beep" - three short tones, like an alarm clock.
  function beep() {
    var ctx = ensureAudio();
    if (!ctx) return;
    [0, 0.22, 0.44].forEach(function (offset) {
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "square";
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, ctx.currentTime + offset);
      g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + offset + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + offset + 0.16);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + offset);
      o.stop(ctx.currentTime + offset + 0.18);
    });
  }

  // The alarm sound: "beep-beep-beep-beep", a pause, over and over, like an
  // alarm clock. Made here as a WAV file and played by an <audio> element
  // on loop, which keeps sounding while the phone is busy or the page is in
  // the background, where beeps timed by the page get delayed or dropped.
  function alarmTone() {
    var rate = 22050, secs = 1.4, n = Math.floor(rate * secs);
    var buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    function str(o, t) { for (var i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); }
    str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
    str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, "data"); v.setUint32(40, n * 2, true);
    for (var i = 0; i < n; i++) {
      var t = i / rate, k = Math.floor(t / 0.16), u = t - k * 0.16, x = 0;
      if (k < 4 && u < 0.11) {
        var env = Math.min(1, u / 0.005, (0.11 - u) / 0.005);
        var w = 2 * Math.PI * 1760 * t;      // high and piercing, with harmonics
        x = env * (0.62 * Math.sin(w) + 0.25 * Math.sin(3 * w) + 0.13 * Math.sin(5 * w));
      }
      v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x)) * 32000, true);
    }
    return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
  }

  function ensureSiren() {
    if (!siren && typeof Audio !== "undefined") {
      siren = new Audio(alarmTone());
      siren.loop = true;
      siren.preload = "auto";
    }
    return siren;
  }"""

new_sound = """  // One "beep-beep-beep" - three short tones, like an alarm clock.
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
  }"""

# 3. Add sound control functions before loadSetting()
target_before_load = """  function wakeDistance() {"""

new_sound_fns = """  /* ------------------------------------------- tone & volume controls */

  function togglePreview() {
    var s = ensureSiren();
    var btn = el("tone-preview");
    if (previewTimer) {
      clearTimeout(previewTimer);
      previewTimer = null;
      if (s) { s.pause(); s.currentTime = 0; }
      if (btn) btn.classList.remove("is-playing");
      return;
    }
    if (!s) return;
    stopRinging();
    s.muted = false;
    s.volume = currentVolume;
    s.currentTime = 0;
    if (btn) btn.classList.add("is-playing");
    var p = s.play();
    if (p && p.catch) p.catch(function () {});
    previewTimer = setTimeout(function () {
      previewTimer = null;
      if (s) { s.pause(); s.currentTime = 0; }
      if (btn) btn.classList.remove("is-playing");
    }, 2800);
  }

  function setTone(toneKey) {
    if (!TONES[toneKey]) toneKey = "classic";
    currentTone = toneKey;
    try { window.localStorage.setItem(TONE_SETTING, toneKey); } catch (e) {}
    if (siren) {
      var playing = !siren.paused;
      siren.src = alarmTone(toneKey);
      siren.volume = currentVolume;
      if (playing) siren.play().catch(function () {});
    }
    var chips = el("tone-chips");
    if (chips) {
      Array.prototype.forEach.call(chips.children, function (c) {
        c.setAttribute("aria-checked", String(c.getAttribute("data-tone") === toneKey));
      });
    }
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
      if (savedTone && TONES[savedTone]) currentTone = savedTone;
      var savedVol = window.localStorage.getItem(VOL_SETTING);
      if (savedVol != null && !isNaN(Number(savedVol))) {
        currentVolume = Math.max(0.05, Math.min(1, Number(savedVol)));
      }
    } catch (e) {}
  }

  function buildSoundControls() {
    var chips = el("tone-chips");
    if (chips && !chips.children.length) {
      Object.keys(TONES).forEach(function (k) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "tone__chip";
        b.setAttribute("role", "radio");
        b.setAttribute("data-tone", k);
        b.setAttribute("aria-checked", String(k === currentTone));
        b.textContent = TONES[k].name;
        b.title = TONES[k].desc;
        b.addEventListener("click", function () {
          setTone(k);
          togglePreview();
        });
        chips.appendChild(b);
      });
    }
    var volSlider = el("trip-volume");
    if (volSlider) {
      volSlider.value = String(Math.round(currentVolume * 100));
      volSlider.addEventListener("input", function () {
        setVolume(Number(volSlider.value) / 100);
      });
      volSlider.addEventListener("change", function () {
        if (!previewTimer) togglePreview();
      });
    }
    var valEl = el("trip-volume-val");
    if (valEl) valEl.textContent = Math.round(currentVolume * 100) + "%";

    var previewBtn = el("tone-preview");
    if (previewBtn) previewBtn.addEventListener("click", togglePreview);

    var silentBox = el("trip-silent");
    var updateSilentUI = function () {
      var box = el("trip-sound-box");
      if (box) box.classList.toggle("is-disabled", silent());
    };
    if (silentBox) silentBox.addEventListener("change", updateSilentUI);
    updateSilentUI();
  }

  function wakeDistance() {"""

# 4. Patch ring() volume usage:
target_ring = """    if (s) {
      s.muted = false;
      s.volume = 1;
      s.currentTime = 0;"""

new_ring = """    if (s) {
      s.muted = false;
      s.volume = currentVolume;
      s.currentTime = 0;"""

# 5. Patch init()
target_init = """    loadSetting();
    buildRing();"""

new_init = """    loadSetting();
    buildRing();
    loadSoundSettings();
    buildSoundControls();"""

def replace_smart(t, target, rep):
    if target in t:
        return t.replace(target, rep, 1)
    target_crlf = target.replace("\n", "\r\n")
    rep_crlf = rep.replace("\n", "\r\n")
    if target_crlf in t:
        return t.replace(target_crlf, rep_crlf, 1)
    return None

for label, tgt, rep in [
    ("vars", target_vars, new_vars),
    ("sound", target_sound, new_sound),
    ("sound_fns", target_before_load, new_sound_fns),
    ("ring", target_ring, new_ring),
    ("init", target_init, new_init),
]:
    res = replace_smart(text, tgt, rep)
    if res:
        text = res
        print(f"Patched {label}")
    else:
        print(f"Failed to patch {label}")

with open(alerts_path, "w", encoding="utf-8") as f:
    f.write(text)

print("Saved web/alerts.js")
