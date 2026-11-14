import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1. Patch web/index.html
index_path = os.path.join(ROOT, "web", "index.html")
with open(index_path, "r", encoding="utf-8") as f:
    index_text = f.read()

target_index = """          </select>
        </div>
        <label class="switch trip__silent">"""

sound_box_html = """          </select>
        </div>
        <div class="trip__sound" id="trip-sound-box">
          <div class="sound__head">
            <span class="sound__label">Alarm sound</span>
            <button type="button" class="sound__preview" id="tone-preview" aria-label="Test alarm sound" title="Test alarm sound">
              <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" width="14" height="14"><path d="M8 5v14l11-7z"/></svg>
              <span>Test sound</span>
            </button>
          </div>
          <div class="sound__tones" id="tone-chips" role="radiogroup" aria-label="Alarm sound tone"></div>
          <div class="sound__vol">
            <span class="sound__vol-icon" aria-hidden="true" title="Volume">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="18" height="18"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>
            </span>
            <input type="range" id="trip-volume" min="10" max="100" value="100" step="5" aria-label="Alarm volume" />
            <span class="sound__vol-val" id="trip-volume-val">100%</span>
          </div>
        </div>
        <label class="switch trip__silent">"""

if target_index in index_text:
    index_text = index_text.replace(target_index, sound_box_html, 1)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_text)
    print("Patched web/index.html")
else:
    # Try with CRLF
    target_crlf = target_index.replace("\n", "\r\n")
    sound_crlf = sound_box_html.replace("\n", "\r\n")
    if target_crlf in index_text:
        index_text = index_text.replace(target_crlf, sound_crlf, 1)
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(index_text)
        print("Patched web/index.html (CRLF)")
    else:
        print("Target not found in web/index.html")
