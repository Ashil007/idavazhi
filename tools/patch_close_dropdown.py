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
# 1. Update web/index.html: Add cross close button inside sound-dropdown header
# ==============================================================================
index_path = os.path.join(ROOT, "web", "index.html")
with open(index_path, "r", encoding="utf-8") as f:
    index_text = f.read()

old_drop_header = """            <div class="sound__dropdown" id="sound-dropdown" hidden>
              <div class="sound__drop-title">Tap any sound to test it:</div>"""

new_drop_header = """            <div class="sound__dropdown" id="sound-dropdown" hidden>
              <div class="sound__drop-head">
                <span class="sound__drop-title">Tap any sound to test it:</span>
                <button type="button" class="sound__drop-close" id="sound-close-btn" aria-label="Close sound selection" title="Close">
                  <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <line x1="18" y1="6" x2="6" y2="18"></line>
                    <line x1="6" y1="6" x2="18" y2="18"></line>
                  </svg>
                </button>
              </div>"""

res_index = replace_smart(index_text, old_drop_header, new_drop_header)
if res_index:
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(res_index)
    print("Patched web/index.html")
else:
    print("Failed to patch web/index.html")

# ==============================================================================
# 2. Update web/style.css: Add styling for .sound__drop-head and .sound__drop-close
# ==============================================================================
style_path = os.path.join(ROOT, "web", "style.css")
with open(style_path, "r", encoding="utf-8") as f:
    style_text = f.read()

old_css_title = """.sound__drop-title {
  font-size: 13px; font-weight: 600; color: var(--ink-dim); padding: 2px 4px 6px;
  border-bottom: 1px solid var(--line);
}"""

new_css_title = """.sound__drop-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 2px 4px 6px; border-bottom: 1px solid var(--line);
}
.sound__drop-title {
  font-size: 13px; font-weight: 600; color: var(--ink-dim);
}
.sound__drop-close {
  display: grid; place-items: center; width: 28px; height: 28px;
  border-radius: 8px; background: transparent; border: 0; color: var(--ink-dim);
  cursor: pointer; transition: background .15s, color .15s;
}
.sound__drop-close:hover {
  background: var(--hover); color: var(--ink);
}"""

res_style = replace_smart(style_text, old_css_title, new_css_title)
if res_style:
    with open(style_path, "w", encoding="utf-8") as f:
        f.write(res_style)
    print("Patched web/style.css")
else:
    print("Failed to patch web/style.css")

# ==============================================================================
# 3. Update web/alerts.js: Close on outside click (pointerdown & click) + Cross button
# ==============================================================================
alerts_path = os.path.join(ROOT, "web", "alerts.js")
with open(alerts_path, "r", encoding="utf-8") as f:
    alerts_text = f.read()

old_alerts_handlers = """    var cancelBtn = el("sound-cancel-btn");
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
    });"""

new_alerts_handlers = """    var cancelBtn = el("sound-cancel-btn");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        closeDropdown();
      });
    }

    var closeBtn = el("sound-close-btn");
    if (closeBtn) {
      closeBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        closeDropdown();
      });
    }

    // Close when tapping anywhere outside the dropdown
    function handleOutside(e) {
      var drop = el("sound-dropdown");
      var btn = el("sound-drop-btn");
      if (!drop || drop.hidden) return;
      if (!drop.contains(e.target) && (!btn || !btn.contains(e.target))) {
        closeDropdown();
      }
    }
    document.addEventListener("pointerdown", handleOutside, true);
    document.addEventListener("click", handleOutside, true);"""

res_alerts = replace_smart(alerts_text, old_alerts_handlers, new_alerts_handlers)
if res_alerts:
    with open(alerts_path, "w", encoding="utf-8") as f:
        f.write(res_alerts)
    print("Patched web/alerts.js")
else:
    print("Failed to patch web/alerts.js")
