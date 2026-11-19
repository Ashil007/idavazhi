"""Draw Idavazhi's app icons into web/icons/ (needs Pillow: pip install pillow).

    python tools/make_icons.py

A winding green bus route ending at an amber pin, on the app's dark
background. Drawn large and scaled down, for smooth edges. The "maskable"
icon fills the whole square and keeps the drawing inside the middle 80%,
since Android crops icons to a circle or squircle.
"""

import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "icons")
S = 1024                                   # drawing size
BG = (17, 21, 28)                          # --bg
BG2 = (30, 38, 51)                         # --panel-2
GREEN = (34, 160, 107)                     # --bus
AMBER = (245, 182, 66)


def bezier(p0, p1, p2, p3, n=200):
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        yield (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
               u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def draw(full_bleed, scale):
    """The icon at S x S. `scale` shrinks the drawing toward the middle."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if full_bleed:
        d.rectangle([0, 0, S, S], fill=BG)
    else:
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=BG)
    # A soft lighter disc behind the drawing.
    r = S * 0.40 * scale
    d.ellipse([S / 2 - r, S / 2 - r, S / 2 + r, S / 2 + r], fill=BG2)

    def at(x, y):                          # 0..1 drawing space -> pixels
        return (S / 2 + (x - 0.5) * S * scale, S / 2 + (y - 0.5) * S * scale)

    # The route: bottom-left to the pin, with stops along it.
    pts = list(bezier(at(0.17, 0.80), at(0.52, 0.86), at(0.28, 0.45), at(0.60, 0.50)))
    w = S * 0.055 * scale
    for x, y in pts:
        d.ellipse([x - w, y - w, x + w, y + w], fill=GREEN)
    for i in (0, 70, 140):
        x, y = pts[i]
        sr = w * 0.55
        d.ellipse([x - sr, y - sr, x + sr, y + sr], fill=(255, 255, 255))

    # The pin: a circle with a point at the bottom, sitting on the route's end.
    cx, cy = at(0.66, 0.30)
    pr = S * 0.15 * scale
    tip = at(0.66, 0.53)
    a = math.radians(38)
    d.polygon([(cx - pr * math.cos(a), cy + pr * math.sin(a)),
               (cx + pr * math.cos(a), cy + pr * math.sin(a)), tip], fill=AMBER)
    d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=AMBER)
    hr = pr * 0.42
    d.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=BG)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    plain, maskable = draw(False, 1.0), draw(True, 0.78)
    jobs = [(plain, 192, "icon-192.png"), (plain, 512, "icon-512.png"),
            (maskable, 512, "maskable-512.png"), (maskable, 192, "maskable-192.png"),
            (draw(True, 0.9), 180, "apple-touch-icon.png"),   # iOS rounds the corners itself
            (plain, 32, "favicon-32.png")]
    for img, size, name in jobs:
        out = img.resize((size, size), Image.LANCZOS)
        if name == "apple-touch-icon.png":
            out = out.convert("RGB")       # iOS shows transparency as black
        out.save(os.path.join(OUT, name), optimize=True)
        print("wrote", os.path.join("web", "icons", name))


if __name__ == "__main__":
    main()
