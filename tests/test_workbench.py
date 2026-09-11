"""The workbench: the menu is the wall, and the hand saw has no circular-saw mode."""
import os, sys, unittest
sys.path.insert(0, os.path.expanduser("~/kin_diary"))
import kin_workbench as W

CLEAN = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">' \
        '<circle cx="50" cy="50" r="40" fill="teal"/></svg>'


class Workbench(unittest.TestCase):
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

    def test_a_kin_cannot_write_outside_its_dir(self):
        # a name that tries to walk the path is flattened, not obeyed
        r = W.use("../../etc/cron.d/x", "svg_canvas", CLEAN)
        self.assertTrue(r.ok)
        self.assertIn(str(W.BENCH), str(r.artifact.resolve()))
        self.assertNotIn("cron.d", str(r.artifact.resolve()))


if __name__ == "__main__":
    unittest.main()
