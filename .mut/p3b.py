# MUTANT 3b: the dangerous "fix" — INSERT+commit up front, then on a policy
# raise, a same-connection DELETE that is NOT committed. Same-conn count == 0,
# but the committed row survives into a fresh open.
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
        self.conn.commit()                       # MUTANT 3b: committed up front
        try:
            getattr(node, REPLAY[kind])(payload)
        except AgoraError:
            # "clean up" the bad row — but forget to commit the delete, so the
            # same connection sees 0 while the file keeps the committed insert.
            self.conn.execute(
                "DELETE FROM agora_events WHERE node=? AND seq="
                "(SELECT MAX(seq) FROM agora_events WHERE node=?)",
                (self.node_name, self.node_name))
            raise
        if kind == "appeal" and payload.get("signature") in existing_appeals:
            return
        self._invalidate()'''
assert old in s
open(p,'w').write(s.replace(old,new)); print("[3b applied]")
