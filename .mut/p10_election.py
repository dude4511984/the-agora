p='kin_diary/agora/events.py'; s=open(p).read()
old='    _normalize_hex(election, "speaker_key_id")'
assert old in s, "anchor missing for election"
open(p,'w').write(s.replace(old, '    pass  # MUTANT 10-election: normalization skipped', 1))
print("[10-election applied]")
