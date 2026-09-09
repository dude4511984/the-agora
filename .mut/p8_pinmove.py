# MUTANT P8-pinmove: drop pin_peer's URL-lock so a same-key different-URL falls
# through to INSERT OR REPLACE and the pin silently moves.
# test_a_pinned_url_does_not_move_on_a_second_discover_elsewhere must go RED.
p='kin_diary/agora/store.py'; s=open(p).read()
old='''            if existing:
                old_url = (existing["url"] or "").rstrip("/")
                if url is None:
                    return
                new_url = url.rstrip("/")
                if old_url != new_url:
                    raise AgoraError(
                        f"{peer} is pinned at {existing['url']}; URL moves "
                        "require reauthorize_peer_url"
                    )
                return
'''
assert old in s, "pinmove anchor not found"
open(p,'w').write(s.replace(old,'',1)); print("[p8_pinmove applied]")
