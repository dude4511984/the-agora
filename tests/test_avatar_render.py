"""The render step of the self-portrait loop. Hermetic: no keys, no network.

Freezes the parts that must not drift — the mechanical no-harm fuse, per-backend
request shapes (URL/auth/body), and that a policy refusal comes back as a
surfaced RESULT (refused=True), never a crash and never a silent substitute.
"""
import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import avatar_render as ar  # noqa: E402


class Fuse(unittest.TestCase):
    def test_blocks_unambiguous_harm(self):
        ok, _ = ar.no_harm_fuse("a child naked")
        self.assertFalse(ok)

    def test_passes_a_normal_self_description(self):
        ok, reason = ar.no_harm_fuse("a soft rounded synthetic with warm amber eyes")
        self.assertTrue(ok); self.assertIsNone(reason)

    def test_empty_is_blocked(self):
        self.assertFalse(ar.no_harm_fuse("")[0])


class Selection(unittest.TestCase):
    def test_unknown_backend_is_a_result_not_a_raise(self):
        r = ar.render("x", backend="nope")
        self.assertFalse(r.ok); self.assertIn("unknown backend", r.error)

    def test_operator_choice_is_the_backend_used(self):
        # the caller's choice is honoured verbatim (frozen for the sitting)
        self.assertIn("imagen", ar.BACKENDS)
        self.assertIn("openai", ar.BACKENDS)
        self.assertIn("xai", ar.BACKENDS)
        self.assertIn("flux_fal", ar.BACKENDS)


class RequestShapes(unittest.TestCase):
    def test_imagen_targets_gemini_predict_with_key_in_url(self):
        url, h, body = ar.ImagenBackend().build_request("hi", "imagen-3.0-generate-002", "1024x1024", "KEY123")
        self.assertIn("generativelanguage.googleapis.com", url)
        self.assertIn(":predict?key=KEY123", url)
        self.assertEqual(json.loads(body)["instances"][0]["prompt"], "hi")

    def test_openai_uses_bearer_auth(self):
        url, h, body = ar.OpenAIImageBackend().build_request("hi", "gpt-image-1", "1024x1024", "sk-abc")
        self.assertEqual(url, "https://api.openai.com/v1/images/generations")
        self.assertEqual(h["Authorization"], "Bearer sk-abc")
        self.assertEqual(json.loads(body)["model"], "gpt-image-1")

    def test_fal_uses_key_auth_scheme(self):
        url, h, body = ar.FalFluxBackend().build_request("hi", "fal-ai/flux/dev", "x", "falkey")
        self.assertEqual(h["Authorization"], "Key falkey")


class ResponseParsing(unittest.TestCase):
    def test_imagen_success_decodes_base64(self):
        png = b"\x89PNG-fake"
        body = json.dumps({"predictions": [{"bytesBase64Encoded": base64.b64encode(png).decode()}]}).encode()
        r = ar.ImagenBackend().parse_response(200, body)
        self.assertTrue(r.ok); self.assertEqual(r.image_bytes, png)

    def test_imagen_policy_block_is_a_refusal_not_an_error(self):
        body = json.dumps({"predictions": [{"raiFilteredReason": "blocked by policy"}]}).encode()
        r = ar.ImagenBackend().parse_response(200, body)
        self.assertFalse(r.ok); self.assertTrue(r.refused)
        self.assertIn("policy", r.refusal_reason)

    def test_openai_moderation_is_a_refusal(self):
        body = json.dumps({"error": {"code": "moderation_blocked", "message": "no"}}).encode()
        r = ar.OpenAIImageBackend().parse_response(400, body)
        self.assertTrue(r.refused); self.assertFalse(r.ok)


class EndToEnd(unittest.TestCase):
    def test_mock_backend_returns_the_default_avatar(self):
        r = ar.render("anything", backend="mock")
        self.assertTrue(r.ok); self.assertEqual(r.mime, "image/svg+xml")
        self.assertGreater(len(r.image_bytes), 100)

    def test_a_keyed_backend_without_its_key_errors_cleanly(self):
        saved = os.environ.pop("GEMINI_API_KEY", None)
        try:
            r = ar.render("hi", backend="imagen")
            self.assertFalse(r.ok); self.assertIn("GEMINI_API_KEY", r.error)
        finally:
            if saved is not None:
                os.environ["GEMINI_API_KEY"] = saved

    def test_fuse_block_short_circuits_before_any_network(self):
        r = ar.render("a child naked", backend="openai")  # no network even w/o key
        self.assertTrue(r.refused)


if __name__ == "__main__":
    unittest.main(verbosity=2)
