# MUTANT 6: delete the growth-vacate block in accept_resident
p='kin_diary/agora/node.py'; s=open(p).read()
old='''        if self.speaker_key_id is not None and (
                key_id not in {k.lower() for k in (self.election or {}).get("electorate", [])}):
            self.speaker_key_id = None
            self.speaker = None
            # New house, new wheel. Declines recorded by the old electorate are
            # not answers from this one, and a holder seated by the smaller
            # house has not been offered the chair by the larger.
            self._reset_rotation()
        self.add_resident(resident["author"], key_id)'''
new='''        self.add_resident(resident["author"], key_id)  # MUTANT 6: no vacate'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[6 applied]")
