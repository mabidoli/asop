"""Hand-offs between ASOPs (ASOP.md §3.7, v3.5): what a finished run may file
next, when the filed run may start, and what the engine refuses.

The worked chain is the one the spec uses: `intake` fans out one `extract` per
source; every `extract` hands off to one `consolidate` (a join); `consolidate`
hands to `plan`, which waits for a person's approval; `plan` fans out one build
per task, routed by the task's kind; every procedure escalates to `decide`,
which a person takes part in."""

from __future__ import annotations

import pytest

from asop import validate_asop
from asop.chain import (
    FILING_REQUEST_SCHEMA,
    HANDOFF_RULES,
    ChainContractError,
    natural_key,
    plan_handoffs,
    requires_satisfied,
    validate_filing_request,
    validate_handoff_targets,
    version_in,
)
from asop.refusals import CODES
from asop.sop import SopContractError

DET = {"kind": "deterministic", "check": "make check"}
HUMAN = {"kind": "human", "check": "a person answers", "verifier": "dana",
         "max_park_seconds": 86400, "on_timeout": "escalate", "escalate_to": "dana"}
ESCALATE = {"to": {"asop_id": "decide", "versions": ">=1"}, "inputs": {"question_kind": "escalation"}}


def body(title, *, inputs=(), steps=None, roles=None, **chain):
    b = {
        "title": title,
        "inputs": [{"name": n} for n in inputs],
        "roles": roles or {"worker": {"kind": "agent"}},
        "steps": steps or [{"name": "do", "role": "worker", "purpose": "do it", "gate": DET}],
    }
    b.update(chain)
    return b


INTAKE = body(
    "Frame the work",
    inputs=["root"],
    outcomes=["FETCHED", "NOTHING_TO_FETCH", "BLOCKED"],
    outputs=[{"name": "sources", "description": "every source found"}],
    handoffs=[{
        "id": "extract-each-source",
        "to": {"asop_id": "extract", "versions": ">=2"},
        "on_outcomes": ["FETCHED", "NOTHING_TO_FETCH"],
        "cardinality": "many",
        "subject_from": {"output": "sources", "array": "sources", "id_field": "id",
                         "where": {"kind": ["spec", "feature"]}, "where_not": {"removed": True}},
        "inputs": {"source_id": "$subject", "title": "$subject.title", "root": "$run.inputs.root"},
    }],
    on_escalate=ESCALATE,
)
EXTRACT = body(
    "Extract requirements from one source",
    inputs=["source_id", "title", "root"],
    handoffs=[{"id": "join", "to": {"asop_id": "consolidate", "version": 1}, "inputs": {"root": "$run.inputs.root"}}],
    on_escalate=ESCALATE,
)
CONSOLIDATE = body(
    "Consolidate requirements",
    inputs=["root"],
    requires=[{"kind": "all_runs_done", "asop_id": "extract"}],
    handoffs=[{"id": "plan", "to": {"asop_id": "plan", "version": 1}, "inputs": {"root": "$run.inputs.root"}}],
)
PLAN = body(
    "Plan the work",
    inputs=["root"],
    outputs=[{"name": "tasks"}],
    requires=[{"kind": "run_done", "asop_id": "consolidate"},
              {"kind": "human_approved", "artefact": "research-review"}],
    handoffs=[
        {"id": "build-backend", "to": {"asop_id": "build", "version": 1}, "cardinality": "many",
         "subject_from": {"output": "tasks", "array": "tasks", "id_field": "id", "where": {"kind": "backend"}},
         "inputs": {"task_id": "$subject"}},
        {"id": "build-handoff", "to": {"asop_id": "build-handoff", "version": 1}, "cardinality": "many",
         "subject_from": {"output": "tasks", "array": "tasks", "id_field": "id", "where": {"kind": "handoff"}},
         "inputs": {"task_id": "$subject"}},
    ],
)
BUILD = body("Build one task", inputs=["task_id"])
BUILD_HANDOFF = body("Build one hand-off task", inputs=["task_id"])
DECIDE = body(
    "Decide a question",
    inputs=["question_kind", "question"],
    roles={"owner": {"kind": "human"}},
    steps=[{"name": "answer", "role": "owner", "purpose": "answer it", "gate": HUMAN}],
)

LIBRARY = {  # asop_id -> {version: body}; the version listed is the ACTIVE one
    "intake": {1: INTAKE}, "extract": {2: EXTRACT}, "consolidate": {1: CONSOLIDATE},
    "plan": {1: PLAN}, "build": {1: BUILD}, "build-handoff": {1: BUILD_HANDOFF}, "decide": {3: DECIDE},
}


def resolve(to):
    for version, b in LIBRARY.get(to["asop_id"], {}).items():
        if version_in(to, version):
            return version, validate_asop(b)
    return None


def v(b, asop_id=None):
    return validate_asop(b, asop_id=asop_id)


SOURCES = {"sources": [
    {"id": "s1", "kind": "spec", "title": "Spec one"},
    {"id": "s2", "kind": "feature", "title": "Feature two"},
    {"id": "s3", "kind": "ticket", "title": "Not extracted"},
    {"id": "s4", "kind": "spec", "title": "Withdrawn", "removed": True},
]}


def plan_intake(outcome="FETCHED", requests=(), outputs=None, existing=()):
    return plan_handoffs(v(INTAKE, "intake"), outcome=outcome, outputs={"sources": SOURCES} if outputs is None else outputs,
                         run_inputs={"root": "R1"}, requests=list(requests), resolve=resolve, chain="R1",
                         existing_keys=existing)


def req(**over):
    r = {"schema": FILING_REQUEST_SCHEMA, "handoff": "extract-each-source", "to": "extract", "subject": "s1"}
    r.update(over)
    return r


# ------------------------------------------------------------------ the record

def test_a_record_without_chaining_is_unchanged():
    clean = v(BUILD)
    for f in ("outcomes", "outputs", "handoffs", "on_escalate", "requires"):
        assert f not in clean


def test_every_record_in_the_worked_chain_validates():
    for asop_id, versions in LIBRARY.items():
        for b in versions.values():
            v(b, asop_id)
            validate_handoff_targets(v(b, asop_id), resolve)


def test_a_many_handoff_needs_subject_from():
    bad = body("x", outputs=[{"name": "tasks"}],
               handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}, "cardinality": "many",
                          "inputs": {"task_id": "$subject"}}])
    with pytest.raises(SopContractError, match="no 'subject_from'"):
        v(bad)


def test_subject_from_reads_only_a_declared_output():
    bad = body("x", handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}, "cardinality": "many",
                               "subject_from": {"output": "tasks", "array": "tasks", "id_field": "id"},
                               "inputs": {"task_id": "$subject"}}])
    with pytest.raises(SopContractError, match="not a declared output"):
        v(bad)


def test_on_outcomes_must_name_a_declared_outcome():
    # The misspelling that would otherwise never fire, silently.
    bad = body("x", outcomes=["FETCHED"],
               handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}, "on_outcomes": ["FETCHD"]}])
    with pytest.raises(SopContractError, match="not a declared outcome"):
        v(bad)


def test_on_outcomes_without_declared_outcomes_is_refused():
    bad = body("x", handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}, "on_outcomes": ["DONE"]}])
    with pytest.raises(SopContractError, match="declares no 'outcomes'"):
        v(bad)


@pytest.mark.parametrize("kind", ["slot_free", "artefact_current", "no_run_for_subject", "whenever"])
def test_requires_kinds_are_a_closed_list(kind):
    with pytest.raises(SopContractError, match="not (one of|a requires kind)"):
        v(body("x", requires=[{"kind": kind, "asop_id": "y"}]))


def test_subject_references_exist_only_on_a_fan_out():
    bad = body("x", handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}, "inputs": {"task_id": "$subject"}}])
    with pytest.raises(SopContractError, match="only on a `many`"):
        v(bad)


def test_run_references_must_be_declared():
    bad = body("x", inputs=["root"], handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1},
                                                "inputs": {"task_id": "$run.inputs.rooot"}}])
    with pytest.raises(SopContractError, match="does not declare"):
        v(bad)


def test_an_escalation_may_not_read_outputs():
    bad = body("x", outputs=[{"name": "o"}],
               on_escalate={"to": {"asop_id": "decide", "version": 3}, "inputs": {"question": "$run.outputs.o"}})
    with pytest.raises(SopContractError, match="never its outputs"):
        v(bad)


def test_a_handoff_to_itself_is_refused():
    with pytest.raises(SopContractError, match="its own ASOP"):
        v(body("x", handoffs=[{"id": "h", "to": {"asop_id": "x", "version": 1}}]), "x")


@pytest.mark.parametrize("to,match", [
    ({"asop": "build", "version": 1}, "'asop_id'"),
    ({"asop_id": "build"}, "exactly one of"),
    ({"asop_id": "build", "version": 1, "versions": ">=1"}, "exactly one of"),
    ({"asop_id": "build", "versions": "^1"}, "only two forms"),
    ({"asop_id": "build", "versions": ">=3,<3"}, "empty"),
])
def test_targets_are_a_pin_or_a_simple_range(to, match):
    with pytest.raises(SopContractError, match=match):
        v(body("x", handoffs=[{"id": "h", "to": to}]))


def test_version_ranges():
    assert version_in({"asop_id": "a", "versions": ">=2"}, 2)
    assert version_in({"asop_id": "a", "versions": ">=2,<4"}, 3)
    assert not version_in({"asop_id": "a", "versions": ">=2,<4"}, 4)
    assert not version_in({"asop_id": "a", "versions": ">=2"}, 1)
    assert version_in({"asop_id": "a", "version": 2}, 2) and not version_in({"asop_id": "a", "version": 2}, 3)


# ------------------------------------------------------------------ across records

def test_a_target_that_does_not_declare_the_input_is_refused():
    bad = v(body("x", handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1},
                                 "inputs": {"task_id": "t", "colour": "blue"}}]))
    with pytest.raises(ChainContractError, match="does not declare"):
        validate_handoff_targets(bad, resolve)


def test_a_target_left_without_an_input_is_refused():
    bad = v(body("x", handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}}]))
    with pytest.raises(ChainContractError, match="unsupplied"):
        validate_handoff_targets(bad, resolve)


def test_an_unresolvable_target_is_refused():
    bad = v(body("x", handoffs=[{"id": "h", "to": {"asop_id": "nowhere", "version": 1}}]))
    with pytest.raises(ChainContractError, match="no ACTIVE version"):
        validate_handoff_targets(bad, resolve)


def test_an_escalation_must_reach_a_person():
    bad = v(body("x", on_escalate={"to": {"asop_id": "build", "version": 1}, "inputs": {}}))
    with pytest.raises(ChainContractError, match="reaches a person"):
        validate_handoff_targets(bad, resolve)


# ------------------------------------------------------------------ filing

def filed(decisions):
    return [d for d in decisions if d["action"] == "file"]


def refused(decisions):
    return [d["code"] for d in decisions if d["action"] == "refuse"]


def test_a_fan_out_expands_from_the_output_with_its_filters():
    d = plan_intake()
    assert [x["subject"] for x in filed(d)] == ["s1", "s2"]
    assert filed(d)[0] == {
        "action": "file", "handoff": "extract-each-source", "to": {"asop_id": "extract", "version": 2},
        "subject": "s1", "inputs": {"source_id": "s1", "title": "Spec one", "root": "R1"},
        "natural_key": "R1|extract|s1|a1",
    }
    assert refused(d) == []


def test_an_outcome_outside_on_outcomes_files_nothing():
    assert plan_intake(outcome="BLOCKED") == []


def test_an_empty_fan_out_files_nothing_and_is_not_an_error():
    assert plan_intake(outputs={"sources": {"sources": []}}) == []


def test_requests_narrow_a_fan_out():
    d = plan_intake(requests=[req(subject="s2")])
    assert [x["subject"] for x in filed(d)] == ["s2"]


def test_a_request_for_an_undeclared_handoff_is_refused():
    d = plan_intake(requests=[req(handoff="extract-everything")])
    assert refused(d) == ["handoff_refused:undeclared"]


def test_a_request_whose_target_does_not_match_its_handoff_is_refused():
    d = plan_intake(requests=[req(to="build")])
    assert refused(d) == ["handoff_refused:undeclared"]


def test_a_request_on_the_wrong_outcome_is_refused():
    d = plan_intake(outcome="BLOCKED", requests=[req()])
    assert refused(d) == ["handoff_refused:outcome"]
    assert filed(d) == []


def test_a_request_for_a_subject_the_output_does_not_yield_is_refused():
    # s3 exists but is filtered out; s9 does not exist. Neither may be filed.
    d = plan_intake(requests=[req(subject="s3"), req(subject="s9")])
    assert refused(d) == ["handoff_refused:subject", "handoff_refused:subject"]
    assert filed(d) == []


def test_a_request_may_not_override_a_declared_mapping():
    d = plan_intake(requests=[req(inputs={"root": "SOMEWHERE-ELSE"})])
    assert refused(d) == ["handoff_refused:inputs"]


def test_a_request_may_not_hand_an_undeclared_input():
    d = plan_intake(requests=[req(inputs={"colour": "blue"})])
    assert refused(d) == ["handoff_refused:inputs"]


def test_a_malformed_request_is_refused_not_repaired():
    d = plan_intake(requests=[{"handoff": "extract-each-source", "to": "extract"}])
    assert refused(d) == ["handoff_refused:malformed"]
    assert len(filed(d)) == 2  # the declared hand-off still fires; the junk does not narrow it


def test_a_one_handoff_takes_at_most_one_request():
    one = v(EXTRACT, "extract")
    r = {"schema": FILING_REQUEST_SCHEMA, "handoff": "join", "to": "consolidate"}
    d = plan_handoffs(one, outcome=None, outputs={}, run_inputs={"root": "R1"}, requests=[r, r],
                      resolve=resolve, chain="R1")
    assert refused(d) == ["handoff_refused:cardinality"]
    assert filed(d) == []


def test_a_one_handoff_may_not_choose_its_subject():
    one = v(EXTRACT, "extract")
    r = {"schema": FILING_REQUEST_SCHEMA, "handoff": "join", "to": "consolidate", "subject": "elsewhere"}
    d = plan_handoffs(one, outcome=None, outputs={}, run_inputs={"root": "R1"}, requests=[r],
                      resolve=resolve, chain="R1")
    assert refused(d) == ["handoff_refused:subject"]


def test_a_join_is_filed_once_however_many_predecessors_hand_off():
    one = v(EXTRACT, "extract")
    keys: set[str] = set()
    actions = []
    for _ in range(3):  # three extract runs finish
        d = plan_handoffs(one, outcome=None, outputs={}, run_inputs={"root": "R1"}, requests=[],
                          resolve=resolve, chain="R1", existing_keys=keys)
        actions += [x["action"] for x in d]
        keys |= {x["natural_key"] for x in filed(d)}
    assert actions == ["file", "duplicate", "duplicate"]
    assert keys == {natural_key("R1", "consolidate", "R1")}


def test_a_replay_files_nothing_twice():
    first = plan_intake()
    again = plan_intake(existing={x["natural_key"] for x in filed(first)})
    assert filed(again) == [] and {x["action"] for x in again} == {"duplicate"}


def test_a_target_with_no_active_version_in_range_is_refused():
    saved = LIBRARY["extract"]
    LIBRARY["extract"] = {1: EXTRACT}  # only v1 active; the range is >=2
    try:
        d = plan_intake()
    finally:
        LIBRARY["extract"] = saved
    assert refused(d) == ["handoff_refused:version"]


def test_an_output_without_the_declared_shape_is_refused():
    d = plan_intake(outputs={"sources": {"items": []}})
    assert refused(d) == ["handoff_refused:output"]
    d = plan_intake(outputs={"sources": {"sources": [{"kind": "spec"}]}})
    assert refused(d) == ["handoff_refused:output"]


def test_fan_out_routes_by_kind_across_handoffs():
    tasks = {"tasks": [{"id": "t1", "kind": "backend"}, {"id": "t2", "kind": "handoff"},
                       {"id": "t3", "kind": "backend"}, {"id": "t4", "kind": "frontend"}]}
    d = plan_handoffs(v(PLAN, "plan"), outcome=None, outputs={"tasks": tasks}, run_inputs={"root": "R1"},
                      requests=[], resolve=resolve, chain="R1")
    assert [(x["to"]["asop_id"], x["subject"]) for x in filed(d)] == [
        ("build", "t1"), ("build", "t3"), ("build-handoff", "t2")]


def test_a_literal_dollar_is_escaped():
    b = v(body("x", handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}, "inputs": {"task_id": "$$5"}}]))
    d = plan_handoffs(b, outcome=None, outputs={}, run_inputs={}, requests=[], resolve=resolve, chain="C")
    assert filed(d)[0]["inputs"] == {"task_id": "$5"}


def test_every_handoff_rule_is_a_catalogued_refusal():
    assert "handoff_refused:<rule>" in CODES
    assert set(HANDOFF_RULES) == {"malformed", "undeclared", "outcome", "cardinality", "subject",
                                  "output", "inputs", "version"}


def test_filing_request_shape():
    assert validate_filing_request(req(subject=7))["subject"] == "7"
    with pytest.raises(ChainContractError):
        validate_filing_request(req(schema="asop.filing-request/2"))
    with pytest.raises(ChainContractError):
        validate_filing_request(req(priority="high"))


# ------------------------------------------------------------------ starting

def runs(*specs):
    return [{"asop_id": a, "subject": s, "attempt": n, "done": d} for a, s, n, d in specs]


JOIN = [{"kind": "all_runs_done", "asop_id": "extract"}]


def test_a_join_waits_for_every_filed_run():
    assert not requires_satisfied(JOIN, runs=runs(("extract", "s1", 1, True), ("extract", "s2", 1, False)),
                                  fanned_out=["extract"])
    assert requires_satisfied(JOIN, runs=runs(("extract", "s1", 1, True), ("extract", "s2", 1, True)),
                              fanned_out=["extract"])


def test_a_join_waits_until_the_fan_out_has_happened():
    assert not requires_satisfied(JOIN, runs=[], fanned_out=[])


def test_an_empty_fan_out_satisfies_its_join():
    # The deadlock this rule exists to prevent: a join waiting for runs that
    # a filter guaranteed would never be filed.
    assert requires_satisfied(JOIN, runs=[], fanned_out=["extract"])


def test_only_the_latest_attempt_counts():
    rs = runs(("extract", "s1", 1, False), ("extract", "s1", 2, True))
    assert requires_satisfied(JOIN, runs=rs, fanned_out=["extract"])
    rs = runs(("extract", "s1", 1, True), ("extract", "s1", 2, False))
    assert not requires_satisfied(JOIN, runs=rs, fanned_out=["extract"])
    # by attempt number, not by the order a ledger happens to list them in
    rs = runs(("extract", "s1", 2, False), ("extract", "s1", 1, True))
    assert not requires_satisfied(JOIN, runs=rs, fanned_out=["extract"])


def test_run_done_needs_a_run():
    req_ = [{"kind": "run_done", "asop_id": "consolidate"}]
    assert not requires_satisfied(req_, runs=[])
    assert requires_satisfied(req_, runs=runs(("consolidate", "R1", 1, True)))


def test_every_predicate_must_hold():
    reqs = v(PLAN, "plan")["requires"]
    done = runs(("consolidate", "R1", 1, True))
    assert not requires_satisfied(reqs, runs=done, approved=[])
    assert not requires_satisfied(reqs, runs=[], approved=["research-review"])
    assert requires_satisfied(reqs, runs=done, approved=["research-review"])
