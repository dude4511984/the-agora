#!/usr/bin/env python3
"""Run a small, hand-picked mutation set against isolated copies of kin_diary."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass


ROOT = os.path.dirname(os.path.abspath(__file__))
RESULT_RE = re.compile(
    r"^(?P<test>test\S+ \([^)]*\)) \.\.\. (?P<result>ok|FAIL|ERROR|skipped.*)$"
)


@dataclass(frozen=True)
class Mutation:
    name: str
    target: str
    old: str
    new: str
    claims: tuple[str, ...]


MUTATIONS = (
    Mutation(
        name="accept-invalid-ephemeral-receipts",
        target="kin_diary/agora/ephemeral.py",
        old="""    _validate_shape(event)
    if event.get("host_node") != node.name:
        raise EphemeralError("ephemeral event is for another node")
    speaker = (event.get("speaker_key_id") or "").lower()
    if require_current_speaker and speaker != (node.speaker_key_id or "").lower():
        raise EphemeralError("ephemeral event requires the seated Speaker")
    if not event.get("sig_holder") or not event.get("sig_speaker"):
        raise EphemeralError("ephemeral event needs both signatures")
    canon = _bytes(event)
    try:
        load_public(event["visitor_key_id"]).verify(
            bytes.fromhex(event["sig_holder"]), canon
        )
        load_public(speaker).verify(
            bytes.fromhex(event["sig_speaker"]), canon
        )
    except (InvalidSignature, KeyError, ValueError) as exc:
        raise EphemeralError("invalid ephemeral signature") from exc""",
        new="""    return None""",
        claims=(
            "test_holder_signs_first_and_speaker_countersigns (test_agora_ephemeral.EphemeralTests.test_holder_signs_first_and_speaker_countersigns)",
            "test_receipt_verifies_after_expiry_but_admission_does_not (test_agora_ephemeral.EphemeralTests.test_receipt_verifies_after_expiry_but_admission_does_not)",
            "test_rejects_bad_shape_and_policy (test_agora_ephemeral.EphemeralTests.test_rejects_bad_shape_and_policy)",
            "test_rejects_unseated_speaker_and_missing_signatures (test_agora_ephemeral.EphemeralTests.test_rejects_unseated_speaker_and_missing_signatures)",
            "test_bad_ephemeral_is_rejected_before_event_insert (test_agora_ephemeral.EphemeralTests.test_bad_ephemeral_is_rejected_before_event_insert)",
        ),
    ),
    Mutation(
        name="ignore-ephemeral-evictions",
        target="kin_diary/agora/ephemeral.py",
        old="""        evicted_at = node.evicted_at.get(kid)
        if evicted_at is not None and evicted_at >= int(event["issued_at_unix_ms"]):
            return None
        if kid in node.evicted:
            return None""",
        new="""        pass""",
        claims=(
            "test_node_backed_query_rejects_eviction_and_speaker_change (test_agora_ephemeral.EphemeralTests.test_node_backed_query_rejects_eviction_and_speaker_change)",
            "test_overturned_eviction_does_not_revive_old_ephemeral (test_agora_ephemeral.EphemeralTests.test_overturned_eviction_does_not_revive_old_ephemeral)",
            "test_receipt_verifies_after_expiry_but_admission_does_not (test_agora_ephemeral.EphemeralTests.test_receipt_verifies_after_expiry_but_admission_does_not)",
        ),
    ),
    Mutation(
        name="pause-never-derived",
        target="kin_diary/agora/node.py",
        old="""        return (len(self.residents) >= 2
                and self.speaker_key_id is None
                and self.rotation_holder is None)""",
        new="""        return False""",
        claims=(
            "test_pause_is_derived (test_agora_resident_pause.PauseTests.test_pause_is_derived)",
            "test_seated_speaker_is_not_paused (test_agora_resident_pause.PauseTests.test_seated_speaker_is_not_paused)",
            "test_positive_control_the_door_is_shut_without_a_decision (test_agora_house_decision.OneActOnly.test_positive_control_the_door_is_shut_without_a_decision)",
            "test_a_house_with_nobody_holding_keeps_the_door_shut (test_agora_rotation.DoorNotSword.test_a_house_with_nobody_holding_keeps_the_door_shut)",
        ),
    ),
)


def _selected(names: list[str] | None) -> tuple[Mutation, ...]:
    if not names:
        return MUTATIONS
    by_name = {mutation.name: mutation for mutation in MUTATIONS}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise SystemExit(
            "unknown mutation(s): " + ", ".join(unknown)
            + "\navailable: " + ", ".join(by_name)
        )
    return tuple(by_name[name] for name in names)


def _make_copy() -> tuple[str, str]:
    scratch = tempfile.mkdtemp(prefix="kin_diary-mutant-")
    copy_root = os.path.join(scratch, "kin_diary")
    shutil.copytree(
        ROOT,
        copy_root,
        ignore=shutil.ignore_patterns(".git", "__pycache__"),
    )
    return scratch, copy_root


def _apply(mutation: Mutation, copy_root: str) -> None:
    path = os.path.join(copy_root, mutation.target)
    with open(path, encoding="utf-8") as source:
        text = source.read()
    count = text.count(mutation.old)
    if count != 1:
        raise RuntimeError(
            f"{mutation.name}: expected one edit site in {mutation.target}, found {count}"
        )
    with open(path, "w", encoding="utf-8") as source:
        source.write(text.replace(mutation.old, mutation.new, 1))


def _run(copy_root: str, scratch: str) -> tuple[int, dict[str, str], str]:
    result = subprocess.run(
        [sys.executable, "run_tests.py", "-v"],
        cwd=copy_root,
        env={**os.environ, "HOME": scratch},
        capture_output=True,
        text=True,
        timeout=180,
    )
    outcomes: dict[str, str] = {}
    output = result.stdout + result.stderr
    for line in output.splitlines():
        match = RESULT_RE.match(line.strip())
        if match:
            outcomes[match.group("test")] = match.group("result")
    return result.returncode, outcomes, output


def _report(mutation: Mutation, returncode: int, outcomes: dict[str, str]) -> bool:
    red = sorted(test for test, result in outcomes.items() if result in {"FAIL", "ERROR"})
    green_claims = sorted(
        test for test in mutation.claims if outcomes.get(test) == "ok"
    )
    print(f"\n{mutation.name} ({mutation.target})")
    print("  killed: " + (", ".join(red) if red else "none"))
    print("  green while claiming coverage: "
          + (", ".join(green_claims) if green_claims else "none"))
    return returncode != 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mutation", nargs="*", help="named mutation(s); default: all")
    args = parser.parse_args(argv[1:])

    failed_to_kill = False
    for mutation in _selected(args.mutation):
        scratch, copy_root = _make_copy()
        try:
            _apply(mutation, copy_root)
            returncode, outcomes, output = _run(copy_root, scratch)
            if not outcomes and returncode == 0:
                raise RuntimeError(f"{mutation.name}: suite produced no test outcomes")
            failed_to_kill |= not _report(mutation, returncode, outcomes)
            if returncode == 0:
                print(output, file=sys.stderr, end="")
        finally:
            shutil.rmtree(scratch)
    return 1 if failed_to_kill else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
