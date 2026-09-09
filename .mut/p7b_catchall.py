# MUTANT P7b-B: widen record's catch from ValueError to Exception. Now an
# internal KeyError/AttributeError bug inside record is masked as 403 event
# refused instead of surfacing as 500. test_record_programming_fault_is_a_
# visible_server_error must go RED.
p='kin_diary/agora/wire.py'; s=open(p).read()
old='''                try:
                    self.store.record(routes[self.path], event)
                except ValueError:
                    self._send(403, {"error": "event refused"})
                    return'''
new='''                try:
                    self.store.record(routes[self.path], event)
                except Exception:   # MUTANT: catch-all masks our bugs as 403
                    self._send(403, {"error": "event refused"})
                    return'''
assert old in s, "record-wrap anchor not found"
open(p,'w').write(s.replace(old,new)); print("[p7b_catchall applied]")
