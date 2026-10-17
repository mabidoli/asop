"""v3.6 chain additions (ASOP.md §3.7–§3.9): entry gates (the hand-off gate
and the source readiness gate), declared and bounded rework, ordered and
approved fan-outs, `human_approved` bound to a digest, and the chain gate.

The worked chain: `frame` reads a source document and hands it to `plan`;
`plan` writes a task list, a person approves a subset of it, and it fans out
one `build` per approved task in dependency order; `review` reads the build
and can send its findings back to `plan`; `close` requires the chain gate."""

from __future__ import annotations

import pytest

from asop import validate_asop
from asop.chain import (
    MAX_REWORK_ROUNDS,
    ChainContractError,
    fanout_startable,
    natural_key,
    plan_entry,
    plan_handoffs,
    plan_rework,
    preflight_checks,
    requires_satisfied,
    validate_handoff_targets,
)
from asop.refusals import CODES
from asop.sop import SopContractError

DET = {"kind": "deterministic", "check": "make check"}
READY = {"kind": "deterministic", "check": "check-source --final --approver --no-open-decisions",
         "cannot_evaluate_exit": 3}


def body(title, *, inputs=(), steps=None, **chain):
    b = {"title": title, "inputs": [{"name": n} for n in inputs], "roles": {"worker": {"kind": "agent"}},
         "steps": steps or [{"name": "do", "role": "worker", "purpose": "do it", "gate": DET}]}
    b.update(chain)
    return b


THREE_STEPS = [{"name": n, "role": "worker", "purpose": n, "gate": DET} for n in ("write-tests", "implement", "prove")]

PLAN = body(
    "Plan the work", inputs=["source"],
    entry_gates=[{"id": "source-ready", "over": ["source"], "gate": READY}],
    outcomes=["PLANNED", "NOTHING"],
    outputs=[{"name": "tasks"}],
    handoffs=[{
        "id": "build-each", "to": {"asop_id": "build", "version": 1}, "on_outcomes": ["PLANNED"],
        "cardinality": "many", "preflight": True,
        "subject_from": {"output": "tasks", "array": "tasks", "id_field": "id", "order_by": "depends_on",
                         "concurrency": 2, "approved_subset": True},
        "inputs": {"task": "$subject", "source": "$run.inputs.source"},
    }],
)
BUILD = body(
    "Build one task", inputs=["task", "source"], steps=THREE_STEPS,
    entry_gates=[{"id": "task-buildable", "over": ["task"], "gate": DET}],
    outcomes=["BUILT", "TESTS_DO_NOT_BITE"],
    handoffs=[{"id": "review", "to": {"asop_id": "review", "version": 1}, "on_outcomes": ["BUILT"],
               "inputs": {"source": "$run.inputs.source"}}],
    rework=[{"id": "rewrite-tests", "to": {"step": 1}, "on_step_failure": 3, "max_rounds": 2,
             "consumes_attempt": False}],
)
REVIEW = body(
    "Review the chain", inputs=["source"],
    outcomes=["ACCEPTED", "PLAN_FINDINGS"],
    outputs=[{"name": "findings"}],
    handoffs=[{"id": "close", "to": {"asop_id": "close", "version": 1}, "on_outcomes": ["ACCEPTED"]}],
    rework=[{"id": "replan", "to": {"asop_id": "plan", "versions": ">=1"}, "on_outcomes": ["PLAN_FINDINGS"],
             "findings": "findings", "max_rounds": 2, "consumes_attempt": False, "tier_bump": True}],
)
CLOSE = body("Close the chain",
             requires=[{"kind": "chain_gate", "id": "every-requirement-traced",
                        "gate": {"kind": "deterministic", "check": "trace --ledger", "cannot_evaluate_exit": 3}}])
LIBRARY = {"plan": PLAN, "build": BUILD, "review": REVIEW, "close": CLOSE}


def v(asop_id):
    return validate_asop(LIBRARY[asop_id], asop_id=asop_id)


def resolve(to):
    if to["asop_id"] in LIBRARY:
        return 1, v(to["asop_id"])
    return None


TASKS = {"tasks": [{"id": "T1", "depends_on": []}, {"id": "T2", "depends_on": ["T1"]},
                   {"id": "T3", "depends_on": ["T1"]}, {"id": "T4", "depends_on": ["T2", "T3"]}]}
APPROVED = {"tasks": {"digest": "sha256:aa", "subset": ["T1", "T2", "T3", "T4"]}}
DIGESTS = {"tasks": "sha256:aa"}


def plan_build(**kw):
    args = dict(outcome="PLANNED", outputs={"tasks": TASKS}, run_inputs={"source": "doc.md"}, requests=[],
                resolve=resolve, chain="C1", approvals=APPROVED, output_digests=DIGESTS)
    args.update(kw)
    return plan_handoffs(v("plan"), **args)


def show(ds):
    return [d["subject"] if d["action"] == "file" else d["code"] for d in ds]


# --- the records are valid, and activation accepts the chain -----------------

def test_the_worked_chain_activates():
    for a in LIBRARY:
        validate_handoff_targets(v(a), resolve, asop_id=a)


def test_a_v35_record_is_a_v36_record():
    plain = body("Standalone")
    assert validate_asop(plain) == validate_asop(dict(plain))
    assert "entry_gates" not in validate_asop(plain) and "rework" not in validate_asop(plain)


# --- entry gates (§3.8) ------------------------------------------------------

def test_entry_gate_reads_only_declared_inputs():
    with pytest.raises(SopContractError, match="does not declare"):
        validate_asop(body("x", inputs=["a"], entry_gates=[{"id": "g", "over": ["b"], "gate": DET}]))


def test_entry_gate_is_deterministic():
    with pytest.raises(SopContractError, match="deterministic"):
        validate_asop(body("x", inputs=["a"], entry_gates=[{"id": "g", "over": ["a"], "gate": {
            "kind": "judged", "check": "is it good"}}]))


def test_entry_gate_ids_are_unique_and_over_is_nonempty():
    with pytest.raises(SopContractError, match="twice"):
        validate_asop(body("x", inputs=["a"], entry_gates=[{"id": "g", "over": ["a"], "gate": DET}] * 2))
    with pytest.raises(SopContractError, match="non-empty LIST"):
        validate_asop(body("x", inputs=["a"], entry_gates=[{"id": "g", "over": [], "gate": DET}]))


def test_entry_all_passed_starts():
    assert plan_entry(v("build")["entry_gates"], outcomes={"task-buildable": "passed"},
                      producers={"task": "C1|plan|C1|a1"}) == {"action": "start"}


def test_entry_cannot_evaluate_refiles_the_producer_uncharged():
    d = plan_entry(v("build")["entry_gates"], outcomes={"task-buildable": "cannot_evaluate"},
                   producers={"task": "C1|plan|C1|a1"})
    assert d == {"action": "not_ready", "hold": [],
                 "refile": [{"natural_key": "C1|plan|C1|a1", "gates": ["task-buildable"], "charged": False}]}


def test_entry_failure_is_charged_to_the_producer_never_the_consumer():
    d = plan_entry(v("build")["entry_gates"], outcomes={"task-buildable": "failed"},
                   producers={"task": "C1|plan|C1|a1"})
    assert d["refile"][0]["charged"] is True


def test_readiness_gate_over_a_source_holds_for_a_person():
    """No producer in the chain: the source came in at chain start."""
    for got, reason in (("cannot_evaluate", "source_not_ready"), ("failed", "source_rejected")):
        d = plan_entry(v("plan")["entry_gates"], outcomes={"source-ready": got}, producers={"source": None})
        assert d == {"action": "not_ready", "refile": [],
                     "hold": [{"gate": "source-ready", "input": "source", "reason": reason}]}


def test_entry_unevaluated_waits():
    assert plan_entry(v("plan")["entry_gates"], outcomes={}, producers={})["action"] == "wait"


def test_preflight_hands_the_producer_the_consumers_lint():
    checks = preflight_checks(v("plan"), resolve)
    assert [(c["handoff"], c["entry_gate"], c["inputs"]) for c in checks] == \
        [("build-each", "task-buildable", {"task": "$subject"})]


def test_preflight_against_a_consumer_with_no_entry_gates_is_refused():
    lib = dict(LIBRARY, build=body("Build", inputs=["task", "source"]))
    with pytest.raises(ChainContractError, match="declares no entry gates"):
        validate_handoff_targets(v("plan"), lambda to: (1, validate_asop(lib[to["asop_id"]])), asop_id="plan")


# --- ordered, approved fan-outs (§3.7) ---------------------------------------

def test_fanout_files_with_dependencies_as_natural_keys():
    ds = plan_build()
    assert show(ds) == ["T1", "T2", "T3", "T4"]
    t4 = ds[-1]
    assert t4["depends_on"] == [natural_key("C1", "build", "T2"), natural_key("C1", "build", "T3")]


def test_startable_respects_order_and_concurrency():
    h = v("plan")["handoffs"][0]
    ds = plan_build()
    k = {d["subject"]: d["natural_key"] for d in ds}
    assert fanout_startable(h, ds) == [k["T1"]]
    assert fanout_startable(h, ds, started=[k["T1"]]) == []
    assert fanout_startable(h, ds, started=[k["T1"]], done=[k["T1"]]) == [k["T2"], k["T3"]]
    assert fanout_startable(h, ds, started=[k["T1"], k["T2"]], done=[k["T1"]]) == [k["T3"]]
    one_slot = {**h, "subject_from": {**h["subject_from"], "concurrency": 1}}
    assert fanout_startable(one_slot, ds, started=[k["T1"], k["T2"]], done=[k["T1"]]) == [], \
        "a running run holds its slot"
    both = [k["T1"], k["T2"], k["T3"]]
    assert fanout_startable(h, ds, started=both, done=both) == [k["T4"]]


def test_dependency_outside_the_fanout_is_refused_whole():
    tasks = {"tasks": [{"id": "T1", "depends_on": ["T9"]}]}
    ds = plan_build(outputs={"tasks": tasks}, approvals={"tasks": {"digest": "sha256:aa"}})
    assert show(ds) == ["handoff_refused:order"] and ds[0]["toward"] == ["build"]


def test_dependency_cycle_is_refused():
    tasks = {"tasks": [{"id": "A", "depends_on": ["B"]}, {"id": "B", "depends_on": ["A"]}]}
    ds = plan_build(outputs={"tasks": tasks}, approvals={"tasks": {"digest": "sha256:aa"}})
    assert show(ds) == ["handoff_refused:order"]


def test_requests_that_break_the_order_are_disregarded_not_obeyed():
    """A refused request is as if it had not been written (§3.7): a run asking
    for T4 alone cannot make the declared fan-out un-fileable."""
    req = {"schema": "asop.filing-request/1", "handoff": "build-each", "to": "build", "subject": "T4"}
    ds = plan_build(requests=[req])
    assert show(ds) == ["handoff_refused:order", "T1", "T2", "T3", "T4"]
    assert ds[0]["toward"] == []


def test_only_the_approved_subset_is_filed():
    ds = plan_build(approvals={"tasks": {"digest": "sha256:aa", "subset": ["T1", "T3"]}})
    assert show(ds) == ["T1", "T3"]


def test_approving_a_task_without_its_prerequisite_is_refused():
    ds = plan_build(approvals={"tasks": {"digest": "sha256:aa", "subset": ["T2"]}})
    assert show(ds) == ["handoff_refused:order"]


def test_stale_approval_refuses_the_fanout():
    ds = plan_build(output_digests={"tasks": "sha256:bb"})
    assert show(ds) == ["handoff_refused:approval"] and "stale" in ds[0]["detail"]


def test_missing_approval_refuses_the_fanout():
    assert show(plan_build(approvals={})) == ["handoff_refused:approval"]


def test_approval_naming_an_absent_subject_is_refused():
    ds = plan_build(approvals={"tasks": {"digest": "sha256:aa", "subset": ["T1", "T7"]}})
    assert show(ds) == ["handoff_refused:approval"] and "T7" in ds[0]["detail"]


def test_subject_from_v36_fields_are_typed():
    base = dict(PLAN["handoffs"][0])
    for sf, msg in (({"concurrency": 0}, "positive integer"), ({"approved_subset": False}, "true or absent"),
                    ({"order_by": "id"}, "id field")):
        h = dict(base, subject_from={**base["subject_from"], **sf})
        with pytest.raises(SopContractError, match=msg):
            validate_asop(dict(PLAN, handoffs=[h]))


def test_new_rules_are_catalogued():
    assert "handoff_refused:<rule>" in CODES and "rework_refused:<rule>" in CODES
    assert "adoption_required" in CODES


# --- requires (§3.7) -----------------------------------------------------------

def test_human_approved_bound_to_a_digest():
    req = [{"kind": "human_approved", "artefact": "plan-approval", "binds_digest": True}]
    rec = validate_asop(body("x", requires=req))["requires"]
    ok = {"plan-approval": {"digest": "sha256:aa"}}
    assert requires_satisfied(rec, chain="C1", runs=[], approvals=ok, digests={"plan-approval": "sha256:aa"})
    assert not requires_satisfied(rec, chain="C1", runs=[], approvals=ok, digests={"plan-approval": "sha256:bb"})
    assert not requires_satisfied(rec, chain="C1", runs=[], approved=["plan-approval"]), \
        "a bare name cannot satisfy an approval that must bind a digest: fail closed"


def test_human_approved_without_binding_behaves_as_in_v35():
    rec = validate_asop(body("x", requires=[{"kind": "human_approved", "artefact": "a"}]))["requires"]
    assert requires_satisfied(rec, chain="C1", runs=[], approved=["a"])


def test_chain_gate_must_have_passed():
    rec = v("close")["requires"]
    for got, expect in (("passed", True), ("failed", False), ("cannot_evaluate", False), (None, False)):
        gates = {} if got is None else {"every-requirement-traced": got}
        assert requires_satisfied(rec, chain="C1", runs=[], chain_gates=gates) is expect


def test_chain_gate_shape():
    with pytest.raises(SopContractError, match="exactly"):
        validate_asop(body("x", requires=[{"kind": "chain_gate", "gate": DET}]))
    with pytest.raises(SopContractError, match="twice"):
        validate_asop(body("x", requires=[{"kind": "chain_gate", "id": "g", "gate": DET}] * 2))


# --- rework (§3.9) -----------------------------------------------------------

def test_step_rework_reruns_from_the_earlier_step():
    d = plan_rework(v("build"), failed_step=3, chain="C1")
    assert d == {"action": "rework", "rework": "rewrite-tests", "round": 1, "consumes_attempt": False,
                 "tier_bump": False, "from_step": 1, "through_step": 3}


def test_rework_is_bounded_and_then_escalates():
    d = plan_rework(v("build"), failed_step=3, rounds_used={"rewrite-tests": 2})
    assert d == {"action": "escalate", "rework": "rewrite-tests", "reason": "rounds_exhausted", "rounds": 2}


def test_no_matching_edge_is_the_v35_path():
    assert plan_rework(v("build"), failed_step=2) is None
    assert plan_rework(v("review"), outcome="ACCEPTED") is None


OWNER = {"asop_id": "plan", "subject": "C1", "attempt": 1, "inputs": {"source": "doc.md"}}


def test_owner_is_refiled_with_findings_and_the_same_inputs():
    d = plan_rework(v("review"), outcome="PLAN_FINDINGS", outputs={"findings": "review/findings.json"},
                    owner=OWNER, resolve=resolve, chain="C1")
    assert d["action"] == "rework" and d["to"] == {"asop_id": "plan", "version": 1}
    assert d["attempt"] == 2 and d["natural_key"] == "C1|plan|C1|a2"
    assert d["inputs"] == {"source": "doc.md"} and d["prior_findings"] == "review/findings.json"
    assert d["tier_bump"] is True and d["consumes_attempt"] is False


def test_owner_rework_refusals():
    rv = v("review")
    assert plan_rework(rv, outcome="PLAN_FINDINGS", outputs={"findings": "f"}, resolve=resolve,
                       chain="C1")["code"] == "rework_refused:owner"
    assert plan_rework(rv, outcome="PLAN_FINDINGS", outputs={}, owner=OWNER, resolve=resolve,
                       chain="C1")["code"] == "rework_refused:findings"
    assert plan_rework(rv, outcome="PLAN_FINDINGS", outputs={"findings": {"a": 1}}, owner=OWNER, resolve=resolve,
                       chain="C1")["code"] == "rework_refused:findings"
    assert plan_rework(rv, outcome="PLAN_FINDINGS", outputs={"findings": "f"}, owner=OWNER,
                       resolve=lambda to: None, chain="C1")["code"] == "rework_refused:version"


@pytest.mark.parametrize("edge, msg", [
    ({"to": {"step": 2}, "on_step_failure": 2}, "not earlier"),
    ({"to": {"step": 1}, "on_step_failure": 9}, "on_step_failure"),
    ({"to": {"step": 5}, "on_step_failure": 3}, "must be a step"),
    ({"to": {"step": 1}, "on_step_failure": 3, "findings": "x"}, "nothing to read"),
    ({"to": {"step": 1}, "on_step_failure": 3, "max_rounds": MAX_REWORK_ROUNDS + 1}, "max_rounds"),
    ({"to": {"step": 1}, "on_step_failure": 3, "max_rounds": 0}, "max_rounds"),
    ({"to": {"step": 1}, "on_step_failure": 3, "consumes_attempt": None}, "no default"),
])
def test_step_rework_shape(edge, msg):
    e = {"id": "r", "max_rounds": 1, "consumes_attempt": True, **edge}
    e = {k: x for k, x in e.items() if x is not None}
    with pytest.raises(SopContractError, match=msg):
        validate_asop(dict(BUILD, rework=[e]))


def test_procedure_rework_may_not_share_an_outcome_with_a_handoff():
    e = dict(REVIEW["rework"][0], on_outcomes=["ACCEPTED"])
    with pytest.raises(SopContractError, match="does not also send it forward"):
        validate_asop(dict(REVIEW, rework=[e]))


def test_procedure_rework_to_itself_is_a_step_rework():
    e = dict(REVIEW["rework"][0], to={"asop_id": "review", "version": 1})
    with pytest.raises(SopContractError, match="its own ASOP"):
        validate_asop(dict(REVIEW, rework=[e]), asop_id="review")


def test_two_edges_on_one_trigger_are_refused():
    with pytest.raises(SopContractError, match="One trigger"):
        validate_asop(dict(REVIEW, rework=[REVIEW["rework"][0], dict(REVIEW["rework"][0], id="again")]))
    with pytest.raises(SopContractError, match="One trigger"):
        validate_asop(dict(BUILD, rework=[BUILD["rework"][0], dict(BUILD["rework"][0], id="again", to={"step": 2})]))


def test_rework_must_go_upstream():
    """`close` does not lead to `review`, so a rework from review to close is a
    hand-off by another name — and would escape the cycle ban."""
    e = dict(REVIEW["rework"][0], to={"asop_id": "close", "version": 1})
    rec = validate_asop(dict(REVIEW, rework=[e]), asop_id="review")
    with pytest.raises(ChainContractError, match="does not \\(transitively\\) hand off"):
        validate_handoff_targets(rec, resolve, asop_id="review")


def test_rework_edges_are_not_part_of_the_cycle_check():
    """review → plan by rework, plan → build → review by hand-offs: a cycle in
    the drawing, bounded by max_rounds, and activation accepts it."""
    validate_handoff_targets(v("review"), resolve, asop_id="review")
