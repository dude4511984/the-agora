# MUTANT P13: drop the dead speaker-default append. Nothing reads it (the only
# reader of self.log filters event=="resident"). Suite must stay GREEN,
# confirming the write is dead and safe to remove.
p='kin_diary/agora/node.py'; s=open(p).read()
old='        self.log.append({"event": "speaker-default", "speaker": author})\n'
assert old in s, "speaker-default anchor not found"
open(p,'w').write(s.replace(old,'',1)); print("[p13_del applied]")
