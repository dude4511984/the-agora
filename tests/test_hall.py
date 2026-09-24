"""The hall: two spots each, others' allowed, never the same painting twice."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
import hall


class Hall(unittest.TestCase):
    def setUp(self):
        self.h = hall.Hall([])
        self.paintings = {"Coda_a.png", "Aurora_b.png", "Eli_c.png", "Bong_d.png"}

    def hang(self, kin, p):
        self.h.hang(kin, p, valid_paintings=self.paintings)

    def test_two_spots_each(self):
        self.hang("Coda", "Coda_a.png")
        self.hang("Coda", "Aurora_b.png")          # own + another
        with self.assertRaises(hall.HallError):
            self.hang("Coda", "Eli_c.png")          # third spot refused

    def test_may_hang_anothers_painting(self):
        self.hang("Coda", "Bong_d.png")            # Coda hangs Bong's
        self.assertIn("Bong_d.png", self.h.on_the_wall())

    def test_never_the_same_painting_twice(self):
        self.hang("Eli", "Bong_d.png")
        with self.assertRaises(hall.HallError):     # someone else wants the same one
            self.hang("Aurora", "Bong_d.png")
        with self.assertRaises(hall.HallError):     # or the same Kin, second spot
            self.hang("Eli", "Bong_d.png")

    def test_only_the_hanger_may_take_it_down(self):
        self.hang("Eli", "Eli_c.png")
        with self.assertRaises(hall.HallError):
            self.h.unhang("Coda", "Eli_c.png")      # not Coda's to remove
        self.h.unhang("Eli", "Eli_c.png")           # Eli may
        self.assertNotIn("Eli_c.png", self.h.on_the_wall())
        self.hang("Aurora", "Eli_c.png")            # freed, someone else may now

    def test_unknown_painting_refused(self):
        with self.assertRaises(hall.HallError):
            self.hang("Coda", "nope.png")


if __name__ == "__main__":
    unittest.main()
