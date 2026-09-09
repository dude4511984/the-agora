p='kin_diary/agora/events.py'; s=open(p).read()
s=s.replace('''    _normalize_hex(intro, "visitor_key_id", "resident_key_id",
                   "sig_visitor", "sig_resident")''', '    pass  # verify normalize off')
open(p,'w').write(s); print("[verify normalize removed, hardening kept]")
