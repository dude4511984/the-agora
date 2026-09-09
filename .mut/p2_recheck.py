# MUTANT P2-recheck: revert .lower() on a frozen membership site (the original
# 10b bug shape: raw key against the lowercase-keyed self.evicted). The AST
# freeze test must go RED, naming the site.
p='kin_diary/agora/node.py'; s=open(p).read()
old='        if key.lower() in self.evicted:'
new='        if key in self.evicted:   # MUTANT: raw membership, 10b re-entry'
assert old in s, "evicted membership anchor not found"
open(p,'w').write(s.replace(old,new,1)); print("[p2_recheck applied]")
