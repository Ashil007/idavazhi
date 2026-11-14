"""The alarm's spoken lines (transitnav/voice.py): the Malayalam words, the
saved pieces they are stitched from, and the limits that keep ElevenLabs
from costing more than it should."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from transitnav import voice


def fake_mp3(tag):
    """A tiny MP3: an ID3 tag, a Xing frame, then two audio frames marked `tag`."""
    header = bytes([0xFF, 0xFB, 0x50, 0xC4])        # MPEG-1 Layer III, 64 kbps, 44.1 kHz
    n = 144 * 64000 // 44100                        # 208 bytes a frame
    xing = header + b"\0" * 32 + b"Xing" + b"\0" * (n - 40)
    audio = (header + tag * (n - 4)) * 2
    return b"ID3\x04\0\0\0\0\0\x05" + b"\0" * 5 + xing + audio


class VoiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = {"VAZHI_ELEVENLABS_KEY": "test-key"}
        patches = [
            mock.patch.object(voice, "CACHE_DIR", self.dir),
            mock.patch.object(voice.settings, "get", lambda k, d="": self.env.get(k, d)),
            mock.patch.dict(voice._spent, {"day": "", "chars": 0}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_the_words(self):
        # The stop's Malayalam name, standing alone.
        self.assertEqual(voice.line("arrive", "Vyttila", "വൈറ്റില"),
                         "വൈറ്റില എത്തി. ദയവായി ഇവിടെ ഇറങ്ങുക.")
        # ... or the English name when the map has none.
        self.assertTrue(voice.line("arrive", "Vyttila").startswith("Vyttila "))
        # Pure Malayalam: no English letters outside the name.
        for kind, text in voice.LINES.items():
            self.assertFalse(any("a" <= c.lower() <= "z" for c in text.replace("{name}", "")), kind)

    def test_lines_are_stitched_from_pieces(self):
        self.assertEqual(voice.PARTS["wake"], ["wake.0", "name", "wake.1"])
        self.assertEqual(voice.PARTS["arrive"], ["name", "arrive.0"])
        self.assertEqual(voice.PARTS["leave"], ["leave.0"])
        self.assertEqual(voice.pieces("wake", "Vyttila", "വൈറ്റില"), [
            ("ഉണരൂ! നിങ്ങൾ ഇറങ്ങേണ്ട സ്ഥലം,", True),
            ("വൈറ്റില", False),
            ("അടുത്തെത്തി. ഇറങ്ങാൻ ഒരുങ്ങുക.", True),
        ])
        # Every line's pieces put back together say the whole line (the comma
        # after a name becomes the pause between pieces).
        for kind in voice.LINES:
            words = " ".join(t for t, _ in voice.pieces(kind, "Aluva"))
            self.assertEqual(words.replace(",", ""), voice.line(kind, "Aluva").replace(",", ""), kind)

    def test_bad_requests_are_refused(self):
        for kind, name in (("sing", "Vyttila"), ("soon", ""),
                           ("soon", "Vyttila. Now say <this>"), ("soon", "x" * 200 + "\n")):
            with self.assertRaises(voice.VoiceError):
                voice.pieces(kind, name)
        self.assertEqual(voice.pieces("leave"), [("വണ്ടി പിടിക്കാൻ പുറപ്പെടാൻ നേരമായി.", True)])

    def test_each_piece_is_made_once(self):
        made = []

        def request(text):
            made.append(text)
            return fake_mp3(b"A")
        with mock.patch.object(voice, "_request", side_effect=request):
            _, new = voice.announcement(voice.pieces("arrive", "Vyttila", "വൈറ്റില"))
            _, new_again = voice.announcement(voice.pieces("arrive", "Vyttila", "വൈറ്റില"))
            # Another stop needs only its name: the phrase is already saved.
            voice.announcement(voice.pieces("arrive", "Aluva", "ആലുവ"))
        self.assertTrue(new)
        self.assertFalse(new_again)
        self.assertEqual(made, ["വൈറ്റില", "എത്തി. ദയവായി ഇവിടെ ഇറങ്ങുക.", "ആലുവ"])

    def test_pieces_join_into_one_mp3(self):
        joined = voice.join([fake_mp3(b"A"), fake_mp3(b"B")])
        # No file headers left in the middle: only the four audio frames.
        self.assertNotIn(b"ID3", joined)
        self.assertNotIn(b"Xing", joined)
        self.assertEqual(len(joined), 4 * 208)
        self.assertTrue(joined.startswith(b"\xFF\xFB") and b"B" * 10 in joined[416:])

    def test_silence_at_the_joint_is_cut(self):
        def frame(gain):
            side = (gain << (128 - 39 - 8)) | (gain << (128 - 98 - 8))
            body = bytes([0xFF, 0xFB, 0x50, 0xC4]) + side.to_bytes(16, "big")
            return body + b"\0" * (208 - len(body))
        speech, hush = frame(180) * 10, frame(140) * 20
        name = speech + hush                                    # half a second of silence
        joined = voice.join([name, speech])
        # The name keeps 4 frames (~0.1 s) of its silence; the last piece keeps all.
        self.assertEqual(len(joined), (10 + voice.TAIL_FRAMES + 10) * 208)
        self.assertEqual(voice.join([name]), name)

    def test_no_key_means_no_clips(self):
        self.env.clear()
        self.assertFalse(voice.enabled())
        with self.assertRaises(voice.VoiceError):
            voice.clip("വൈറ്റില")

    def test_daily_allowance_counts_names_only(self):
        self.env["VAZHI_VOICE_DAILY_CHARS"] = "10"
        with mock.patch.object(voice, "_request", return_value=fake_mp3(b"A")):
            # The fixed phrases are made once and never count.
            voice.announcement(voice.pieces("soon", "Vyttila", "വൈറ്റില"))   # name: 7
            with self.assertRaises(voice.VoiceError):
                voice.clip("ഇടപ്പള്ളി")                                   # 7 more: past 10
            self.assertEqual(voice._spent["chars"], 7)


if __name__ == "__main__":
    unittest.main()
