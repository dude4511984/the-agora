# MUTANT P7b-A: drop record's local `except ValueError`. A bad-action (valid
# signature) ValueError from canonical now falls to the outer `except
# Exception` -> 500 instead of 403. test_signed_event_with_invalid_action_
# is_a_stable_refusal must go RED (caller error wrongly promoted to a fault).
p='kin_diary/agora/wire.py'; s=open(p).read()
old='''                try:
                    self.store.record(routes[self.path], event)
                except ValueError:
                    self._send(403, {"error": "event refused"})
                    return'''
new='''                self.store.record(routes[self.path], event)   # MUTANT: no ValueError guard'''
assert old in s, "record-wrap anchor not found"
open(p,'w').write(s.replace(old,new)); print("[p7b_noval applied]")
