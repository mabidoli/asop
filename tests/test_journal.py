"""Per-step journaling, resume, staleness and the chain outcome record
(ASOP.md §5.6–§5.8, v3.6)."""

from __future__ import annotations

import pytest

from asop import validate_asop
from asop.chain import ChainContractError
from asop.gates import retry_decision
from asop.journal import (
    CHAIN_OUTCOME_SCHEMA,
    JOURNAL_SCHEMA,
    chain_outcome,
    charged_failures,
    resume_point,
    stale_runs,
    validate_journal_entry,
)

DET = {"kind": "deterministic", "check": "make check"}
BODY = validate_asop({"title": "Three steps", "roles": {"w": {"kind": "agent"}},
                      "steps": [{"name": n, "role": "w", "purpose": n, "gate": DET} for n in ("a", "b", "c")]})
KEY = "C1|build|T1|a1"


def e(step, event, attempt=1, **kw):
    return {"schema": JOURNAL_SCHEMA, "natural_key": KEY, "step": step, "attempt": attempt, "event": event,
            "at": "2026-10-16T00:00:00Z", **kw}


def test_entry_shape():
    assert validate_journal_entry(e(1, "passed", outputs={"plan": "sha256:aa"}))["outputs"] == {"plan": "sha256:aa"}
    for bad in (e(0, "passed"), e(1, "finished"), e(1, "passed", fault_class="work"),
                e(1, "failed", fault_class="cosmic"), {**e(1, "passed"), "extra": 1},
                {**e(1, "passed"), "schema": "asop.step-journal/2"}):
        with pytest.raises(ChainContractError):
            validate_journal_entry(bad)


def test_resume_after_a_crash_mid_step():
    r = resume_point(BODY, [e(1, "started"), e(1, "passed"), e(2, "started")])
    assert r["done"] == [1] and r["crashed"] == [2] and r["next"] == [2]


def test_resume_of_a_fresh_run_starts_at_step_one():
    assert resume_point(BODY, [])["next"] == [1]


def test_resume_does_not_run_past_a_failure_or_a_parked_gate():
    r = resume_point(BODY, [e(1, "passed"), e(2, "failed")])
    assert r["failed"] == [2] and r["next"] == []
    r = resume_point(BODY, [e(1, "passed"), e(2, "parked")])
    assert r["parked"] == [2] and r["next"] == []


def test_the_last_entry_decides():
    r = resume_point(BODY, [e(1, "failed"), e(1, "started", attempt=2), e(1, "passed", attempt=2)])
    assert r["done"] == [1] and r["next"] == [2]


def test_a_crash_is_not_an_attempt():
    """A crash re-file reached no gate, so it does not spend §5.4's budget."""
    entries = [e(2, "started"), e(2, "started", attempt=2), e(2, "failed", attempt=2), e(2, "started", attempt=3)]
    assert charged_failures(entries, step=2) == 1
    assert retry_decision(charged_failures(entries, step=2)) == "fix"


def test_only_work_failures_are_charged():
    entries = [e(1, "failed", fault_class="environment"), e(1, "cannot_evaluate"),
               e(1, "failed", fault_class="checker"), e(1, "failed", fault_class="work"), e(1, "failed")]
    assert charged_failures(entries, step=1) == 2, "an unclassified failure is charged, as in v3.5"


# --- staleness (§5.7) ---------------------------------------------------------

LINEAGE = {
    "C1|plan|C1|a1": {"read": {"source": "sha256:s1", "question:Q1": None}},
    "C1|build|T1|a1": {"read": {"tasks": "sha256:t1"}},
    "C1|build|T2|a1": {"read": {"tasks": "sha256:t1", "question:Q2": "sha256:ans"}},
}


def test_writing_an_answer_makes_what_was_built_on_the_open_question_stale():
    assert stale_runs(LINEAGE, {"question:Q1": "sha256:ans"}) == ["C1|plan|C1|a1"]


def test_an_unchanged_answer_changes_nothing():
    assert stale_runs(LINEAGE, {"question:Q2": "sha256:ans"}) == []


def test_a_changed_source_invalidates_readiness_and_what_read_it():
    assert stale_runs(LINEAGE, {"source": "sha256:s2"}) == ["C1|plan|C1|a1"]


def test_a_changed_output_makes_every_reader_stale():
    assert stale_runs(LINEAGE, {"tasks": "sha256:t2"}) == ["C1|build|T1|a1", "C1|build|T2|a1"]


def test_artefacts_not_in_the_change_set_are_not_compared():
    assert stale_runs(LINEAGE, {}) == []


# --- the chain outcome record (§5.8) ----------------------------------------

RUNS = [
    {"asop_id": "plan", "version": 9, "subject": "C1", "attempt": 1, "status": "superseded", "spend": 10},
    {"asop_id": "plan", "version": 9, "subject": "C1", "attempt": 2, "status": "done", "spend": 12,
     "human_wait_seconds": 3600},
    {"asop_id": "build", "version": 10, "subject": "T1", "attempt": 1, "status": "failed", "fault_class": "environment"},
    {"asop_id": "build", "version": 10, "subject": "T1", "attempt": 2, "status": "done", "spend": 4},
    {"asop_id": "build", "version": 11, "subject": "T2", "attempt": 1, "status": "done", "spend": 5},
]


def test_outcome_counts_what_owners_pay_for():
    r = chain_outcome(RUNS, chain="C1", manifest="sha256:" + "a" * 64, started_at="2026-10-16T00:00:00Z",
                      ended_at="2026-10-16T18:00:00Z",
                      escalations=[{"fault_class": "environment", "audience": "engineering"},
                                   {"fault_class": "decision", "audience": "product"}],
                      chain_gate="passed", spend_unit="USD")
    assert r["schema"] == CHAIN_OUTCOME_SCHEMA and r["verdict"] == "done"
    assert r["wall_seconds"] == 18 * 3600 and r["spend"] == 31 and r["human_wait_seconds"] == 3600
    assert r["failures_by_class"] == {"environment": 1}
    assert r["escalations_by_class"] == {"decision": 1, "environment": 1}
    assert r["escalations_by_audience"] == {"engineering": 1, "product": 1}
    assert r["superseded"] == 1 and r["runs"] == 5
    assert r["mixed_versions"] == ["build"], "the condition a manifest prevents is never hidden"


def test_the_chain_is_not_done_until_its_chain_gate_passes():
    done = [r for r in RUNS if r["status"] != "failed"]
    assert chain_outcome(done, chain="C1")["verdict"] == "done"
    assert chain_outcome(done, chain="C1", chain_gate="cannot_evaluate")["verdict"] == "open"
    assert chain_outcome(done, chain="C1", chain_gate="failed")["verdict"] == "failed"


def test_a_latest_attempt_that_failed_fails_the_chain():
    runs = RUNS + [{"asop_id": "build", "version": 11, "subject": "T3", "attempt": 1, "status": "failed"}]
    r = chain_outcome(runs, chain="C1")
    assert r["verdict"] == "failed" and r["failures_by_class"] == {"environment": 1, "unclassified": 1}


def test_an_empty_ledger_is_open_not_done():
    assert chain_outcome([], chain="C1")["verdict"] == "open"
