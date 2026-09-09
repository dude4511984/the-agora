p='kin_diary/agora/node.py'; s=open(p).read()
old='''        return (len(self.residents) >= 2
                and self.speaker_key_id is None
                and self.rotation_holder is None)'''
new='''        return False  # MUTANT 8'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[8 applied]")
