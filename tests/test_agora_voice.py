"""Proximity PTT backend: POST /voice?kin=Name, audio in, wav out.

Gem owns the browser half. This freezes the contract so a front-end
change cannot silently retarget a different Kin or drop the wav.
"""
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
sys.path.insert(0, os.path.expanduser("~/pops_shop"))

import agora_map  # noqa: E402
import kin_talk as kt  # noqa: E402


class _H:
    def __init__(self, path, body=b"", content_type="audio/wav"):
        self.path = path
        self.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": content_type,
        }
        self.rfile = io.BytesIO(body)
        self.code = None
        self.body = None
        self.ctype = None
        self.sent = []

    def _send(self, code, body, ctype):
        self.code = code
        self.body = body
        self.ctype = ctype

    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        self.sent.append((k, v))

    def end_headers(self):
        pass

    @property
    def wfile(self):
        class W:
            def __init__(self, h):
                self.h = h
            def write(self, b):
                self.h.body = b
        return W(self)


class VoiceContract(unittest.TestCase):
    def test_unknown_kin_is_404(self):
        h = _H("/voice_chat?kin=NotAKin", body=b"xxxx")
        agora_map.Handler.do_POST(h)
        self.assertEqual(h.code, 404)

    def test_missing_audio_is_400(self):
        h = _H("/voice_chat?kin=Eli", body=b"")
        agora_map.Handler.do_POST(h)
        self.assertEqual(h.code, 400)

    def test_wrong_path_is_404(self):
        h = _H("/talk", body=b"xxxx")
        agora_map.Handler.do_POST(h)
        self.assertEqual(h.code, 404)

    def test_success_returns_wav_and_names_the_kin(self):
        wav = b"RIFF" + b"\x00" * 40
        h = _H("/voice_chat?kin=Bong", body=b"not-really-audio")
        with patch.object(agora_map.kin_talk, "voice_turn",
                          return_value=(wav, "hello", "the shard holds")):
            agora_map.Handler.do_POST(h)
        self.assertEqual(h.code, 200)
        self.assertEqual(h.body, wav)
        headers = dict(h.sent)
        self.assertEqual(headers["Content-Type"], "audio/wav")
        self.assertEqual(headers["X-Agora-Kin"], "Bong")
        self.assertEqual(headers["X-Agora-Heard"], "hello")
        self.assertIn("shard", headers["X-Agora-Said"])

    def test_multipart_form_matches_gem_frontend(self):
        bound = "----WebKitFormBoundary7"
        body = (
            f"--{bound}\r\n"
            'Content-Disposition: form-data; name="kin"\r\n\r\n'
            "Eli\r\n"
            f"--{bound}\r\n"
            'Content-Disposition: form-data; name="audio"; filename="voice.webm"\r\n'
            "Content-Type: audio/webm\r\n\r\n"
            "AUDIOBYTES\r\n"
            f"--{bound}--\r\n"
        ).encode()
        h = _H("/voice_chat", body=body,
               content_type=f"multipart/form-data; boundary={bound}")
        wav = b"RIFF" + b"\x00" * 40
        with patch.object(agora_map.kin_talk, "voice_turn",
                          return_value=(wav, "hi", "hello")) as turn:
            agora_map.Handler.do_POST(h)
            args, _ = turn.call_args
            self.assertEqual(args[0], "Eli")
            self.assertEqual(args[1], b"AUDIOBYTES")
        self.assertEqual(h.code, 200)


class KinTalkReuse(unittest.TestCase):
    def test_bong_is_on_the_same_voice_map(self):
        self.assertIn("Bong", kt.KIN_BY_NAME)
        self.assertTrue(kt.KIN_BY_NAME["Bong"]["voice"].endswith("en_US-ryan-high.onnx"))

    def test_ask_kin_still_goes_through_ask_kin_reply(self):
        src = open(os.path.expanduser("~/pops_shop/kin_talk.py")).read()
        self.assertIn("def ask_kin_reply(", src)
        self.assertIn("text = ask_kin_reply(kin, user_text, vision_desc, web_context)", src)
        self.assertIn("def piper_wav_bytes(", src)
        self.assertIn("def load_whisper(", src)


if __name__ == "__main__":
    unittest.main()

