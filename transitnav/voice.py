"""Spoken Malayalam announcements for the trip alarm, made by ElevenLabs.

The phone's own voice (speechSynthesis) sounds like a robot, so the alarm's
lines are spoken by an ElevenLabs voice instead, in pure Malayalam.

To keep ElevenLabs' monthly allowance from running out, a line is not made
whole for every stop. It is stitched from saved pieces:

    "ഉണരൂ! നിങ്ങൾ ഇറങ്ങേണ്ട സ്ഥലം,"  +  "വൈറ്റില"  +  "അടുത്തെത്തി. ഇറങ്ങാൻ ഒരുങ്ങുക."
     fixed phrase, made once          stop name     fixed phrase, made once

The fixed phrases are about 370 characters in all and are made once, ever.
After that a new stop costs only its name (about 7 characters), and a stop
any rider has already heard costs nothing. Every piece is saved in
.cache/voice/; the server joins the pieces into one MP3, so the phone plays a
single ordinary sound file, which keeps working with the screen locked.

Lines are fixed sentences with only the stop's name filled in, so a saved
piece stays right for everyone (a live "in 4 minutes" would not). The name
always stands alone, in its plain form: the case endings Malayalam adds to
names cannot be put on thousands of stop names reliably.

The phone fetches a trip's lines when the alarm is switched on, so they play
with no signal on the bus. If there is no key, or ElevenLabs does not
answer, the app falls back to the phone's own voice (GET /api/voice/lines).

Settings (data/vazhi.env, never in git):

    VAZHI_ELEVENLABS_KEY     the API key (elevenlabs.io: Profile -> API Keys)
    VAZHI_ELEVENLABS_VOICE   the voice id (default: the voice chosen for Vazhi)
    VAZHI_ELEVENLABS_MODEL   default eleven_v3, which speaks Malayalam
    VAZHI_VOICE_DAILY_CHARS  the most characters of new stop names sent to
                             ElevenLabs in a day (default 300). The fixed
                             phrases are not counted: they are made only once.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from . import settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, ".cache", "voice")
API = "https://api.elevenlabs.io/v1/text-to-speech/%s?output_format=mp3_44100_64"
DEFAULT_VOICE = "nF5xXwlriNhsorW8Fykg"
DEFAULT_MODEL = "eleven_v3"
TIMEOUT_S = 30
MAX_NAME = 60

# The alarm's lines, in pure Malayalam (no English words: minute = നിമിഷം,
# alarm = ഉണർത്തുമണി). "{name}" is the stop. The app shows the same words,
# and the phone's own voice says them when there is no clip.
LINES = {
    "prewake": "ശ്രദ്ധിക്കുക. {name} അടുത്തുവരുന്നു. രണ്ടു നിമിഷത്തിനുള്ളിൽ ഉണർത്തുമണി മുഴങ്ങും.",
    "wake": "ഉണരൂ! നിങ്ങൾ ഇറങ്ങേണ്ട സ്ഥലം, {name}, അടുത്തെത്തി. ഇറങ്ങാൻ ഒരുങ്ങുക.",
    "wake_change": "ഉണരൂ! {name} അടുത്തെത്തി. അവിടെ ഇറങ്ങി മാറിക്കയറണം.",
    "soon": "രണ്ടു നിമിഷത്തിനുള്ളിൽ {name} എത്തും. ഇറങ്ങാൻ ഒരുങ്ങുക.",
    "soon_change": "രണ്ടു നിമിഷത്തിനുള്ളിൽ {name} എത്തും. അവിടെ ഇറങ്ങി മാറിക്കയറണം.",
    "arrive": "{name} എത്തി. ദയവായി ഇവിടെ ഇറങ്ങുക.",
    "arrive_change": "{name} എത്തി. ഇവിടെ ഇറങ്ങി മാറിക്കയറുക.",
    "leave": "വണ്ടി പിടിക്കാൻ പുറപ്പെടാൻ നേരമായി.",
}


def _split(template):
    """A line's pieces in order: phrase ids ("wake.0") and "name"."""
    parts, phrases = [], {}
    chunks = template.split("{name}")
    for i, chunk in enumerate(chunks):
        # The comma after a name belongs to the pause, not the next phrase.
        text = chunk.strip() if i == 0 else chunk.lstrip(" ,").strip()
        if text:
            pid = "%d" % len(phrases)
            phrases[pid] = text
            parts.append(pid)
        if i < len(chunks) - 1:
            parts.append("name")
    return parts, phrases


PARTS = {}      # kind -> ["wake.0", "name", "wake.1"]
PHRASES = {}    # "wake.0" -> "ഉണരൂ! നിങ്ങൾ ഇറങ്ങേണ്ട സ്ഥലം,"
for _kind, _template in LINES.items():
    _parts, _phrases = _split(_template)
    PARTS[_kind] = [p if p == "name" else "%s.%s" % (_kind, p) for p in _parts]
    PHRASES.update({"%s.%s" % (_kind, k): v for k, v in _phrases.items()})

# A stop name may hold letters (English or Malayalam), digits, spaces and a
# little punctuation - nothing that could turn a line into a speech of its own.
_NAME_OK = re.compile(r"^[A-Za-z0-9ഀ-ൿ .,'()&/\-]+$")

_lock = threading.Lock()            # one generation at a time: no double charges
_spent = {"day": "", "chars": 0}


class VoiceError(Exception):
    """A line that cannot be made, with a message for the caller."""


def enabled() -> bool:
    return bool(settings.get("VAZHI_ELEVENLABS_KEY"))


def _clean(name):
    name = " ".join(str(name or "").split())
    return name if name and len(name) <= MAX_NAME and _NAME_OK.match(name) else ""


def spoken_name(name: str = "", name_ml: str = "") -> str:
    """The name to say: the stop's Malayalam name when the map has one."""
    return _clean(name_ml) or _clean(name)


def line(kind: str, name: str = "", name_ml: str = "") -> str:
    """The whole words of one announcement."""
    if kind not in LINES:
        raise VoiceError("Unknown announcement.")
    template = LINES[kind]
    if "{name}" not in template:
        return template
    use = spoken_name(name, name_ml)
    if not use:
        raise VoiceError("Missing or unusable stop name.")
    return template.replace("{name}", use)


def pieces(kind: str, name: str = "", name_ml: str = ""):
    """The announcement as [(text, is_phrase)], in speaking order."""
    line(kind, name, name_ml)                       # same checks as the words
    use = spoken_name(name, name_ml)
    return [(use, False) if p == "name" else (PHRASES[p], True) for p in PARTS[kind]]


def _path(text: str) -> str:
    voice = settings.get("VAZHI_ELEVENLABS_VOICE") or DEFAULT_VOICE
    model = settings.get("VAZHI_ELEVENLABS_MODEL") or DEFAULT_MODEL
    key = "%s|%s|%s" % (voice, model, text)
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".mp3")


def version() -> str:
    """A short tag for the voice and model in use. The app puts it in each
    clip's address, so phones fetch new clips when the voice is changed
    instead of keeping the old ones for a month."""
    voice = settings.get("VAZHI_ELEVENLABS_VOICE") or DEFAULT_VOICE
    model = settings.get("VAZHI_ELEVENLABS_MODEL") or DEFAULT_MODEL
    return hashlib.sha1(("%s|%s" % (voice, model)).encode("utf-8")).hexdigest()[:8]


def cached(text: str):
    """The saved clip for `text`, or None."""
    try:
        with open(_path(text), "rb") as fh:
            return fh.read()
    except OSError:
        return None


def missing(parts) -> bool:
    """Whether any piece still has to be made (and so costs characters)."""
    return any(cached(text) is None for text, _ in parts)


def _budget_ok(chars: int) -> bool:
    day = time.strftime("%Y-%m-%d")
    if _spent["day"] != day:
        _spent.update(day=day, chars=0)
    try:
        cap = int(settings.get("VAZHI_VOICE_DAILY_CHARS") or 300)
    except ValueError:
        cap = 300
    return _spent["chars"] + chars <= cap


def _request(text: str) -> bytes:
    """Ask ElevenLabs to speak `text`; the MP3 bytes."""
    voice = settings.get("VAZHI_ELEVENLABS_VOICE") or DEFAULT_VOICE
    body = json.dumps({"text": text,
                       "model_id": settings.get("VAZHI_ELEVENLABS_MODEL") or DEFAULT_MODEL})
    req = urllib.request.Request(API % voice, data=body.encode("utf-8"), method="POST", headers={
        "xi-api-key": settings.get("VAZHI_ELEVENLABS_KEY"),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read()


def clip(text: str, phrase: bool = False):
    """(mp3 bytes, made_now) for one piece: the saved clip, or a new one from
    ElevenLabs. Stop names count against the day's allowance; the fixed
    phrases (`phrase`) do not, as there are only a few and each is made once.
    VoiceError when there is no key, the allowance is spent, or ElevenLabs
    does not answer - the app then uses the phone's voice."""
    data = cached(text)
    if data:
        return data, False
    if not enabled():
        raise VoiceError("Voice clips are not switched on.")
    with _lock:
        data = cached(text)               # made by another request meanwhile
        if data:
            return data, False
        if not phrase and not _budget_ok(len(text)):
            raise VoiceError("Today's voice allowance is used up.")
        try:
            data = _request(text)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            print("  ElevenLabs refused a line: HTTP %s %s" % (exc.code, detail), flush=True)
            raise VoiceError("The voice service refused the line.") from None
        except Exception as exc:  # noqa: BLE001 - timeouts, no internet
            print("  ElevenLabs did not answer: %s" % exc, flush=True)
            raise VoiceError("The voice service did not answer.") from None
        if not data or len(data) < 100:
            raise VoiceError("The voice service sent no sound.")
        if not phrase:
            _spent["chars"] += len(text)
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _path(text)
        tmp = "%s.%d.tmp" % (path, threading.get_ident())
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        return data, True


# ---- joining MP3 pieces --------------------------------------------------
#
# MP3 is a run of independent frames, so pieces made with the same settings
# can be joined end to end. Only the headers that describe a whole file are
# dropped: ID3 tags, and the "Xing"/"Info" frame that tells players a file's
# length (left in, a player could stop after the first piece).

_BITRATES = {  # kbps for MPEG-1 and MPEG-2/2.5, Layer III
    1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _frame_len(data, i):
    """Length of the Layer III frame starting at data[i], or 0."""
    if i + 4 > len(data) or data[i] != 0xFF or (data[i + 1] & 0xE0) != 0xE0:
        return 0
    ver = (data[i + 1] >> 3) & 3            # 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5
    layer = (data[i + 1] >> 1) & 3          # 1 = Layer III
    bidx, ridx = data[i + 2] >> 4, (data[i + 2] >> 2) & 3
    if ver == 1 or layer != 1 or bidx in (0, 15) or ridx == 3:
        return 0
    rate = _RATES[ver][ridx]
    bitrate = _BITRATES[1 if ver == 3 else 2][bidx] * 1000
    pad = (data[i + 2] >> 1) & 1
    return (144 if ver == 3 else 72) * bitrate // rate + pad


def _frames(data: bytes) -> bytes:
    """The audio frames of one MP3, without its file-wide headers."""
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:          # ID3v2 tag at the front
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        i = 10 + size + (10 if data[5] & 0x10 else 0)
    end = len(data) - 128 if data[-128:-125] == b"TAG" else len(data)
    while i < end and not _frame_len(data, i):        # find the first frame
        i += 1
    n = _frame_len(data, i)
    if n and (b"Xing" in data[i:i + 48] or b"Info" in data[i:i + 48]
              or data[i + 36:i + 40] == b"VBRI"):
        i += n                                          # the file-length frame
    return data[i:end]


def _gain(data, i):
    """The loudness of a mono MPEG-1 frame: its larger "global gain" (the
    quantiser step, high for speech, about 40 lower for silence). None for
    other kinds of frame."""
    if (data[i + 1] >> 3) & 3 != 3 or (data[i + 3] >> 6) != 3:
        return None
    side = data[i + 4 + (0 if data[i + 1] & 1 else 2):][:16]
    if len(side) < 16:
        return None
    word = int.from_bytes(side, "big")

    def field(offset, width):
        return (word >> (128 - offset - width)) & ((1 << width) - 1)
    return max(field(39, 8), field(98, 8))      # granules 0 and 1


TAIL_FRAMES = 4          # silence kept after a piece: ~0.1 s, a natural joint
QUIET = 28               # this far under a piece's loudest frame is silence


def _trim_tail(frames: bytes) -> bytes:
    """A piece without the half second of silence ElevenLabs leaves at its
    end, so "വൈറ്റില" runs on into "എത്തി" as one sentence. Untouched when
    the frames cannot be read."""
    starts, gains, i = [], [], 0
    while i < len(frames):
        n = _frame_len(frames, i)
        if not n:
            break
        starts.append(i)
        gains.append(_gain(frames, i))
        i += n
    if len(gains) < 12 or None in gains:
        return frames
    body = gains[:-2]                       # the encoder's last frames are noise
    loud = max(body) - QUIET
    last = max(k for k, g in enumerate(body) if g >= loud)
    keep = last + 1 + TAIL_FRAMES
    return frames if keep >= len(starts) else frames[:starts[keep]]


def join(clips) -> bytes:
    """One MP3 from several pieces, in order, with the pauses between them
    shortened."""
    out = [_frames(c) for c in clips]
    return b"".join([_trim_tail(f) for f in out[:-1]] + out[-1:])


def announcement(parts):
    """(mp3 bytes, made_now) for a whole line, from its pieces."""
    made = False
    out = []
    for text, phrase in parts:
        data, new = clip(text, phrase)
        made = made or new
        out.append(data)
    return join(out), made
