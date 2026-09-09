# MUTANT 8b: is_paused ignores the rotation holder — a held house reports paused
p='kin_diary/agora/node.py'; s=open(p).read()
old='''        return (len(self.residents) >= 2
                and self.speaker_key_id is None
                and self.rotation_holder is None)'''
new='''        return (len(self.residents) >= 2
                and self.speaker_key_id is None)  # MUTANT 8b: holder term dropped'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[8b applied]")
