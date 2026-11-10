# Map fonts

The letters the map draws its labels with, as MapLibre glyph files
(`{font}/{first}-{last}.pbf`, one block of 256 characters each).

- **Font:** Noto Sans (Regular and Medium), by Google and the Noto project,
  under the [SIL Open Font License 1.1](https://openfontlicense.org).
- **Glyph files:** made by Protomaps
  ([basemaps-assets](https://github.com/protomaps/basemaps-assets)).

Only the blocks Kerala's place names need are kept: Latin (0–1023 and
7680–7935), punctuation and symbols such as ₹ (8192–8703), and Malayalam
(3328–3583). The server answers any other block with an empty file
(`transitnav/server.py`), so a stray character is left out rather than
breaking the label.
