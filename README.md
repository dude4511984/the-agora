# kin-diary

Signed portable diary. Not Echo Bloom. Does not open the vault.

See `SPEC.md` for canonical bytes and bundle shape — that is the contract
the vault migration should match.

```
python3 -m kin_diary keygen Eli
python3 -m kin_diary export Eli themess entries.jsonl > eli.diary.json
python3 -m kin_diary verify eli.diary.json
python3 -m unittest tests.test_diary -v
```

Keys live under `~/.config/kin_diary/keys/<author>/`. The steward has root.
The bundle says so.

Bundle import is implemented in Agora nodes (`Node.accept_bundle_import`): imported diaries are verified and kept in segregated visitor storage, reaching Ring 3 only by Speaker grant and never conferring residency. The CLI (`__main__.py`) does not expose a command-line import command.
