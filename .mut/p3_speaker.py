# MUTANT P3-speaker: accept_quarantine clears speaker_key_id, unseating a
# quarantined Speaker instead of leaving them seated with powers waiting.
# test_a_quarantined_speaker_stays_seated must go RED.
p='kin_diary/agora/node.py'; s=open(p).read()
old='''            self.quarantined_keys.add(key)
            self.log.append({"event": "quarantine", "key_id": key})'''
new='''            self.quarantined_keys.add(key)
            self.speaker_key_id = None   # MUTANT: unseat a quarantined Speaker
            self.log.append({"event": "quarantine", "key_id": key})'''
assert old in s, "accept_quarantine anchor not found"
open(p,'w').write(s.replace(old,new)); print("[p3_speaker applied]")
