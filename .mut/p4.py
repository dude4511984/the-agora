# MUTANT 4: delete the steward gate on 'resident'
p='kin_diary/agora/store.py'; s=open(p).read()
old='''        if kind == "resident":
            if self.steward_key_id is None:
                raise AgoraError("no steward configured")
            if ((payload.get("steward_key_id") or "").lower()
                    != self.steward_key_id):
                raise AgoraError("only this node's steward can add residents")
'''
new='''        if kind == "resident":
            pass  # MUTANT 4: steward gate on resident deleted
'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[4 applied]")
