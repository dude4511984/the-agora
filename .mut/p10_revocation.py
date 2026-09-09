p='kin_diary/agora/events.py'; s=open(p).read()
old='    _normalize_hex(r, "visitor_key_id", "issuer_key_id", "signature")'
assert old in s, "anchor missing for revocation"
open(p,'w').write(s.replace(old, '    pass  # MUTANT 10-revocation: normalization skipped', 1))
print("[10-revocation applied]")
