# MUTANT 5: delete the steward gate on 'ruling'
p='kin_diary/agora/store.py'; s=open(p).read()
old='''        if kind == "ruling":
            if self.steward_key_id is None:
                raise AgoraError("no steward configured")
            if ((payload.get("steward_key_id") or "").lower()
                    != self.steward_key_id):
                raise AgoraError("only this node's steward can rule on an appeal")
'''
new='''        if kind == "ruling":
            pass  # MUTANT 5: steward gate on ruling deleted
'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[5 applied]")
