# MUTANT P9-countcap: drop the 50-notice cap, so 51 notices all reach verify.
# test_more_than_fifty_notices_is_refused_before_verify must go RED.
p='kin_diary/agora/federation.py'; s=open(p).read()
old='''    if len(payload["notices"]) > MAX_NOTICES_PER_FETCH:
        raise PeerResponseTooLarge(
            f"peer returned more than {MAX_NOTICES_PER_FETCH} notices"
        )
'''
assert old in s, "countcap anchor not found"
open(p,'w').write(s.replace(old,'',1)); print("[p9_countcap applied]")
