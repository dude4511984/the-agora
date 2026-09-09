# Mutation harness — and the two ways it can measure nothing

`run.sh <name> <patch.py>` builds a scratch copy of the CURRENT working tree,
applies one Python patch that rewrites `kin_diary/agora/*.py` in place, and
runs the suite. A mutant that leaves the suite green is either furniture (no
test asserts the behavior) or — the danger this file is about — a mutant that
was never actually under test. Two traps produce the second, and both look
identical to a real green.

## Trap 1 — the sys.path trap (P17, hit in butter item 1)

Every test file does `sys.path.insert(0, os.path.expanduser("~/kin_diary"))`.
So a test run from inside the scratch tree still imports the UNMUTATED real
package. The mutant sits in the scratch, untouched, while the tests exercise
the shipping code. Green, and a lie.

**Guardrail:** `run.sh` repoints that literal at the scratch root in every
test file (the `[repointed N test files at scratch]` line), and then prints
`[loads: <scratch>/.../store.py]` — proof that the file under test is the
mutated one, not `~/kin_diary`'s. If that line ever shows a real path, stop.
(Copilot's `mutate.py` solves the same trap with `HOME=<scratch>`.)

## Trap 2 — the untracked-omission trap (found 2026-09-04, P3 verify)

`run.sh` built the scratch file list from `git ls-files` + `git diff
--name-only`. Both miss UNTRACKED files. A brand-new killing test — the normal
state of a test written minutes before its own commit — was therefore absent
from the scratch. The mutant "survived" against a killer that wasn't in the
room. The first `p3_replay` run showed 318 tests, not 329: the 11 quarantine
tests, silently gone, and a green that meant nothing.

This is the same family as the two cases in `tests/test_suite_integrity.py`:
a test absent without going red.

**Fix:** the file list now includes
`git ls-files --others --exclude-standard`.

**Guardrail:** the COUNT-PARITY check. Before applying any mutant, `run.sh`
collects the test count in the real tree and in the scratch baseline and
ABORTS (exit 5) if they differ — "a mutant would measure nothing." Demonstrated
2026-09-04: drop the `--others` clause with an untracked test present and the
harness aborts (`scratch collected 331, real tree has 333`) instead of running
a hollow pass.

## The rule under both

The thing you mutate must be the thing that loads, and the killer must be in
the room. `run.sh` now asserts both out loud. A green is evidence only after
you have seen `[loads: <scratch>...]` and `[count parity OK: N ...]`.
