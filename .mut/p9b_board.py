# MUTANT P9b: Node.read copies the WHOLE board, ignoring max_entries. A board of
# 201 read with 200 returns 201. test_read_returns_only_the_newest_max_entries
# must go RED.
p='kin_diary/agora/node.py'; s=open(p).read()
old='        for e in self.boards[board][-max_entries:] if max_entries else []:'
new='        for e in self.boards[board]:   # MUTANT: ignore the window'
assert old in s, "board window anchor not found"
open(p,'w').write(s.replace(old,new)); print("[p9b_board applied]")
