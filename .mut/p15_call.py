# MUTANT P15: drop the growth-vacate _reset_rotation() call (line ~376, the one
# under the "New house, new wheel" comment). If the suite stays GREEN, the call
# is dead there — rotation_holder is provably None under the seated-Speaker
# guard and declines are already empty — confirming it is defensive, not live.
p='kin_diary/agora/node.py'; s=open(p).read()
old='''            # New house, new wheel. Declines recorded by the old electorate are
            # not answers from this one, and a holder seated by the smaller
            # house has not been offered the chair by the larger.
            self._reset_rotation()
        self.add_resident(resident["author"], key_id)'''
new='''            pass  # MUTANT: growth-vacate reset removed
        self.add_resident(resident["author"], key_id)'''
assert old in s, "growth-vacate anchor not found"
open(p,'w').write(s.replace(old,new)); print("[p15_call applied]")
