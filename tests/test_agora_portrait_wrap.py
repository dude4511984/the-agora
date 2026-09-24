"""A claimed 3D form and a claimed portrait are one object.

Don, 2026-09-18: addPresence was strictly either/or — shape3d replaced the
face instead of wearing it. Mutation: restore the old `createShape3D(s3d)`
branch with no portraitTex and these fail.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agora_map  # noqa: E402


class PortraitWrapsTheClaimedShape(unittest.TestCase):
    def test_shape_and_portrait_are_one_mesh(self):
        page = agora_map.PAGE_3D
        self.assertIn("function createShape3D(params, portraitTex)", page)
        self.assertIn("opts.map = portraitTex", page)
        self.assertIn("placeShape(s3d, tex)", page)
        # the old either/or: a shape landing with no portrait argument
        self.assertNotIn("const obj = createShape3D(s3d);", page)

    def test_a_missing_face_does_not_drop_the_form(self):
        page = agora_map.PAGE_3D
        self.assertIn("() => placeShape(s3d, null)", page)
        self.assertIn("placeShape(s3d, null)", page)


if __name__ == "__main__":
    unittest.main()

