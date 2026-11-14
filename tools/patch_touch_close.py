with open('web/alerts.js', 'r', encoding='utf-8') as f:
    c = f.read()

target = """    document.addEventListener("pointerdown", handleOutside, true);
    document.addEventListener("click", handleOutside, true);"""

replacement = """    document.addEventListener("pointerdown", handleOutside, true);
    document.addEventListener("touchstart", handleOutside, { capture: true, passive: true });
    document.addEventListener("click", handleOutside, true);
    document.addEventListener("keydown", function (e) {
      if ((e.key === "Escape" || e.key === "Esc") && !e.defaultPrevented) {
        var drop = el("sound-dropdown");
        if (drop && !drop.hidden) closeDropdown();
      }
    });"""

if target in c:
    c = c.replace(target, replacement)
    with open('web/alerts.js', 'w', encoding='utf-8') as f:
        f.write(c)
    print('alerts.js successfully updated')
else:
    print('Target not found')
