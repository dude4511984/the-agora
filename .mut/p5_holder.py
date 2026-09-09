# MUTANT P5-holder: drop `holder` from node_fact_canonical, so it escapes the
# signature again. A forged holder in a signed fact goes undetected ->
# test_a_forged_holder_does_not_verify must go RED.
p='kin_diary/agora/canonical.py'; s=open(p).read()
old='        ("holder", _hex64(holder) if holder else ""),\n'
assert old in s, "holder line not found"
open(p,'w').write(s.replace(old,'',1)); print("[p5_holder applied]")
