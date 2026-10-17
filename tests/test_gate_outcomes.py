"""v3.6 gate additions (ASOP.md §3.3, §5.2): the third outcome
(`cannot_evaluate`), the zero-cases floor, and the non-blocking human gate
(`on_timeout: proceed_on_default`). Every one is opt-in by declaration, so the
last block pins that a v3.5 gate means exactly what it meant."""

from __future__ import annotations

import pytest

from asop.errors import Refusal
from asop.gates import (
    GATE_OUTCOMES,
    ON_TIMEOUT,
    gate_outcome,
    gate_satisfied,
    timeout_resolution,
    validate_attestation,
    validate_gate,
)

SUITE = {"kind": "deterministic", "check": "make test", "cannot_evaluate_exit": 3, "min_cases": 1}
QUESTION = {
    "kind": "human", "check": "which retention period applies?", "verifier": "dana",
    "max_park_seconds": 3600, "on_timeout": "proceed_on_default",
    "default": {"option": "keep the current period", "consequence": "the purge job is built for it",
                "audience": "product owner"},
}


def attest(gate, *, exit_status=0, cases_run=None, stage=None, check=None, verdict=None):
    doc = {"check": check or gate.get("check") or gate["checks"][stage], "exit_status": exit_status,
           "environment": "ci", "at": "2026-10-16T00:00:00Z"}
    if cases_run is not None:
        doc["cases_run"] = cases_run
    if stage is not None:
        doc["stage"] = stage
    if verdict is not None:
        doc["verdict"] = verdict
    return validate_attestation(doc, gate=gate, submitted_by="runner")


def refused(payload, fragment):
    with pytest.raises(Refusal) as e:
        validate_gate(payload)
    assert e.value.code == "gate_invalid"
    assert fragment in e.value.message, e.value.message


# --- cannot_evaluate -------------------------------------------------------

def test_outcomes_vocabulary():
    assert GATE_OUTCOMES == ("passed", "failed", "cannot_evaluate", "incomplete")
    assert "proceed_on_default" in ON_TIMEOUT


def test_declared_exit_is_cannot_evaluate_and_never_satisfies():
    gate = validate_gate(SUITE)
    a = attest(gate, exit_status=3, cases_run=0)
    assert gate_outcome(gate, [a]) == "cannot_evaluate"
    assert gate_satisfied(gate, [a]) is False


def test_zero_cases_with_exit_zero_is_not_a_pass():
    """The suite collected nothing, exited 0, and would have merged."""
    gate = validate_gate(SUITE)
    empty = attest(gate, exit_status=0, cases_run=0)
    assert gate_satisfied(gate, [empty]) is False
    assert gate_outcome(gate, [empty]) == "cannot_evaluate"
    assert gate_outcome(gate, [attest(gate, exit_status=0, cases_run=12)]) == "passed"


def test_min_cases_makes_the_count_part_of_the_evidence():
    gate = validate_gate(SUITE)
    with pytest.raises(Refusal) as e:
        validate_attestation({"check": "make test", "exit_status": 0, "environment": "ci",
                              "at": "2026-10-16T00:00:00Z"}, gate=gate, submitted_by="runner")
    assert e.value.code == "attestation_invalid" and "cases_run" in e.value.message
    for bad in (-1, True, "12"):
        with pytest.raises(Refusal):
            attest(gate, cases_run=bad)


def test_other_nonzero_exit_is_still_a_failure():
    gate = validate_gate(SUITE)
    assert gate_outcome(gate, [attest(gate, exit_status=1, cases_run=40)]) == "failed"


def test_no_evidence_is_incomplete():
    assert gate_outcome(validate_gate(SUITE), []) == "incomplete"


def test_staged_ladder_reports_the_first_rung_that_did_not_pass():
    gate = validate_gate({"kind": "deterministic", "checks": ["make lint", "make test"], "cannot_evaluate_exit": 3})
    lint_ok = attest(gate, stage=0)
    test_ce = attest(gate, stage=1, exit_status=3)
    assert gate_outcome(gate, [lint_ok, test_ce]) == "cannot_evaluate"
    assert gate_outcome(gate, [attest(gate, stage=0, exit_status=1), test_ce]) == "failed"
    assert gate_outcome(gate, [lint_ok]) == "incomplete"
    assert gate_outcome(gate, [lint_ok, attest(gate, stage=1)]) == "passed"


def test_cannot_evaluate_fields_are_deterministic_only_and_typed():
    refused({"kind": "judged", "check": "x", "cannot_evaluate_exit": 3}, "cannot_evaluate_exit is set on a 'judged'")
    refused({"kind": "deterministic", "check": "x", "cannot_evaluate_exit": 0}, "1..255")
    refused({"kind": "deterministic", "check": "x", "cannot_evaluate_exit": True}, "1..255")
    refused({"kind": "deterministic", "check": "x", "min_cases": 0}, "positive integer")
    refused({"kind": "human", "check": "x", "verifier": "dana", "max_park_seconds": 60,
             "on_timeout": "fail", "min_cases": 1}, "min_cases is set on a 'human'")


# --- proceed_on_default ------------------------------------------------------

def test_proceed_on_default_normalises_and_resolves():
    gate = validate_gate(QUESTION, require=("clock",))
    assert gate["default"]["audience"] == "product owner"
    assert timeout_resolution(gate) == {"status": "done", "on_default": gate["default"]}


def test_default_needs_all_three_fields():
    for drop in ("option", "consequence", "audience"):
        d = dict(QUESTION["default"])
        del d[drop]
        refused({**QUESTION, "default": d}, "needs default")
    refused({**QUESTION, "default": {**QUESTION["default"], "audience": "  "}}, "needs default")
    refused({k: v for k, v in QUESTION.items() if k != "default"}, "needs default")


def test_default_is_human_only():
    refused({**QUESTION, "kind": "judged", "verifier": None}, "on a 'judged' gate")


def test_default_without_proceed_is_refused():
    refused({**QUESTION, "on_timeout": "fail"}, "default is set but on_timeout is 'fail'")


def test_default_alone_is_a_half_declared_clock():
    refused({"kind": "human", "check": "x", "verifier": "dana", "default": QUESTION["default"]},
            "declares part of its park clock")


def test_the_other_timeouts_resolve_as_before():
    assert timeout_resolution(validate_gate({**QUESTION, "on_timeout": "fail", "default": None})) == \
        {"status": "verify_failed"}
    assert timeout_resolution(validate_gate({**QUESTION, "on_timeout": "escalate", "escalate_to": "owner",
                                             "default": None})) == {"status": "escalated", "escalate_to": "owner"}
    with pytest.raises(ValueError):
        timeout_resolution(validate_gate({"kind": "deterministic", "check": "x"}))


# --- compatibility -----------------------------------------------------------

def test_a_v35_gate_means_what_it_meant():
    """No `cannot_evaluate_exit`, no `min_cases`: exit 3 is a plain failure and
    exit 0 is a pass, whatever the count."""
    gate = validate_gate({"kind": "deterministic", "check": "make test"})
    assert gate["cannot_evaluate_exit"] is None and gate["min_cases"] is None and gate["default"] is None
    assert gate_outcome(gate, [attest(gate, exit_status=3)]) == "failed"
    assert gate_satisfied(gate, [attest(gate, exit_status=0)]) is True
    assert gate_outcome(gate, [attest(gate, exit_status=0, cases_run=0)]) == "passed"


def test_normalised_v36_gate_revalidates_and_strips_to_a_v35_shape():
    """A store re-validates on every write, and a 0.5.0 reader drops nulls
    first (§3.3): a v3.6-normalised gate that uses no v3.6 field must carry
    nothing a 0.5.0 reader would refuse."""
    gate = validate_gate({"kind": "deterministic", "check": "make test"})
    assert validate_gate(gate) == gate
    non_null = {k for k, v in gate.items() if v is not None}
    assert non_null <= {"schema_version", "kind", "check"}
