# MUTANT P7: remove the pre-dispatch JSON-object shape guard, so malformed
# generic event bodies fall through to json.loads inside _who/record and leak
# JSONDecodeError/AttributeError again. Copilot's new wire tests must go RED.
p='kin_diary/agora/wire.py'; s=open(p).read()
old='''                raw = self._raw_body()
                try:
                    event = json.loads(raw)
                    if not isinstance(event, dict):
                        raise ValueError("event body must be a JSON object")
                except (UnicodeDecodeError, ValueError, TypeError):
                    self._send(403, {"error": "event refused"})
                    return'''
new='''                raw = self._raw_body()
                event = json.loads(raw)   # MUTANT: no shape guard, leak parser errors'''
assert old in s, "P7 shape-guard anchor not found"
open(p,'w').write(s.replace(old,new)); print("[p7_shape applied]")
