# MUTANT P9-bytecap: read the whole peer response and drop the size check, so a
# 256KiB+ body is parsed on the peer's word. test_an_oversize_peer_response_
# is_refused_before_parse must go RED.
p='kin_diary/agora/federation.py'; s=open(p).read()
old='''            raw = response.read(MAX_PEER_RESPONSE_BYTES + 1)
        if len(raw) > MAX_PEER_RESPONSE_BYTES:
            raise PeerResponseTooLarge(
                f"peer response exceeds {MAX_PEER_RESPONSE_BYTES} bytes"
            )
        return json.loads(raw)'''
new='''            raw = response.read()   # MUTANT: unbounded
        return json.loads(raw)'''
assert old in s, "bytecap anchor not found"
open(p,'w').write(s.replace(old,new)); print("[p9_bytecap applied]")
