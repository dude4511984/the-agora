# MUTANT 7: _refuse_if_paused becomes a no-op (the door never freezes)
p='kin_diary/agora/node.py'; s=open(p).read()
import re
# insert an early return at the top of the method body
old='''        if len(self.residents) >= 2 and self.speaker_key_id is None:
            if door_only and self.rotation_holder is not None:
                return
            # "A Speaker OR a decision." A unanimous house decision naming this
            # exact act is the other half, and it permits this act only.
            if act and self._permitted_by_house(act[0], act[1]):
                return
            raise AgoraError(self.PAUSE_REASON if self.rotation_holder is None
                             else self.ROTATED_REASON)'''
new='''        return  # MUTANT 7: pause never refuses
        if len(self.residents) >= 2 and self.speaker_key_id is None:
            if door_only and self.rotation_holder is not None:
                return
            if act and self._permitted_by_house(act[0], act[1]):
                return
            raise AgoraError(self.PAUSE_REASON if self.rotation_holder is None
                             else self.ROTATED_REASON)'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[7 applied]")
