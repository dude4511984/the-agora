#!/usr/bin/env bash
# Repeatable single-mutant harness. Builds a scratch copy of the CURRENT
# working tree (not HEAD — we want uncommitted tests too), repoints the
# hardcoded ~/kin_diary in every test file at the scratch, applies one
# python-patch, verifies the mutant is the file that loads, runs the suite.
#
#   .mut/run.sh <name> <patch.py>
# where patch.py rewrites kin_diary/agora/*.py in place (cwd = scratch root).
set -u
REPO="/home/thedude/kin_diary"
NAME="${1:?name}"; PATCH="$(readlink -f "${2:?patch file}")"
S="/tmp/claude-1000/-home-thedude/7f16d9f9-7bc9-42c8-a823-800ba1e3f631/scratchpad/mut_$NAME"
rm -rf "$S"; mkdir -p "$S"
# copy the working tree (tracked files + current edits), excluding junk
( cd "$REPO" && git ls-files && git diff --name-only && git ls-files --others --exclude-standard ) | sort -u | while read -r f; do
  mkdir -p "$S/$(dirname "$f")"; cp "$REPO/$f" "$S/$f"
done
cd "$S" || exit 3
python3 - <<'PY'
import glob, os
root=os.getcwd(); n=0
for f in glob.glob("tests/*.py"):
    s=open(f).read()
    if 'os.path.expanduser("~/kin_diary")' in s:
        open(f,"w").write(s.replace('os.path.expanduser("~/kin_diary")', repr(root))); n+=1
print(f"[repointed {n} test files at scratch]")
PY
echo "[baseline: unmutated scratch]"
BASE_OUT="$(python3 run_tests.py 2>&1)"; echo "$BASE_OUT" | tail -1
# COUNT-PARITY GUARD (P17, the untracked-omission trap): the scratch must
# collect exactly as many tests as the real tree. If the file list ever drops
# a killing test (as it did when it copied only tracked files), the mutant
# would "survive" against an absent killer — a green that measures nothing.
# Fail loud here instead of lying later.
SCRATCH_N="$(echo "$BASE_OUT" | grep -oE 'Ran [0-9]+ tests' | grep -oE '[0-9]+')"
REAL_N="$(cd "$REPO" && python3 run_tests.py 2>&1 | grep -oE 'Ran [0-9]+ tests' | grep -oE '[0-9]+')"
if [ "$SCRATCH_N" != "$REAL_N" ]; then
  echo "ABORT: scratch collected $SCRATCH_N tests, real tree has $REAL_N."
  echo "       the scratch is missing tests — a mutant would measure nothing."
  echo "       fix the file list at line 15 (git ls-files + diff + others)."
  exit 5
fi
echo "[count parity OK: $SCRATCH_N tests in both real tree and scratch]"
python3 "$PATCH" || { echo "PATCH FAILED"; exit 4; }
python3 - <<PY
import sys; sys.path.insert(0,"tests"); sys.path.insert(0,".")
import unittest; unittest.defaultTestLoader.discover("tests", top_level_dir="tests")
import kin_diary.agora.store as st
print("[loads:", st.__file__.replace("$S","<scratch>"), "]")
PY
echo "[under mutant $NAME]"
python3 run_tests.py 2>&1 | grep -E "^(FAIL|ERROR):|^Ran |^OK|^FAILED"
