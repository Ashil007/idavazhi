"""Hear the alarm in a few voices before choosing one.

    python tools/voice_samples.py                 # Sarvam: pooja, kavitha, priya, ritu
    python tools/voice_samples.py pooja shruti    # just these voices

Makes two whole alarm lines ("wake" and "soon", for Vyttila) in each voice
and saves them as MP3s in .cache/voice-samples/, named after the voice.
Uses the Sarvam key in data/vazhi.env; each voice costs about 130 characters
(well under ₹1). The app's own clips are not touched.

To use a voice in the app, put VAZHI_SARVAM_SPEAKER=<name> in data/vazhi.env
and restart the server; phones fetch the new clips by themselves.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from transitnav import settings, voice  # noqa: E402

OUT = os.path.join(ROOT, ".cache", "voice-samples")
DEFAULT_SPEAKERS = ["pooja", "kavitha", "priya", "ritu"]
SAMPLES = [("wake", "Vyttila", "വൈറ്റില"), ("soon", "Vyttila", "വൈറ്റില")]


def main(speakers):
    if not settings.get("VAZHI_SARVAM_KEY"):
        sys.exit("Put VAZHI_SARVAM_KEY=<your key> in data/vazhi.env first.")
    os.makedirs(OUT, exist_ok=True)
    real_get = settings.get
    for speaker in speakers:
        # This voice, for these lines only.
        settings.get = lambda k, d="", s=speaker: (s if k == "VAZHI_SARVAM_SPEAKER" else
                                                   "sarvam" if k == "VAZHI_VOICE_PROVIDER" else real_get(k, d))
        for kind, name, name_ml in SAMPLES:
            text = voice.line(kind, name, name_ml)
            try:
                data = voice._request(text)
            except Exception as exc:  # noqa: BLE001
                detail = ""
                if hasattr(exc, "read"):
                    detail = exc.read().decode("utf-8", "replace")[:300]
                print("%s: %s %s" % (speaker, exc, detail))
                break
            path = os.path.join(OUT, "%s-%s.mp3" % (speaker, kind))
            with open(path, "wb") as fh:
                fh.write(data)
            print("%-8s %-5s %6d bytes  %s" % (speaker, kind, len(data), path))
    settings.get = real_get


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT_SPEAKERS)
