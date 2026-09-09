# MUTANT 10: _normalize_hex stops lowercasing
p='kin_diary/agora/events.py'; s=open(p).read()
old='''def _normalize_hex(payload: dict, *fields: str) -> None:
    for field in fields:
        payload[field] = payload[field].lower()'''
new='''def _normalize_hex(payload: dict, *fields: str) -> None:
    return  # MUTANT 10: no normalization'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[10 applied]")
