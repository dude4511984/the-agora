p='kin_diary/agora/node.py'; s=open(p).read()
old='''            raise AgoraError(self.PAUSE_REASON if self.rotation_holder is None
                             else self.ROTATED_REASON)'''
new='''            raise AgoraError("frozen for some other reason")  # MUTANT 7b'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[7b applied]")
