"""A staged gate is a ladder, and evidence must say which rung it climbed.

These tests exist because a documented feature was unusable and nothing noticed:
`validate_gate` normalised a staged gate to `check: None` while keeping `checks`,
and `validate_attestation` compared against `check` — so every possible
attestation against a staged gate was refused, including the correct one. The
package's own suite never authored a staged gate, and neither did either
reference implementation, so the two halves of the merged schema had never been
asked to compose.

The rule now: one attestation per stage, each pinned to its index. A single-check
gate takes one and refuses an index it has no rung for.
"""

import pytest

from asop.errors import Refusal
from asop.gates import (
    attestation_passes,
    gate_satisfied,
    validate_attestation,
    validate_gate,
)

EVIDENCE = {
    "exit_status": 0,
    "environment": "ci:linux-x86_64:py3.12",
    "at": "2026-09-07T21:00:00Z",
}


@pytest.fixture
def staged():
    return validate_gate({"kind": "deterministic", "checks": ["pytest -q", "ruff check ."]})


@pytest.fixture
def single():
    return validate_gate({"kind": "deterministic", "check": "pytest -q"})


def attest(gate, **fields):
    return validate_attestation({**EVIDENCE, **fields}, gate=gate, submitted_by="ci")


def test_each_stage_can_be_attested(staged):
    """The regression itself: this was refused for every possible input."""
    assert attest(staged, check="pytest -q", stage=0)["stage"] == 0
    assert attest(staged, check="ruff check .", stage=1)["stage"] == 1


def test_a_staged_gate_refuses_evidence_that_names_no_stage(staged):
    with pytest.raises(Refusal) as e:
        attest(staged, check="pytest -q")
    assert "which one it is about" in e.value.message


def test_a_stage_index_must_match_the_command_that_ran(staged):
    """Naming stage 0 while running stage 1's command is the interesting lie."""
    with pytest.raises(Refusal) as e:
        attest(staged, check="ruff check .", stage=0)
    assert "stage 0 of this gate checks" in e.value.message


def test_a_stage_outside_the_ladder_is_refused(staged):
    for bad in (2, 7, -1):
        with pytest.raises(Refusal):
            attest(staged, check="pytest -q", stage=bad)


def test_a_boolean_is_not_a_stage_index(staged):
    """True == 1 in Python, so this would otherwise pass as stage 1."""
    with pytest.raises(Refusal):
        attest(staged, check="ruff check .", stage=True)


def test_a_single_check_gate_has_no_stage_to_attest_to(single):
    with pytest.raises(Refusal) as e:
        attest(single, check="pytest -q", stage=0)
    assert "no stage" in e.value.message


def test_a_single_check_gate_still_works_unchanged(single):
    assert attest(single, check="pytest -q")["check"] == "pytest -q"
    with pytest.raises(Refusal):
        attest(single, check="make test")


def test_a_ladder_is_climbed_only_when_every_rung_is(staged):
    zero = attest(staged, check="pytest -q", stage=0)
    one = attest(staged, check="ruff check .", stage=1)
    assert not gate_satisfied(staged, [])
    assert not gate_satisfied(staged, [zero]), "a missing rung hides the failure staging exists to surface"
    assert not gate_satisfied(staged, [zero, zero]), "the same rung twice is not two rungs"
    assert gate_satisfied(staged, [zero, one])


def test_a_failed_stage_does_not_satisfy_the_gate(staged):
    zero = attest(staged, check="pytest -q", stage=0)
    one = validate_attestation(
        {**EVIDENCE, "exit_status": 1, "check": "ruff check .", "stage": 1},
        gate=staged, submitted_by="ci",
    )
    assert not attestation_passes(one)
    assert not gate_satisfied(staged, [zero, one])


def test_a_single_check_gate_is_satisfied_by_one_passing_attestation(single):
    assert gate_satisfied(single, [attest(single, check="pytest -q")])
    assert not gate_satisfied(single, [])
