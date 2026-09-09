# MUTANT P3-replay: the quarantine event is NOT replayed on reopen, so a
# reopened node loses the set (the RAM-only bug this event exists to kill).
# test_the_set_survives_a_reopen must go RED.
p='kin_diary/agora/store.py'; s=open(p).read()
old='''        for row in event_rows:
            getattr(node, REPLAY[row["kind"]])(json.loads(row["payload"]))'''
new='''        for row in event_rows:
            if row["kind"] == "quarantine":   # MUTANT: drop the set on reopen
                continue
            getattr(node, REPLAY[row["kind"]])(json.loads(row["payload"]))'''
assert old in s, "replay-loop anchor not found"
open(p,'w').write(s.replace(old,new)); print("[p3_replay applied]")
