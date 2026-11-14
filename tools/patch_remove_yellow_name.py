# Update web/index.html
with open('web/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

target_html = """          <div class="sound__head">
            <span class="sound__label">Alarm sound</span>
            <span class="sound__active-tone" id="sound-active-tone">Classic Digital</span>
          </div>"""

replacement_html = """          <div class="sound__head">
            <span class="sound__label">Alarm sound</span>
          </div>"""

if target_html in html:
    html = html.replace(target_html, replacement_html)
    with open('web/index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("index.html successfully updated")
else:
    print("target_html not found")

# Update web/alerts.js
with open('web/alerts.js', 'r', encoding='utf-8') as f:
    js = f.read()

target_js = """  function updateToneLabels() {
    var name = (TONES[currentTone] && TONES[currentTone].name) || "Classic Digital";
    var dropLabel = el("sound-drop-label");
    if (dropLabel) dropLabel.textContent = name;
    var activeLabel = el("sound-active-tone");
    if (activeLabel) activeLabel.textContent = name;
  }"""

replacement_js = """  function updateToneLabels() {
    var name = (TONES[currentTone] && TONES[currentTone].name) || "Classic Digital";
    var dropLabel = el("sound-drop-label");
    if (dropLabel) dropLabel.textContent = name;
  }"""

if target_js in js:
    js = js.replace(target_js, replacement_js)
    with open('web/alerts.js', 'w', encoding='utf-8') as f:
        f.write(js)
    print("alerts.js successfully updated")
else:
    print("target_js not found")

# Update web/style.css
with open('web/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

target_css = """.sound__active-tone {
  font-size: 13px; font-weight: 600; color: var(--amber);
}
"""
if target_css in css:
    css = css.replace(target_css, "")
    with open('web/style.css', 'w', encoding='utf-8') as f:
        f.write(css)
    print("style.css successfully updated")
else:
    print("target_css not found")

# Bump sw.js cache version
with open('web/sw.js', 'r', encoding='utf-8') as f:
    sw = f.read()

if 'vazhi-shell-v48' in sw:
    sw = sw.replace('vazhi-shell-v48', 'vazhi-shell-v49')
    with open('web/sw.js', 'w', encoding='utf-8') as f:
        f.write(sw)
    print("sw.js successfully updated to v49")
