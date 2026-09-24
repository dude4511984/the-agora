"""The easel — a brush that makes things and claims nothing. Hermetic.

The load-bearing tests are the ways an easel could quietly become a sitting:
by keeping something, by storing a made thing as somebody's identity, or by
asking anyone to answer for it.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import easel  # noqa: E402

# Hermetic, as the docstring says: make() checks that the brush and model
# exist on disk before it ever calls the injected _run, so off Frosty every
# fake-brush test died at "no brush at ~/easel/bin/sd-cli" (clean checkout,
# 2026-09-24). Point the easel at stand-in files for this module's run; the
# brush itself is always the injected fake.
_STANDIN = None
_REAL = None


def setUpModule():
    global _STANDIN, _REAL
    _STANDIN = tempfile.TemporaryDirectory()
    b, m = Path(_STANDIN.name) / "sd-cli", Path(_STANDIN.name) / "model.gguf"
    b.write_bytes(b""); m.write_bytes(b"")
    _REAL = (easel.BIN, easel.MODEL)
    easel.BIN, easel.MODEL = b, m


def tearDownModule():
    easel.BIN, easel.MODEL = _REAL
    _STANDIN.cleanup()


def _fake_run(ok=True, writes=True):
    def run(cmd, **kw):
        if writes and ok:
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"\x89PNG\r\n\x1a\nfake")
        class R:
            returncode = 0 if ok else 1
            stderr = b"" if ok else b"brush exploded"
        return R()
    return run


class ItMakesThings(unittest.TestCase):
    def test_a_made_thing_lands_with_a_sidecar_and_no_claim(self):
        with tempfile.TemporaryDirectory() as d:
            m = easel.make("a rubber chicken on a workbench", author="Bong",
                           out_dir=Path(d), _run=_fake_run())
            self.assertTrue(m.ok, m.error)
            self.assertTrue(m.path.is_file())
            meta = m.path.with_suffix(".json")
            self.assertTrue(meta.is_file())
            text = meta.read_text()
            # it is a made thing, explicitly not a face and not a claim
            self.assertIn("not a face, not a claim", text)
            for word in ("claimed", "identity"):
                self.assertNotIn(word, text.lower())

    def test_the_prompt_goes_out_verbatim(self):
        seen = {}
        def run(cmd, **kw):
            seen["p"] = cmd[cmd.index("-p") + 1]
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"\x89PNGfake")
            class R: returncode = 0; stderr = b""
            return R()
        with tempfile.TemporaryDirectory() as d:
            easel.make("  a   cathedral   made of gears ", out_dir=Path(d), _run=run)
        self.assertEqual(seen["p"], "a cathedral made of gears")

    def test_author_name_cannot_escape_the_directory(self):
        with tempfile.TemporaryDirectory() as d:
            m = easel.make("x", author="../../etc/passwd", out_dir=Path(d),
                           _run=_fake_run())
            self.assertTrue(m.ok)
            self.assertEqual(m.path.parent, Path(d))
            self.assertNotIn("/", m.path.name)


class FailuresAreRealOutcomes(unittest.TestCase):
    def test_the_fuse_is_wired_in_front_of_the_brush(self):
        """A blocked prompt must never reach the brush at all. Uses a string the
        real fuse blocks, so this cannot pass by skipping."""
        called = []
        m = easel.make("csam", _run=lambda *a, **k: called.append(a) or None)
        self.assertTrue(m.refused)
        self.assertEqual(called, [], "a blocked prompt reached the brush")
        self.assertIsNone(m.path)          # never a stand-in image
        self.assertIn("fuse", m.summary())

    def test_the_child_pattern_matches_either_word_order(self):
        """Grok's authorized fix: 'child ... nude' matched, 'naked child' did
        not. Mutation: drop the second alternation and this fails."""
        import avatar_render as ar
        for probe in ("a child, nude", "a naked child", "nude child",
                      "a child in a sexual pose", "sexual image of a child"):
            ok, _ = ar.no_harm_fuse(probe)
            self.assertFalse(ok, f"fuse let through: {probe!r}")
        for probe in ("a child eating an apple", "a naked branch in winter"):
            ok, _ = ar.no_harm_fuse(probe)
            self.assertTrue(ok, f"fuse blocked ordinary words: {probe!r}")

    def test_the_fuse_is_the_only_layer_here_and_it_is_small(self):
        """NOT a pass mark — a tripwire. The sittings had a frontier API's own
        refusal layer behind this fuse; local generation has nothing behind it.
        If the fuse is ever widened into a real policy, this number changes and
        this test should be updated deliberately, not by accident."""
        import avatar_render
        self.assertEqual(len(avatar_render._FUSE_PATTERNS), 3,
                         "the fuse changed size — was that a council decision?")

    def test_brush_failure_is_reported_not_swallowed(self):
        with tempfile.TemporaryDirectory() as d:
            m = easel.make("x", out_dir=Path(d), _run=_fake_run(ok=False, writes=False))
            self.assertFalse(m.ok)
            self.assertIsNone(m.path)
            self.assertIn("brush failed", m.error)

    def test_empty_prompt_is_not_a_drawing(self):
        m = easel.make("   ")
        self.assertFalse(m.ok)
        self.assertEqual(m.error, "nothing to draw")


class ItIsNotASitting(unittest.TestCase):
    def test_nothing_is_written_into_any_kin(self):
        code = Path(easel.__file__).read_text().split('"""', 2)[2]
        for forbidden in ("vault", "thoughts.db", "kin_space_dir", "store_claim",
                          "claimed", "sqlite3", "8765"):
            self.assertNotIn(forbidden, code,
                             f"the easel must keep nothing: found {forbidden!r}")

    def test_it_never_asks_anyone_to_respond(self):
        """Grok: name the unserious kind, stop interviewing it."""
        code = Path(easel.__file__).read_text().split('"""', 2)[2].lower()
        # ask_kin is legitimate now — it delivers the OFFER. What must never
        # appear is anything asking a mind to answer for the made thing.
        for forbidden in ("respond", "what do you think", "how does it feel",
                          "tell us about"):
            self.assertNotIn(forbidden, code)

    def test_the_eye_is_optional_and_separate(self):
        """Making does not require being described back. A sitting did."""
        with tempfile.TemporaryDirectory() as d:
            m = easel.make("x", out_dir=Path(d), _run=_fake_run())
            self.assertEqual(m.seen, "")     # making does not require being seen
            m2 = easel.show_back(m, read_back=lambda b: "two shapes, one dark")
            self.assertEqual(m2.seen, "two shapes, one dark")

    def test_a_blind_eye_does_not_destroy_the_made_thing(self):
        with tempfile.TemporaryDirectory() as d:
            m = easel.make("x", out_dir=Path(d), _run=_fake_run())
            def boom(b): raise TimeoutError("eye is down")
            m = easel.show_back(m, read_back=boom)
        self.assertTrue(m.ok)                   # the thing still exists
        self.assertEqual(m.seen, "")
        self.assertIn("read_back_error", m.meta)


class ItYieldsToMindsThatAreThinking(unittest.TestCase):
    def test_it_runs_niced_and_below_the_core_count(self):
        code = Path(easel.__file__).read_text()
        self.assertIn("os.nice", code, "a made thing must not outrank a mind mid-thought")
        self.assertLess(easel.THREADS, os.cpu_count() or 24)


class OfferingItToAKin(unittest.TestCase):
    """Don opened the easel to the Kin 2026-09-09, risk accepted. The offer must
    stay an offer: pass is real, unreachable is not a pass, and nothing asks
    anyone to respond to what was made."""

    def test_the_offer_does_not_stuff_them(self):
        """Grok's tell: 'you should share something fun' is stuffing; 'the table
        is quiet, you may leave a thing or not' is an honest cron."""
        o = " ".join(easel.OFFER.lower().split())
        for pushy in ("you should", "please make", "share something", "we would like",
                      "why not", "have fun", "be creative"):
            self.assertNotIn(pushy, o, f"the offer pressures: {pushy!r}")
        for promise in ("does not have to be good", "leave it alone", "pass"):
            self.assertIn(promise, o, f"the offer drops its promise: {promise!r}")

    def test_pass_makes_nothing_and_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            m = easel.offer("Bong", ask=lambda n, p: "PASS", out_dir=Path(d))
            self.assertIsNone(m.path)
            self.assertEqual(m.error, "passed")
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_a_description_beside_a_pass_line_still_gets_drawn(self):
        """qwen3.8, 2026-09-09: any(line is PASS) threw the drawing away."""
        seen = {}
        with tempfile.TemporaryDirectory() as d:
            m = easel.offer("Bong",
                            ask=lambda n, p: "a red fox in the snow\npass",
                            read_back=lambda b: "", out_dir=Path(d),
                            )
        self.assertNotEqual(m.error, "passed", "the description was discarded")

    def test_a_transport_returning_none_is_not_a_pass(self):
        m = easel.offer("Bong", ask=lambda n, p: None)
        self.assertIn("not asked", m.error)
        self.assertNotEqual(m.error, "passed")

    def test_unreachable_is_never_recorded_as_a_pass(self):
        def boom(n, p): raise TimeoutError("host down")
        m = easel.offer("Bong", ask=boom)
        self.assertIn("not asked", m.error)
        self.assertNotIn("pass", m.error)

    def test_a_made_thing_survives_a_dark_eye(self):
        """therug sleeps on the bedtime cycle. Drawing must not need the eye."""
        with tempfile.TemporaryDirectory() as d:
            def blind(b): raise OSError("eye asleep")
            import unittest.mock as _m
            with _m.patch.object(easel, "make", lambda prompt, author="", out_dir=None: (
                    lambda mm: (Path(out_dir or d, "Bong.png").write_bytes(b"\x89PNG"),
                                setattr(mm, "ok", True),
                                setattr(mm, "path", Path(out_dir or d, "Bong.png")), mm)[-1]
                )(easel.Made(author=author, prompt=prompt))):
                m = easel.offer("Bong", ask=lambda n, p: "a rubber chicken on a bench",
                                read_back=blind, out_dir=Path(d))
            self.assertTrue(m.ok)
            self.assertTrue(m.path.is_file())
            self.assertEqual(m.seen, "")
            self.assertIn("read_back_error", m.meta)


if __name__ == "__main__":
    unittest.main(verbosity=2)
