p='kin_diary/agora/events.py'; s=open(p).read()
old='    _normalize_hex(ev, "visitor_key_id", "speaker_key_id", "signature")'
assert old in s, "anchor missing for eviction"
open(p,'w').write(s.replace(old, '    pass  # MUTANT 10-eviction: normalization skipped', 1))
print("[10-eviction applied]")
