# MUTANT 4b: the receipt trap — delete the resident gate AND have the resident
# path raise for a DIFFERENT reason (as if a later verify caught it). A bare
# assertRaises would stay green; the reason assertion must catch it.
p='kin_diary/agora/store.py'; s=open(p).read()
old='''        if kind == "resident":
            if self.steward_key_id is None:
                raise AgoraError("no steward configured")
            if ((payload.get("steward_key_id") or "").lower()
                    != self.steward_key_id):
                raise AgoraError("only this node's steward can add residents")
'''
new='''        if kind == "resident":
            raise AgoraError("some unrelated validation failure")  # MUTANT 4b
'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[4b applied]")
