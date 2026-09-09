# MUTANT P8-fetch: fetch_peer_notices uses url-or-pinned without the compare, so
# a caller URL override is honoured (the one-shot redirect).
# test_fetch_notices_refuses_an_unauthorized_url_override must go RED.
p='kin_diary/agora/federation.py'; s=open(p).read()
old='''    pinned_url = pinned.get("url")
    if url is not None and (
        url.rstrip("/") != (pinned_url or "").rstrip("/")
    ):
        raise PeerUrlChanged(
            f"{peer} is pinned at {pinned_url}; URL moves require "
            "reauthorize_peer_url"
        )
    target = pinned_url if url is None else url'''
new='''    target = url or pinned.get("url")   # MUTANT: honour the override'''
assert old in s, "fetch anchor not found"
open(p,'w').write(s.replace(old,new)); print("[p8_fetch applied]")
