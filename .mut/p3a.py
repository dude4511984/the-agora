# MUTANT 3a: INSERT before the rule check (naive INSERT-first)
p='kin_diary/agora/store.py'; s=open(p).read()
old='''        getattr(node, REPLAY[kind])(payload)   # raises if the rule says no
        if kind == "appeal" and payload.get("signature") in existing_appeals:
            return
        self.conn.execute(
            "INSERT INTO agora_events(node, kind, payload, recorded_at_unix_ms) "
            "VALUES (?,?,?,?)",
            (self.node_name, kind, json.dumps(payload, sort_keys=True),
             int(time.time() * 1000)),
        )
        self.conn.commit()
        self._invalidate()'''
new='''        self.conn.execute(
            "INSERT INTO agora_events(node, kind, payload, recorded_at_unix_ms) "
            "VALUES (?,?,?,?)",
            (self.node_name, kind, json.dumps(payload, sort_keys=True),
             int(time.time() * 1000)),
        )
        self.conn.commit()
        getattr(node, REPLAY[kind])(payload)   # MUTANT 3a: too late, row is in
        if kind == "appeal" and payload.get("signature") in existing_appeals:
            return
        self._invalidate()'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[3a applied]")
