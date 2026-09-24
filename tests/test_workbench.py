"""The workbench: the menu is the wall, and the hand saw has no circular-saw mode."""
import os, shutil, sys, unittest
# The repo this file sits in, not ~/kin_diary (Don's checkout location).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kin_workbench as W

CLEAN = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">' \
        '<circle cx="50" cy="50" r="40" fill="teal"/></svg>'


# Two tests render for real: the renderer is part of what's under test (the
# write-outside-its-dir check goes through a real render). They need the
# system tool rsvg-convert (Debian/Ubuntu: librsvg2-bin). Verified 2026-09-24:
# 7/7 pass on a clean checkout once it's installed; without it they skip.
NEEDS_RSVG = unittest.skipUnless(shutil.which("rsvg-convert"),
                                 "needs rsvg-convert (apt install librsvg2-bin)")

# Renders land in W.BENCH, which is ~/kin_workbench: on Frosty the tests were
# leaving drawings (an Eli/ folder among them) beside the Kin's real ones every
# run (found 2026-09-24). Point the bench at a temp dir for this module.
import tempfile  # noqa: E402
_BENCH_TMP, _REAL_BENCH = None, None


def setUpModule():
    global _BENCH_TMP, _REAL_BENCH
    _BENCH_TMP = tempfile.TemporaryDirectory()
    _REAL_BENCH, W.BENCH = W.BENCH, __import__("pathlib").Path(_BENCH_TMP.name)


def tearDownModule():
    W.BENCH = _REAL_BENCH
    _BENCH_TMP.cleanup()


class Workbench(unittest.TestCase):
    @NEEDS_RSVG
    def test_a_clean_drawing_renders(self):
        r = W.use("Eli", "svg_canvas", CLEAN)
        self.assertTrue(r.ok, r.detail)
        self.assertTrue(r.artifact.is_file())

    def test_an_unknown_tool_is_refused(self):
        r = W.use("Eli", "rm_rf", "anything")
        self.assertFalse(r.ok)
        self.assertIn("no such tool", r.detail)

    def test_a_script_is_refused(self):
        r = W.use("Eli", "svg_canvas",
                  '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
        self.assertFalse(r.ok)

    def test_an_event_handler_is_refused(self):
        r = W.use("Eli", "svg_canvas",
                  '<svg xmlns="http://www.w3.org/2000/svg"><rect onload="x()"/></svg>')
        self.assertFalse(r.ok)

    def test_an_external_fetch_is_refused(self):
        r = W.use("Eli", "svg_canvas",
                  '<svg xmlns="http://www.w3.org/2000/svg">'
                  '<image xlink:href="http://evil/x.png"/></svg>')
        self.assertFalse(r.ok)

    def test_an_xxe_entity_is_refused(self):
        r = W.use("Eli", "svg_canvas",
                  '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                  '<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>')
        self.assertFalse(r.ok)

    @NEEDS_RSVG
    def test_a_kin_cannot_write_outside_its_dir(self):
        # a name that tries to walk the path is flattened, not obeyed
        r = W.use("../../etc/cron.d/x", "svg_canvas", CLEAN)
        self.assertTrue(r.ok)
        self.assertIn(str(W.BENCH), str(r.artifact.resolve()))
        self.assertNotIn("cron.d", str(r.artifact.resolve()))


if __name__ == "__main__":
    unittest.main()
