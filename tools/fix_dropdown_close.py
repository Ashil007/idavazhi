# 1. Update web/style.css
with open('web/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

target_css = """.sound__dropdown {
  position: absolute; top: calc(100% + 6px); left: 0; right: 0; z-index: 600;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-lg);
  box-shadow: 0 16px 36px rgba(0,0,0,0.45); padding: 12px;
  display: flex; flex-direction: column; gap: 8px;
}"""

replacement_css = """.sound__dropdown {
  position: absolute; top: calc(100% + 6px); left: 0; right: 0; z-index: 600;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-lg);
  box-shadow: 0 16px 36px rgba(0,0,0,0.45); padding: 12px;
  display: flex; flex-direction: column; gap: 8px;
}
.sound__dropdown[hidden] {
  display: none !important;
}
.sound__drop-close svg {
  pointer-events: none;
}"""

if target_css in css:
    css = css.replace(target_css, replacement_css)
    with open('web/style.css', 'w', encoding='utf-8') as f:
        f.write(css)
    print("style.css updated successfully")
else:
    print("target_css not found in style.css")

# 2. Update web/alerts.js
with open('web/alerts.js', 'r', encoding='utf-8') as f:
    js = f.read()

target_drop_funcs = """  function closeDropdown() {
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
  }"""

replacement_drop_funcs = """  function closeDropdown() {
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
  }"""

if target_drop_funcs in js:
    js = js.replace(target_drop_funcs, replacement_drop_funcs)
else:
    print("target_drop_funcs not found in alerts.js")

target_btn_bindings = """    var applyBtn = el("sound-apply-btn");
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
    }"""

replacement_btn_bindings = """    function bindDismiss(buttonEl) {
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
    }"""

if target_btn_bindings in js:
    js = js.replace(target_btn_bindings, replacement_btn_bindings)
    with open('web/alerts.js', 'w', encoding='utf-8') as f:
        f.write(js)
    print("alerts.js updated successfully")
else:
    print("target_btn_bindings not found in alerts.js")

# 3. Bump sw.js cache version
with open('web/sw.js', 'r', encoding='utf-8') as f:
    sw = f.read()

if 'vazhi-shell-v49' in sw:
    sw = sw.replace('vazhi-shell-v49', 'vazhi-shell-v50')
    with open('web/sw.js', 'w', encoding='utf-8') as f:
        f.write(sw)
    print("sw.js bumped to v50")
else:
    print("vazhi-shell-v49 not found in sw.js")
