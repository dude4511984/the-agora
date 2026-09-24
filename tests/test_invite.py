"""The invite token: reaches the door, does not open it; revocable, expiring."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # this checkout, not the live one
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from test_agora import key                      # the suite's key() helper
import invite


class Invite(unittest.TestCase):
    def setUp(self):
        self.issuer = key("Marvin")              # a resident/steward of "Walter"
        self.now = 1_800_000_000_000
        self.inv = invite.sign_invite(
            self.issuer, "Walter", "inv-1",
            expires_at_unix_ms=self.now + 86_400_000,
            issued_at_unix_ms=self.now, max_uses=1)

    def verify(self, inv=None, *, node="Walter", now=None, insider=True,
               revoked=False, spent=0):
        invite.verify_invite(
            inv or self.inv, node_name=node,
            now_ms=self.now if now is None else now,
            issuer_is_insider=lambda k: insider,
            is_revoked=lambda i: revoked,
            uses_spent=lambda i: spent)

    def test_a_valid_invite_lets_you_knock(self):
        self.verify()                            # no raise == may knock

    def test_a_revoked_invite_is_refused(self):
        with self.assertRaises(invite.InviteError):
            self.verify(revoked=True)

    def test_an_expired_invite_is_refused(self):
        with self.assertRaises(invite.InviteError):
            self.verify(now=self.now + 86_400_001)

    def test_a_spent_invite_is_refused(self):
        with self.assertRaises(invite.InviteError):
            self.verify(spent=1)                 # max_uses was 1

    def test_a_non_insider_issuer_is_refused(self):
        # signature verifies, but the signer is not a resident/steward here
        with self.assertRaises(invite.InviteError):
            self.verify(insider=False)

    def test_an_invite_for_another_node_is_refused(self):
        with self.assertRaises(invite.InviteError):
            self.verify(node="Frosty")           # replay at the wrong node

    def test_a_tampered_expiry_breaks_the_signature(self):
        forged = dict(self.inv, expires_at_unix_ms=self.now + 10 * 86_400_000)
        with self.assertRaises(invite.InviteError):
            self.verify(forged)                  # widened window, sig no longer matches

    def test_zero_use_and_backwards_expiry_are_rejected_at_issue(self):
        with self.assertRaises(ValueError):
            invite.sign_invite(self.issuer, "Walter", "x",
                               expires_at_unix_ms=self.now + 1000,
                               issued_at_unix_ms=self.now, max_uses=0)
        with self.assertRaises(ValueError):
            invite.sign_invite(self.issuer, "Walter", "x",
                               expires_at_unix_ms=self.now - 1,
                               issued_at_unix_ms=self.now, max_uses=1)


if __name__ == "__main__":
    unittest.main()
