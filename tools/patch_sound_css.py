import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1. Patch web/style.css
style_path = os.path.join(ROOT, "web", "style.css")
with open(style_path, "r", encoding="utf-8") as f:
    style_text = f.read()

# Add order for .trip__sound
old_order = """.trip__when { order: 3; }
  .trip__silent { order: 4; }
  .trip__share { order: 4; }
  #trip-sim { order: 5; }
  .trip__note { order: 6; }"""

new_order = """.trip__when { order: 3; }
  .trip__sound { order: 4; }
  .trip__silent { order: 5; }
  .trip__share { order: 5; }
  #trip-sim { order: 6; }
  .trip__note { order: 7; }"""

sound_css = """
/* Alarm sound tone & volume control */
.trip__sound {
  padding: 16px; border-radius: var(--radius-lg); background: var(--panel-2);
  display: flex; flex-direction: column; gap: 12px; transition: opacity .2s;
}
.trip__sound.is-disabled {
  opacity: .45; pointer-events: none;
}
.sound__head {
  display: flex; align-items: center; justify-content: space-between;
}
.sound__label {
  font-size: 14px; font-weight: 600; line-height: 20px; color: var(--ink-dim);
}
.sound__preview {
  display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px;
  border-radius: 999px; background: var(--panel-3); color: var(--ink);
  font-size: 13px; font-weight: 600; border: 0; cursor: pointer;
  transition: background .15s, color .15s;
}
.sound__preview:hover { background: var(--hover); }
.sound__preview.is-playing { background: var(--amber); color: var(--amber-ink); }
.sound__preview svg { display: block; }
.sound__tones {
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
.tone__chip:focus-visible { outline: 3px solid #fff; outline-offset: 2px; }

.sound__vol {
  display: flex; align-items: center; gap: 12px; margin-top: 4px;
}
.sound__vol-icon {
  display: grid; place-items: center; color: var(--ink-dim); flex-shrink: 0;
}
.sound__vol input[type="range"] {
  flex: 1; min-width: 0; height: 6px; border-radius: 6px;
  background: var(--panel-3); accent-color: var(--amber); cursor: pointer;
  -webkit-appearance: none; appearance: none;
}
.sound__vol input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none; width: 18px; height: 18px; border-radius: 50%;
  background: var(--amber); cursor: pointer; border: 2px solid var(--panel-2);
}
.sound__vol input[type="range"]::-moz-range-thumb {
  width: 18px; height: 18px; border-radius: 50%;
  background: var(--amber); cursor: pointer; border: 2px solid var(--panel-2);
}
.sound__vol-val {
  font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums;
  color: var(--ink-dim); min-width: 38px; text-align: right;
}
"""

def replace_smart(text, target, rep):
    if target in text:
        return text.replace(target, rep, 1)
    target_crlf = target.replace("\n", "\r\n")
    rep_crlf = rep.replace("\n", "\r\n")
    if target_crlf in text:
        return text.replace(target_crlf, rep_crlf, 1)
    return None

res = replace_smart(style_text, old_order, new_order)
if res:
    style_text = res

target_switch = """@media (prefers-reduced-motion: reduce) { .switch__track, .switch__track::after { transition: none; } }"""
res2 = replace_smart(style_text, target_switch, target_switch + "\n" + sound_css)
if res2:
    style_text = res2
    with open(style_path, "w", encoding="utf-8") as f:
        f.write(style_text)
    print("Patched web/style.css")
else:
    print("Target switch not found in web/style.css")
