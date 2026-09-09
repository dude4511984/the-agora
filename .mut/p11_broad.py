# MUTANT P11: restore the broad `except Exception: 404` for the artifact route,
# so an unexpected fault (RuntimeError) is served as the byte-identical denial.
# test_an_unexpected_fetch_fault_is_a_visible_500_not_a_denial must go RED.
p='kin_diary/agora/wire.py'; s=open(p).read()
old='''                except ArtifactTooLarge as exc:
                    print(f"artifact store failure: {type(exc).__name__}",
                          file=sys.stderr)
                    self._send(500, {"error": "artifact store failure"})
                    return
                except Exception as exc:
                    print(f"artifact store failure: {type(exc).__name__}",
                          file=sys.stderr)
                    self._send(500, {"error": "artifact store failure"})
                    return'''
new='''                except Exception:   # MUTANT: our bug as their denial
                    self._send(404, {"error": "artifact unavailable"})
                    return'''
assert old in s, "p11 broad anchor not found"
open(p,'w').write(s.replace(old,new)); print("[p11_broad applied]")
