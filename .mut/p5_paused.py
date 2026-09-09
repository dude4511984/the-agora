# MUTANT P5-paused: drop `paused` from node_fact_canonical -> a flipped paused
# verifies -> test_a_flipped_paused_does_not_verify must go RED.
p='kin_diary/agora/canonical.py'; s=open(p).read()
old='        ("paused", "true" if paused else "false"),\n'
assert old in s, "paused line not found"
open(p,'w').write(s.replace(old,'',1)); print("[p5_paused applied]")
