# MUTANT 10b: verify_key_intro stops normalizing (the intro caller only)
p='kin_diary/agora/events.py'; s=open(p).read()
old='''    _normalize_hex(intro, "visitor_key_id", "resident_key_id",
                   "sig_visitor", "sig_resident")'''
new='''    pass  # MUTANT 10b: intro normalization skipped'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[10b applied]")
