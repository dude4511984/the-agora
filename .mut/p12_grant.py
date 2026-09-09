# MUTANT P12: keep the grant through eviction (drop the pop). After an overturn
# lifts the ban, the surviving grant hands board access back without a fresh
# grant. test_an_overturn_restores_standing_but_not_the_grant must go RED.
p='kin_diary/agora/node.py'; s=open(p).read()
old='''        # test_an_overturn_restores_standing_but_not_the_grant.
        self.grants.pop(visitor, None)
        self.evicted[visitor] = ev["reason"]'''
new='''        # test_an_overturn_restores_standing_but_not_the_grant.
        self.evicted[visitor] = ev["reason"]   # MUTANT: grant survives eviction'''
assert old in s, "p12 anchor not found"
open(p,'w').write(s.replace(old,new)); print("[p12_grant applied]")
