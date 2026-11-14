with open('web/style.css', 'r', encoding='utf-8') as f:
    c = f.read()

target = """.sound__drop-close {
  display: grid; place-items: center; width: 28px; height: 28px;
  border-radius: 8px; background: transparent; border: 0; color: var(--ink-dim);
  cursor: pointer; transition: background .15s, color .15s;
}
.sound__drop-close:hover {
  background: var(--hover); color: var(--ink);
}"""

replacement = """.sound__drop-close {
  display: grid; place-items: center; width: 32px; height: 32px;
  border-radius: 8px; background: transparent; border: 0; color: var(--ink-dim);
  cursor: pointer; transition: background .15s, color .15s, transform .1s;
}
.sound__drop-close:hover {
  background: var(--hover); color: var(--ink);
}
.sound__drop-close:active {
  transform: scale(0.92);
}"""

if target in c:
    c = c.replace(target, replacement)
    with open('web/style.css', 'w', encoding='utf-8') as f:
        f.write(c)
    print('style.css successfully updated')
else:
    print('Target not found in style.css')
