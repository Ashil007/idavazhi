import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1. Update web/index.html: remove the separate test sound button
index_path = os.path.join(ROOT, "web", "index.html")
with open(index_path, "r", encoding="utf-8") as f:
    index_text = f.read()

old_head = """          <div class="sound__head">
            <span class="sound__label">Alarm sound</span>
            <button type="button" class="sound__preview" id="tone-preview" aria-label="Test alarm sound" title="Test alarm sound">
              <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" width="14" height="14"><path d="M8 5v14l11-7z"/></svg>
              <span>Test sound</span>
            </button>
          </div>"""

new_head = """          <div class="sound__head">
            <span class="sound__label">Alarm sound</span>
          </div>"""

def replace_smart(t, target, rep):
    if target in t:
        return t.replace(target, rep, 1)
    target_crlf = target.replace("\n", "\r\n")
    rep_crlf = rep.replace("\n", "\r\n")
    if target_crlf in t:
        return t.replace(target_crlf, rep_crlf, 1)
    return None

res_index = replace_smart(index_text, old_head, new_head)
if res_index:
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(res_index)
    print("Updated web/index.html")
else:
    print("Failed to find target in web/index.html")

# 2. Update web/alerts.js
alerts_path = os.path.join(ROOT, "web", "alerts.js")
with open(alerts_path, "r", encoding="utf-8") as f:
    alerts_text = f.read()

old_preview_block = """  function togglePreview() {
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
  }"""

new_preview_block = """  function previewTone(toneKey) {
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
  }"""

res_alerts1 = replace_smart(alerts_text, old_preview_block, new_preview_block)
if res_alerts1:
    alerts_text = res_alerts1
    print("Updated preview function in web/alerts.js")
else:
    print("Failed to find old preview block in web/alerts.js")

old_click_block = """        b.addEventListener("click", function () {
          setTone(k);
          togglePreview();
        });"""

new_click_block = """        b.addEventListener("click", function () {
          if (currentTone === k && previewTimer) {
            clearTimeout(previewTimer);
            previewTimer = null;
            if (siren) { siren.pause(); siren.currentTime = 0; }
            return;
          }
          setTone(k);
          previewTone(k);
        });"""

res_alerts2 = replace_smart(alerts_text, old_click_block, new_click_block)
if res_alerts2:
    alerts_text = res_alerts2
    print("Updated click handler in web/alerts.js")
else:
    print("Failed to find old click block in web/alerts.js")

old_vol_change = """      volSlider.addEventListener("change", function () {
        if (!previewTimer) togglePreview();
      });"""

new_vol_change = """      volSlider.addEventListener("change", function () {
        previewTone(currentTone);
      });"""

res_alerts3 = replace_smart(alerts_text, old_vol_change, new_vol_change)
if res_alerts3:
    alerts_text = res_alerts3
    print("Updated vol slider in web/alerts.js")

old_preview_btn_binding = """    var previewBtn = el("tone-preview");
    if (previewBtn) previewBtn.addEventListener("click", togglePreview);"""

res_alerts4 = replace_smart(alerts_text, old_preview_btn_binding, "")
if res_alerts4:
    alerts_text = res_alerts4
    print("Removed previewBtn binding in web/alerts.js")

with open(alerts_path, "w", encoding="utf-8") as f:
    f.write(alerts_text)
print("Saved web/alerts.js")
