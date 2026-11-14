import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def replace_smart(text, target, rep):
    if target in text:
        return text.replace(target, rep, 1)
    target_crlf = target.replace("\n", "\r\n")
    rep_crlf = rep.replace("\n", "\r\n")
    if target_crlf in text:
        return text.replace(target_crlf, rep_crlf, 1)
    return None

# ==============================================================================
# 1. Update web/index.html: Replace tone chips with Dropdown Picker & Apply Button
# ==============================================================================
index_path = os.path.join(ROOT, "web", "index.html")
with open(index_path, "r", encoding="utf-8") as f:
    index_text = f.read()

old_sound_box = """        <div class="trip__sound" id="trip-sound-box">
          <div class="sound__head">
            <span class="sound__label">Alarm sound</span>
          </div>
          <div class="sound__tones" id="tone-chips" role="radiogroup" aria-label="Alarm sound tone"></div>
          <div class="sound__vol">
            <span class="sound__vol-icon" aria-hidden="true" title="Volume">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="18" height="18"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>
            </span>
            <input type="range" id="trip-volume" min="10" max="100" value="100" step="5" aria-label="Alarm volume" />
            <span class="sound__vol-val" id="trip-volume-val">100%</span>
          </div>
        </div>"""

new_sound_box = """        <div class="trip__sound" id="trip-sound-box">
          <div class="sound__head">
            <span class="sound__label">Alarm sound</span>
            <span class="sound__active-tone" id="sound-active-tone">Classic Digital</span>
          </div>
          <div class="sound__picker">
            <button type="button" class="sound__drop-btn" id="sound-drop-btn" aria-haspopup="true" aria-expanded="false">
              <span class="sound__drop-val" id="sound-drop-label">Classic Digital</span>
              <svg class="sound__drop-chevron" viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M7 10l5 5 5-5z"/></svg>
            </button>
            <div class="sound__dropdown" id="sound-dropdown" hidden>
              <div class="sound__drop-title">Tap any sound to test it:</div>
              <div class="sound__drop-list" id="sound-drop-list" role="radiogroup" aria-label="Alarm ringtone"></div>
              <div class="sound__drop-actions">
                <button type="button" class="sound__action-cancel" id="sound-cancel-btn">Cancel</button>
                <button type="button" class="sound__action-apply" id="sound-apply-btn">Apply</button>
              </div>
            </div>
          </div>
          <div class="sound__vol">
            <span class="sound__vol-icon" aria-hidden="true" title="Volume">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="18" height="18"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>
            </span>
            <input type="range" id="trip-volume" min="10" max="100" value="100" step="5" aria-label="Alarm volume" />
            <span class="sound__vol-val" id="trip-volume-val">100%</span>
          </div>
        </div>"""

res_index = replace_smart(index_text, old_sound_box, new_sound_box)
if res_index:
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(res_index)
    print("Patched web/index.html")
else:
    print("Failed to patch web/index.html")

# ==============================================================================
# 2. Update web/style.css: Style the Dropdown Picker, List Items, and Apply Button
# ==============================================================================
style_path = os.path.join(ROOT, "web", "style.css")
with open(style_path, "r", encoding="utf-8") as f:
    style_text = f.read()

old_css_fragment = """.sound__tones {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(64px, 1fr)); gap: 6px;
}
.tone__chip {
  min-height: 42px; padding: 0 4px; border-radius: 10px; cursor: pointer;
  font: inherit; font-size: 14px; font-weight: 600;
  background: var(--panel-3); color: var(--ink); border: 0;
  display: grid; place-items: center; text-align: center;
  transition: background .15s, color .15s;
}
.tone__chip:hover { background: var(--hover); }
.tone__chip[aria-checked="true"] { background: var(--amber); color: var(--amber-ink); }
.tone__chip:focus-visible { outline: 3px solid #fff; outline-offset: 2px; }"""

new_css_fragment = """.sound__active-tone {
  font-size: 13px; font-weight: 600; color: var(--amber);
}
.sound__picker {
  position: relative; width: 100%;
}
.sound__drop-btn {
  width: 100%; min-height: 46px; padding: 10px 14px; border-radius: 12px;
  background: var(--panel-3); color: var(--ink); border: 1px solid var(--line);
  display: flex; align-items: center; justify-content: space-between;
  font: inherit; font-size: 15px; font-weight: 600; cursor: pointer;
  transition: background .15s, border-color .15s;
}
.sound__drop-btn:hover { background: var(--hover); border-color: var(--amber); }
.sound__drop-btn[aria-expanded="true"] { border-color: var(--amber); }
.sound__drop-chevron {
  flex-shrink: 0; color: var(--ink-dim); transition: transform .2s;
}
.sound__drop-btn[aria-expanded="true"] .sound__drop-chevron {
  transform: rotate(180deg);
}

.sound__dropdown {
  position: absolute; top: calc(100% + 6px); left: 0; right: 0; z-index: 600;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-lg);
  box-shadow: 0 16px 36px rgba(0,0,0,0.45); padding: 12px;
  display: flex; flex-direction: column; gap: 8px;
}
.sound__drop-title {
  font-size: 13px; font-weight: 600; color: var(--ink-dim); padding: 2px 4px 6px;
  border-bottom: 1px solid var(--line);
}
.sound__drop-list {
  display: flex; flex-direction: column; gap: 4px; max-height: 220px; overflow-y: auto;
  padding-right: 2px;
}
.sound__drop-list::-webkit-scrollbar { width: 6px; }
.sound__drop-list::-webkit-scrollbar-thumb { background: var(--panel-3); border-radius: 6px; }
.sound__drop-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 10px; border-radius: 8px; cursor: pointer;
  background: transparent; color: var(--ink); border: 1px solid transparent;
  font: inherit; text-align: left; transition: background .12s;
}
.sound__drop-item:hover { background: var(--hover); }
.sound__drop-item[aria-checked="true"] {
  background: var(--panel-3); border-color: var(--amber);
}
.sound__item-info {
  display: flex; flex-direction: column; gap: 2px;
}
.sound__item-name {
  font-size: 14px; font-weight: 600; color: var(--ink);
}
.sound__item-desc {
  font-size: 12px; color: var(--ink-dim);
}
.sound__item-icon {
  font-size: 15px; color: var(--amber); opacity: 0; transition: opacity .15s;
}
.sound__drop-item[aria-checked="true"] .sound__item-icon {
  opacity: 1;
}

.sound__drop-actions {
  display: flex; align-items: center; justify-content: flex-end; gap: 8px;
  padding-top: 8px; border-top: 1px solid var(--line);
}
.sound__action-cancel {
  padding: 6px 12px; border-radius: 8px; background: transparent;
  color: var(--ink-dim); border: 0; font: inherit; font-size: 14px; font-weight: 600;
  cursor: pointer;
}
.sound__action-cancel:hover { color: var(--ink); }
.sound__action-apply {
  padding: 8px 18px; border-radius: 8px; background: var(--amber);
  color: var(--amber-ink); border: 0; font: inherit; font-size: 14px; font-weight: 700;
  cursor: pointer; box-shadow: var(--sh-1);
}
.sound__action-apply:hover { filter: brightness(1.08); }"""

res_style = replace_smart(style_text, old_css_fragment, new_css_fragment)
if res_style:
    with open(style_path, "w", encoding="utf-8") as f:
        f.write(res_style)
    print("Patched web/style.css")
else:
    print("Failed to patch web/style.css")

# ==============================================================================
# 3. Update web/alerts.js: 8 Tones, Play Selected Tone on Final Stop Arrival,
#    Dropdown Selection with Auditioning and Apply Locking
# ==============================================================================
alerts_path = os.path.join(ROOT, "web", "alerts.js")
with open(alerts_path, "r", encoding="utf-8") as f:
    alerts_text = f.read()

old_tones_def = """  var TONES = {
    classic: { name: "Classic", desc: "Digital clock beeps" },
    chime:   { name: "Chime",   desc: "Soft harmonic bell" },
    radar:   { name: "Radar",   desc: "Sonar pulse" },
    marimba: { name: "Marimba", desc: "Melodic chime" },
    siren:   { name: "Siren",   desc: "Urgent two-tone alert" }
  };"""

new_tones_def = """  var TONES = {
    classic: { name: "Classic Digital", desc: "Digital clock 4-beep pulse" },
    chime:   { name: "Harmonic Bell",   desc: "Soft dual acoustic chime" },
    marimba: { name: "Morning Marimba", desc: "Cheerful 4-note melody" },
    radar:   { name: "Sonar Pulse",     desc: "Rhythmic radar sweep" },
    siren:   { name: "Emergency Siren", desc: "Urgent alternating siren" },
    cosmic:  { name: "Cosmic Synth",    desc: "Futuristic space arpeggio" },
    harp:    { name: "Gentle Harp",     desc: "Cascading soothing strings" },
    birds:   { name: "Forest Birds",    desc: "Crisp natural chirping" }
  };
  var pendingTone = "classic";"""

res_a1 = replace_smart(alerts_text, old_tones_def, new_tones_def)
if res_a1:
    alerts_text = res_a1

# Update alarmTone waveform math to support cosmic, harp, birds
old_alarm_tone = """      } else if (key === "siren") {
        var step = Math.floor(t / 0.14);
        var sf = (step % 2 === 0) ? 960 : 1240;
        var su = t - step * 0.14;
        if (step < 7 && su < 0.13) {
          var env = Math.min(1, su / 0.008, (0.13 - su) / 0.008);
          x = env * (0.7 * Math.sin(2 * Math.PI * sf * t) + 0.3 * Math.sin(6 * Math.PI * sf * t));
        }
      } else {"""

new_alarm_tone = """      } else if (key === "siren") {
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
      } else {"""

res_a2 = replace_smart(alerts_text, old_alarm_tone, new_alarm_tone)
if res_a2:
    alerts_text = res_a2

# Replace alarm arrival call so final stop uses selected tone
old_alarm_fn = """  function alarm(title, body, loud) {
    el("alarm-title").textContent = title;
    el("alarm-body").textContent = body;
    el("alarm").hidden = false;
    el("alarm").classList.toggle("is-loud", !!loud);
    notify(title, body);
    if (loud) { ring(); return; }
    stopRinging();
    vibrate([300, 150, 300]);
    if (!silent()) beep();
  }"""

new_alarm_fn = """  // Play one cycle of the selected alarm tone for arrival / gentle alerts
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
  function alarm(title, body, loud) {
    el("alarm-title").textContent = title;
    el("alarm-body").textContent = body;
    el("alarm").hidden = false;
    el("alarm").classList.toggle("is-loud", !!loud);
    notify(title, body);
    if (loud) { ring(); return; }
    stopRinging();
    vibrate([300, 150, 300]);
    if (!silent()) playArrivalSound();
  }"""

res_a3 = replace_smart(alerts_text, old_alarm_fn, new_alarm_fn)
if res_a3:
    alerts_text = res_a3

# Replace sound controls: Dropdown with Apply locking
old_sound_controls = """  /* ------------------------------------------- tone & volume controls */

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
    }, 2500);
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
          if (currentTone === k && previewTimer) {
            clearTimeout(previewTimer);
            previewTimer = null;
            if (siren) { siren.pause(); siren.currentTime = 0; }
            return;
          }
          setTone(k);
          previewTone(k);
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
  }"""

new_sound_controls = """  /* ------------------------------------------- tone & volume controls */

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
    var activeLabel = el("sound-active-tone");
    if (activeLabel) activeLabel.textContent = name;
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
    if (drop) drop.hidden = true;
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

    var applyBtn = el("sound-apply-btn");
    if (applyBtn) {
      applyBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        stopPreview();
        setTone(pendingTone); // locks up the selected tone
        closeDropdown();      // dropdown disappears
      });
    }

    var cancelBtn = el("sound-cancel-btn");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        closeDropdown();
      });
    }

    // Close when clicking outside
    document.addEventListener("click", function (e) {
      var box = el("trip-sound-box");
      if (box && !box.contains(e.target)) {
        closeDropdown();
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
  }"""

res_a4 = replace_smart(alerts_text, old_sound_controls, new_sound_controls)
if res_a4:
    alerts_text = res_a4

with open(alerts_path, "w", encoding="utf-8") as f:
    f.write(alerts_text)
print("Saved web/alerts.js")
