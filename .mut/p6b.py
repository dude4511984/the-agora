# MUTANT 6b: keep the vacate, delete ONLY the rotation reset
p='kin_diary/agora/node.py'; s=open(p).read()
old='''            self.speaker_key_id = None
            self.speaker = None
            # New house, new wheel. Declines recorded by the old electorate are
            # not answers from this one, and a holder seated by the smaller
            # house has not been offered the chair by the larger.
            self._reset_rotation()'''
new='''            self.speaker_key_id = None
            self.speaker = None
            pass  # MUTANT 6b: rotation NOT reset on growth-vacate'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[6b applied]")
