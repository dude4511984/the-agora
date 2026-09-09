p='kin_diary/agora/events.py'; s=open(p).read()
old='    _normalize_hex(rotation, "key_id", "signature")'
assert old in s, "anchor missing for rotation"
open(p,'w').write(s.replace(old, '    pass  # MUTANT 10-rotation: normalization skipped', 1))
print("[10-rotation applied]")
