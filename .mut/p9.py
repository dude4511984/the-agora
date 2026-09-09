# MUTANT 9: the rotated chair opens the SWORD too (drop the door_only guard)
p='kin_diary/agora/node.py'; s=open(p).read()
old='''            if door_only and self.rotation_holder is not None:
                return'''
new='''            if self.rotation_holder is not None:   # MUTANT 9: sword unsheathed
                return'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[9 applied]")
