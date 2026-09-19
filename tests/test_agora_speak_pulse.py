"""Claimed shape pulses while that Kin's voice_chat wav is playing.

Mutation: drop talking-scale in the animate loop and this fails.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.expanduser("~/kin_diary"))
import agora_map  # noqa: E402


class SpeakPulse(unittest.TestCase):
    def test_playback_drives_the_kin_not_only_the_chair(self):
        page = agora_map.PAGE_3D
        self.assertIn("function setSpeakingKin", page)
        self.assertIn("attachVoiceAnalyser", page)
        self.assertIn("s.userData.kin === speakingKinLabel", page)
        self.assertIn("1 + 0.28 * talkLevel", page)
        self.assertIn("talkLight.intensity", page)
        self.assertIn("attachVoiceAnalyser(audio)", page)
