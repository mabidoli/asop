"""Hand-offs between ASOPs (ASOP.md §3.7, v3.5): what a finished run may file
next, when the filed run may start, and what the engine refuses.

The worked chain is the one the spec uses: `intake` fans out one `extract` per
source, files one `conventions` run, and joins its fan-out into one
`consolidate` (a hand-off with `after`); every `extract` hands one `index` run
to the chain; `consolidate` hands to `plan`, which waits for `conventions` and a
person's approval; `plan` fans out one build per task, routed by the task's
kind, and joins them into `close`; procedures escalate to `decide`, which a
person takes part in."""

from __future__ import annotations

import pytest

from asop import validate_asop
from asop.chain import (
    FILING_REQUEST_SCHEMA,
    HANDOFF_RULES,
    ChainContractError,
    natural_key,
    parse_version_range,
    plan_escalation,
    plan_handoffs,
    plan_join,
    join_state,
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
    }, {
        "id": "conventions",
        "to": {"asop_id": "conventions", "version": 1},
        "on_outcomes": ["FETCHED", "NOTHING_TO_FETCH"],
        "inputs": {"root": "$run.inputs.root"},
    }, {
        "id": "consolidate",
        "to": {"asop_id": "consolidate", "version": 1},
        "on_outcomes": ["FETCHED", "NOTHING_TO_FETCH"],
        "after": ["extract-each-source"],
        "inputs": {"root": "$run.inputs.root"},
    }],
    on_escalate=ESCALATE,
)
EXTRACT = body(
    "Extract requirements from one source",
    inputs=["source_id", "title", "root"],
    handoffs=[{"id": "index", "to": {"asop_id": "index", "version": 1}, "inputs": {"root": "$run.inputs.root"}}],
    on_escalate=ESCALATE,
)
INDEX = body("Index the chain", inputs=["root"])
CONVENTIONS = body("Record the conventions", inputs=["root"])
CONSOLIDATE = body(
    "Consolidate requirements",
    inputs=["root"],
    handoffs=[{"id": "plan", "to": {"asop_id": "plan", "version": 1}, "inputs": {"root": "$run.inputs.root"}}],
)
CLOSE = body("Close the epic", inputs=["root"])
PLAN = body(
    "Plan the work",
    inputs=["root"],
    outputs=[{"name": "tasks"}],
    requires=[{"kind": "run_done", "asop_id": "conventions"},
              {"kind": "human_approved", "artefact": "research-review"}],
    handoffs=[
        {"id": "build-backend", "to": {"asop_id": "build", "version": 1}, "cardinality": "many",
         "subject_from": {"output": "tasks", "array": "tasks", "id_field": "id", "where": {"kind": "backend"}},
         "inputs": {"task_id": "$subject"}},
        {"id": "build-handoff", "to": {"asop_id": "build-handoff", "version": 1}, "cardinality": "many",
         "subject_from": {"output": "tasks", "array": "tasks", "id_field": "id", "where": {"kind": "handoff"}},
         "inputs": {"task_id": "$subject"}},
        {"id": "close", "to": {"asop_id": "close", "version": 1}, "after": ["build-backend", "build-handoff"],
         "inputs": {"root": "$run.inputs.root"}},
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
    "index": {1: INDEX}, "conventions": {1: CONVENTIONS}, "close": {1: CLOSE},
}


def resolve(to):
    for version, b in LIBRARY.get(to["asop_id"], {}).items():
        if version_in(to, version):
            return version, validate_asop(b, asop_id=to["asop_id"])
    return None


def v(b, asop_id=None):
    return validate_asop(b, asop_id=asop_id)


SOURCES = {"sources": [
    {"id": "s1", "kind": "spec", "title": "Spec one"},
    {"id": "s2", "kind": "feature", "title": "Feature two"},
    {"id": "s3", "kind": "ticket", "title": "Not extracted"},
    {"id": "s4", "kind": "spec", "title": "Withdrawn", "removed": True},
]}


def plan_intake(outcome="FETCHED", requests=(), outputs=None, existing=(), attempt=1):
    return plan_handoffs(v(INTAKE, "intake"), outcome=outcome,
                         outputs={"sources": SOURCES} if outputs is None else outputs,
                         run_inputs={"root": "R1"}, requests=None if requests is None else list(requests),
                         resolve=resolve, chain="R1", existing=existing, attempt=attempt)


def plan_extract(requests=(), existing=(), outcome=None):
    return plan_handoffs(v(EXTRACT, "extract"), outcome=outcome, outputs={}, run_inputs={"root": "R1"},
                         requests=list(requests), resolve=resolve, chain="R1", existing=existing)


def req(**over):
    r = {"schema": FILING_REQUEST_SCHEMA, "handoff": "extract-each-source", "to": "extract", "subject": "s1"}
    r.update(over)
    return r


ONE_REQ = {"schema": FILING_REQUEST_SCHEMA, "handoff": "index", "to": "index"}


def filed(decisions):
    return [d for d in decisions if d["action"] == "file"]


def subjects(decisions):
    # intake's one `conventions` run files beside every fan-out; leave it out
    return [d["subject"] for d in filed(decisions) if d["handoff"] != "conventions"]


def refused(decisions):
    return [d["code"] for d in decisions if d["action"] == "refuse"]


# ------------------------------------------------------------------ the record

def test_a_record_without_chaining_is_unchanged():
    clean = v(BUILD)
    for f in ("outcomes", "outputs", "handoffs", "on_escalate", "requires"):
        assert f not in clean


def test_every_record_in_the_worked_chain_validates():
    for asop_id, versions in LIBRARY.items():
        for b in versions.values():
            validate_handoff_targets(v(b, asop_id), resolve, asop_id=asop_id)


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


@pytest.mark.parametrize("kind", ["slot_free", "artefact_current", "no_run_for_subject", "all_runs_done", "whenever"])
def test_requires_kinds_are_a_closed_list(kind):
    with pytest.raises(SopContractError, match="not (one of|a requires kind)"):
        v(body("x", requires=[{"kind": kind, "asop_id": "y"}]))


def test_all_runs_done_points_to_joins():
    # withdrawn before release; the refusal says where a join goes instead
    with pytest.raises(SopContractError, match="procedure that fans out"):
        v(body("x", requires=[{"kind": "all_runs_done", "asop_id": "y"}]))


@pytest.mark.parametrize("kind", [["run_done"], {"a": 1}, 7, None])
def test_a_non_string_requires_kind_is_refused_not_a_crash(kind):
    # review M6: an unhashable kind raised TypeError through validate_asop
    with pytest.raises(SopContractError, match="'kind' must be one of"):
        v(body("x", requires=[{"kind": kind, "asop_id": "y"}]))


def test_subject_references_exist_only_on_a_fan_out():
    bad = body("x", handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}, "inputs": {"task_id": "$subject"}}])
    with pytest.raises(SopContractError, match="only on a `many`"):
        v(bad)


def test_a_subject_field_is_one_key_not_a_path():
    # review M11: the rule is stated, and enforced where it is written
    bad = body("x", outputs=[{"name": "o"}],
               handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}, "cardinality": "many",
                          "subject_from": {"output": "o", "array": "a.b", "id_field": "id"},
                          "inputs": {"task_id": "$subject.m.n"}}])
    with pytest.raises(SopContractError, match="one field"):
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


def test_two_handoffs_to_one_target_are_refused():
    # review H5(b): they would share natural keys and the second would vanish as a duplicate
    bad = body("x", handoffs=[{"id": "h1", "to": {"asop_id": "build", "version": 1}, "inputs": {"task_id": "a"}},
                              {"id": "h2", "to": {"asop_id": "build", "version": 1}, "inputs": {"task_id": "b"}}])
    with pytest.raises(SopContractError, match="One hand-off per target"):
        v(bad)


@pytest.mark.parametrize("raw", [
    {"inputs": {"a": "one", " a": "two"}},
    {"inputs": {"a ": "one"}},
])
def test_names_with_surrounding_whitespace_are_refused_not_stripped(raw):
    # review M7: stripping made {"a": "one", " a": "two"} come back as {"a": "two"}
    with pytest.raises(SopContractError, match="surrounding whitespace"):
        v(body("x", handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}, **raw}]))


@pytest.mark.parametrize("to,match", [
    ({"asop": "build", "version": 1}, "'asop_id'"),
    ({"asop_id": "build"}, "exactly one of"),
    ({"asop_id": "build", "version": 1, "versions": ">=1"}, "exactly one of"),
    ({"asop_id": "build", "versions": "^1"}, "only two forms"),
    ({"asop_id": "build", "versions": ">=3,<3"}, "empty"),
    ({"asop_id": "build", "versions": ">=0"}, "only two forms"),
    ({"asop_id": "build", "versions": ">=٣"}, "only two forms"),  # Arabic-Indic three
    ({"asop_id": "build", "versions": ">=3\n"}, "only two forms"),
    ({"asop_id": "build", "versions": ">= 3"}, "only two forms"),
])
def test_targets_are_a_pin_or_a_strict_range(to, match):
    # review M8: the range grammar is ASCII, unspaced, N >= 1, M > N — in the
    # Python, the schema pattern and the prose alike
    with pytest.raises(SopContractError, match=match):
        v(body("x", handoffs=[{"id": "h", "to": to}]))


def test_version_ranges():
    assert parse_version_range(">=2,<4") == (2, 4)
    assert version_in({"asop_id": "a", "versions": ">=2"}, 2)
    assert version_in({"asop_id": "a", "versions": ">=2,<4"}, 3)
    assert not version_in({"asop_id": "a", "versions": ">=2,<4"}, 4)
    assert not version_in({"asop_id": "a", "versions": ">=2"}, 1)
    assert version_in({"asop_id": "a", "version": 2}, 2) and not version_in({"asop_id": "a", "version": 2}, 3)


def test_the_natural_key_cannot_be_forged_with_a_separator():
    # review L13
    assert natural_key("C", "x|y", "z") != natural_key("C", "x", "y|z")
    assert natural_key("C", "x", "a%7Cb") != natural_key("C", "x", "a|b")


# ------------------------------------------------------------------ across records

def test_a_target_that_does_not_declare_the_input_is_refused():
    bad = v(body("x", handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1},
                                 "inputs": {"task_id": "t", "colour": "blue"}}]))
    with pytest.raises(ChainContractError, match="does not declare"):
        validate_handoff_targets(bad, resolve, asop_id="x")


def test_a_target_left_with_an_unmapped_input_is_refused():
    # review H4: a run cannot fill the gap (requests name subjects, never
    # inputs), so the filing would always be refused — say so at activation
    bad = v(body("x", handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}}]))
    with pytest.raises(ChainContractError, match="unmapped"):
        validate_handoff_targets(bad, resolve, asop_id="x")


def test_an_unresolvable_target_is_refused():
    bad = v(body("x", handoffs=[{"id": "h", "to": {"asop_id": "nowhere", "version": 1}}]))
    with pytest.raises(ChainContractError, match="no ACTIVE version"):
        validate_handoff_targets(bad, resolve, asop_id="x")


def test_an_escalation_must_reach_a_person():
    bad = v(body("x", on_escalate={"to": {"asop_id": "build", "version": 1}, "inputs": {}}))
    with pytest.raises(ChainContractError, match="reaches a person"):
        validate_handoff_targets(bad, resolve, asop_id="x")


def test_a_cycle_through_other_records_is_refused():
    # review M12: A→B→A passed the self-reference check
    saved = dict(LIBRARY)
    LIBRARY["a"] = {1: body("A", handoffs=[{"id": "h", "to": {"asop_id": "b", "version": 1}}])}
    LIBRARY["b"] = {1: body("B", handoffs=[{"id": "h", "to": {"asop_id": "a", "version": 1}}])}
    try:
        with pytest.raises(ChainContractError, match="cycle: a → b → a"):
            validate_handoff_targets(v(LIBRARY["a"][1], "a"), lambda to: (1, v(LIBRARY[to["asop_id"]][1], to["asop_id"])),
                                     asop_id="a")
    finally:
        LIBRARY.clear(); LIBRARY.update(saved)


def with_library(extra):
    saved = dict(LIBRARY)
    LIBRARY.update({k: {1: b} for k, b in extra.items()})
    return saved


def test_a_requires_on_something_this_asop_files_is_refused():
    # review N1, simple form: W hands off to X and waits for X — it can never start
    saved = with_library({"x": BUILD})
    try:
        w = v(body("W", handoffs=[{"id": "h", "to": {"asop_id": "x", "version": 1}, "inputs": {"task_id": "t"}}],
                   requires=[{"kind": "run_done", "asop_id": "x"}]), "w")
        with pytest.raises(ChainContractError, match="wait forever"):
            validate_handoff_targets(w, resolve, asop_id="w")
    finally:
        LIBRARY.clear(); LIBRARY.update(saved)


def test_a_requires_on_a_transitive_descendant_is_refused():
    saved = with_library({"mid": body("Mid", inputs=["root"], handoffs=[
        {"id": "h", "to": {"asop_id": "build", "version": 1}, "inputs": {"task_id": "$run.inputs.root"}}])})
    try:
        w = v(body("W", handoffs=[{"id": "h", "to": {"asop_id": "mid", "version": 1}, "inputs": {"root": "r"}}],
                   requires=[{"kind": "run_done", "asop_id": "build"}]), "w")
        with pytest.raises(ChainContractError, match="wait forever"):
            validate_handoff_targets(w, resolve, asop_id="w")
    finally:
        LIBRARY.clear(); LIBRARY.update(saved)


def test_an_escalation_loop_with_a_person_in_it_is_allowed():
    # review N3: decide -> apply, apply escalates to decide, is bounded by the person
    saved = with_library({
        "decide2": body("Decide", inputs=["question"], roles={"owner": {"kind": "human"}},
                        steps=[{"name": "answer", "role": "owner", "purpose": "answer", "gate": HUMAN}],
                        handoffs=[{"id": "apply", "to": {"asop_id": "apply", "version": 1}, "inputs": {}}]),
        "apply": body("Apply", on_escalate={"to": {"asop_id": "decide2", "version": 1}, "inputs": {}}),
    })
    try:
        for k in ("decide2", "apply"):
            validate_handoff_targets(v(LIBRARY[k][1], k), resolve, asop_id=k)
    finally:
        LIBRARY.clear(); LIBRARY.update(saved)


def test_cycle_detection_needs_the_records_own_id():
    # review N3: without it, A->B->A passed while A was not yet active
    with pytest.raises(ChainContractError):
        validate_handoff_targets(v(BUILD), resolve, asop_id="")
    with pytest.raises(TypeError):
        validate_handoff_targets(v(BUILD), resolve)  # keyword is required


# ------------------------------------------------------------------ filing

def test_a_fan_out_expands_from_the_output_with_its_filters():
    d = plan_intake()
    assert subjects(d) == ["s1", "s2"]
    assert filed(d)[0] == {
        "action": "file", "handoff": "extract-each-source", "to": {"asop_id": "extract", "version": 2},
        "subject": "s1", "inputs": {"source_id": "s1", "title": "Spec one", "root": "R1"},
        "natural_key": "R1|extract|s1|a1",
    }
    assert refused(d) == []


def test_an_outcome_outside_on_outcomes_files_nothing():
    assert plan_intake(outcome="BLOCKED") == []


@pytest.mark.parametrize("outcome", ["OKAY", None])
def test_an_undeclared_outcome_is_refused_not_silent(outcome):
    # review H2: a closed set is closed — an outcome outside it was silently
    # suppressing every hand-off
    assert refused(plan_intake(outcome=outcome)) == ["handoff_refused:outcome"]
    assert filed(plan_intake(outcome=outcome)) == []


def test_an_empty_fan_out_files_nothing_and_is_not_an_error():
    d = plan_intake(outputs={"sources": {"sources": []}})
    assert subjects(d) == [] and refused(d) == []


def test_requests_narrow_a_fan_out():
    assert subjects(plan_intake(requests=[req(subject="s2")])) == ["s2"]


def test_a_refused_request_never_cancels_the_declared_expansion():
    # review H1: one bogus request suppressed the whole fan-out — option B's hole
    for bad in (req(subject="nope"), req(subject="s3"), req(handoff="extract-all"),
                req(to="build"), {"handoff": "extract-each-source"}, req(inputs={"root": "X"})):
        d = plan_intake(requests=[bad])
        assert len(refused(d)) == 1, bad
        assert subjects(d) == ["s1", "s2"], bad


def test_good_requests_still_narrow_beside_a_refused_one():
    d = plan_intake(requests=[req(subject="nope"), req(subject="s2")])
    assert refused(d) == ["handoff_refused:subject"]
    assert subjects(d) == ["s2"]


def test_a_request_on_the_wrong_outcome_is_refused():
    d = plan_intake(outcome="BLOCKED", requests=[req()])
    assert refused(d) == ["handoff_refused:outcome"]
    assert filed(d) == []


def test_a_request_carries_no_inputs():
    # review H4/M8: a run chooses which, never what — so there is nothing to override
    with pytest.raises(ChainContractError, match="no 'inputs'"):
        validate_filing_request(req(inputs={"root": "R1"}))


def test_requests_that_are_not_a_list_are_refused():
    d = plan_intake(requests=None)
    assert subjects(d) == ["s1", "s2"]
    d = plan_handoffs(v(INTAKE, "intake"), outcome="FETCHED", outputs={"sources": SOURCES},
                      run_inputs={"root": "R1"}, requests={"schema": 1}, resolve=resolve, chain="R1")
    assert refused(d) == ["handoff_refused:malformed"] and subjects(d) == ["s1", "s2"]


def test_a_one_handoff_with_two_requests_refuses_them_and_still_files():
    d = plan_extract(requests=[ONE_REQ, ONE_REQ])
    assert refused(d) == ["handoff_refused:cardinality"]
    assert subjects(d) == ["R1"]


def test_a_one_handoff_may_not_choose_its_subject_but_still_files():
    d = plan_extract(requests=[{**ONE_REQ, "subject": "elsewhere"}])
    assert refused(d) == ["handoff_refused:subject"]
    assert subjects(d) == ["R1"]


def test_a_chain_run_is_filed_once_however_many_predecessors_hand_off():
    # NOT a join: the run is filed when the FIRST extract finishes. Review
    # round 3 (B2) showed a per-subject `one` hand-off used as a join either
    # conflicts for ever or drops inputs; a join is `after`, on the fan-out.
    filed_so_far: dict = {}
    actions = []
    for _ in range(3):  # three extract runs finish
        d = plan_extract(existing=filed_so_far)
        actions += [x["action"] for x in d]
        filed_so_far.update({x["natural_key"]: x["inputs"] for x in filed(d)})
    assert actions == ["file", "duplicate", "duplicate"]
    assert set(filed_so_far) == {natural_key("R1", "index", "R1")}


def test_a_duplicate_with_different_inputs_is_a_conflict():
    # review H5(c): a chain run's inputs were whichever predecessor finished first
    d = plan_extract(existing={"R1|index|R1|a1": {"root": "SOMEWHERE-ELSE"}})
    assert refused(d) == ["handoff_refused:conflict"]


def test_a_replay_files_nothing_twice():
    first = plan_intake()
    again = plan_intake(existing={x["natural_key"]: x["inputs"] for x in filed(first)})
    assert filed(again) == [] and {x["action"] for x in again} == {"duplicate", "wait"}
    assert plan_intake(existing=[x["natural_key"] for x in filed(first)])[0]["action"] == "duplicate"


def test_a_stale_refile_files_the_next_attempt():
    # review H5(a): attempt was hard-coded to 1, so re-filing on staleness was a duplicate
    first = plan_intake()
    again = plan_intake(existing={x["natural_key"]: x["inputs"] for x in filed(first)}, attempt=2)
    assert [x["natural_key"] for x in filed(again)] == ["R1|extract|s1|a2", "R1|extract|s2|a2",
                                                        "R1|conventions|R1|a2"]


def test_a_target_with_no_active_version_in_range_is_refused():
    saved = LIBRARY["extract"]
    LIBRARY["extract"] = {1: EXTRACT}  # only v1 active; the range is >=2
    try:
        d = plan_intake()
    finally:
        LIBRARY["extract"] = saved
    assert refused(d) == ["handoff_refused:version"]
    assert [x["action"] for x in d] == ["refuse", "file", "wait"]  # conventions still files; the join waits


@pytest.mark.parametrize("out", [
    {"sources": {"items": []}},                                   # no such array
    {"sources": {"sources": [{"kind": "spec"}]}},                 # no id
    {"sources": {"sources": [{"id": True, "kind": "spec"}]}},     # bool id
    {"sources": {"sources": [{"id": {"k": 1}, "kind": "spec"}]}},  # structure id
    {"sources": {"sources": [{"id": 1, "kind": "spec"}, {"id": "1", "kind": "spec"}]}},  # same subject
    {"sources": [{"id": "s1", "kind": "spec"}]},                  # output is not a mapping
    {},                                                           # output not written
])
def test_an_output_without_the_declared_shape_is_refused(out):
    # review M9/M11: ids are strings or integers, distinct as subjects; the output is a mapping
    assert refused(plan_intake(outputs=out)) == ["handoff_refused:output"]


def test_where_is_type_strict():
    # review M9: `where {flag: [true]}` matched `flag: 1`
    b = v(body("x", outputs=[{"name": "o"}], handoffs=[{
        "id": "h", "to": {"asop_id": "build", "version": 1}, "cardinality": "many",
        "subject_from": {"output": "o", "array": "items", "id_field": "id", "where": {"flag": [True]},
                         "where_not": {"n": 1}},
        "inputs": {"task_id": "$subject"}}]))
    out = {"o": {"items": [{"id": "a", "flag": 1}, {"id": "b", "flag": True}, {"id": "c", "flag": True, "n": True},
                           {"id": "d", "flag": True, "n": 1}, {"id": "e"}]}}
    d = plan_handoffs(b, outcome=None, outputs=out, run_inputs={}, requests=[], resolve=resolve, chain="C")
    assert subjects(d) == ["b", "c"]


def test_a_structured_value_is_not_handed_on():
    # review P4: a dict or list in an input bypassed the scalar rule
    out = {"sources": {"sources": [{"id": "s1", "kind": "spec", "title": {"en": "x"}}]}}
    assert refused(plan_intake(outputs=out)) == ["handoff_refused:inputs"]


def test_fan_out_routes_by_kind_across_handoffs():
    tasks = {"tasks": [{"id": "t1", "kind": "backend"}, {"id": "t2", "kind": "handoff"},
                       {"id": "t3", "kind": "backend"}, {"id": "t4", "kind": "frontend"}]}
    d = plan_handoffs(v(PLAN, "plan"), outcome=None, outputs={"tasks": tasks}, run_inputs={"root": "R1"},
                      requests=[], resolve=resolve, chain="R1")
    assert [(x["to"]["asop_id"], x["subject"]) for x in filed(d)] == [
        ("build", "t1"), ("build", "t3"), ("build-handoff", "t2")]
    assert d[-1] == {"action": "wait", "handoff": "close", "after": ["build-backend", "build-handoff"]}


def test_a_literal_dollar_is_escaped():
    b = v(body("x", handoffs=[{"id": "h", "to": {"asop_id": "build", "version": 1}, "inputs": {"task_id": "$$5"}}]))
    d = plan_handoffs(b, outcome=None, outputs={}, run_inputs={}, requests=[], resolve=resolve, chain="C")
    assert filed(d)[0]["inputs"] == {"task_id": "$5"}


def test_every_handoff_rule_is_a_catalogued_refusal():
    assert "handoff_refused:<rule>" in CODES
    # the nine of v3.5, unchanged, plus v3.6's two (additive: a new rule refuses
    # only what a v3.6 declaration asks for)
    assert set(HANDOFF_RULES) == {"malformed", "undeclared", "outcome", "cardinality", "subject",
                                  "output", "inputs", "version", "conflict", "order", "approval"}


def test_filing_request_shape():
    assert validate_filing_request(req(subject=7))["subject"] == "7"
    for bad in (req(schema="asop.filing-request/2"), req(priority="high"), req(subject="  s1  "),
                req(subject=""), req(subject=True), req(handoff=" extract-each-source")):
        with pytest.raises(ChainContractError):
            validate_filing_request(bad)


# ------------------------------------------------------------------ escalation

def test_an_escalation_maps_then_fills_from_context():
    d = plan_escalation(v(INTAKE, "intake"), subject="intake/R1/1", run_inputs={"root": "R1"},
                        context={"question": "the source is unreadable", "question_kind": "IGNORED", "noise": 1},
                        resolve=resolve, chain="R1")
    assert d["action"] == "file" and d["to"] == {"asop_id": "decide", "version": 3}
    assert d["inputs"] == {"question_kind": "escalation", "question": "the source is unreadable"}
    assert d["natural_key"] == "R1|decide|intake/R1/1|a1"


def test_an_escalation_missing_an_input_is_refused():
    d = plan_escalation(v(INTAKE, "intake"), subject="intake/R1/1", run_inputs={}, context={},
                        resolve=resolve, chain="R1")
    assert d["code"] == "handoff_refused:inputs"


def test_no_on_escalate_means_the_gate_escalation_applies():
    assert plan_escalation(v(BUILD), subject="s", run_inputs={}, context={}, resolve=resolve, chain="C") is None


# ------------------------------------------------------------------ joins

def keys(decisions, handoff="extract-each-source"):
    return [d["natural_key"] for d in decisions if d.get("handoff") == handoff and d["action"] in ("file", "duplicate")]


AFTER = ["extract-each-source"]


def test_a_join_is_recorded_waiting_not_filed_at_completion():
    d = plan_intake()
    assert {"action": "wait", "handoff": "consolidate", "after": AFTER} in d
    assert not [x for x in filed(d) if x["to"]["asop_id"] == "consolidate"]


def test_a_join_waits_for_every_run_its_fan_out_filed():
    # review H3 / round 3: the first finisher must not fire the join
    d = plan_intake()
    s1, s2 = keys(d)
    assert join_state(AFTER, decisions=d) == "wait"
    assert join_state(AFTER, decisions=d, done=[s1]) == "wait"
    assert join_state(AFTER, decisions=d, done=[s1, s2]) == "ready"


def test_an_empty_fan_out_makes_its_join_ready():
    # the deadlock this rule exists to prevent
    d = plan_intake(outputs={"sources": {"sources": []}}, outcome="NOTHING_TO_FETCH")
    assert join_state(AFTER, decisions=d) == "ready"


def test_a_refused_fan_out_blocks_its_join_instead_of_reading_as_empty():
    for d in (plan_intake(outputs={"sources": {"items": []}}),        # output
              plan_intake(outputs={"sources": {"sources": [{"id": "s1", "kind": "spec", "title": {"x": 1}}]}})):  # inputs
        assert join_state(AFTER, decisions=d, done=keys(d)) == "blocked"


def test_a_refused_request_does_not_block_the_join():
    d = plan_intake(requests=[req(subject="nope")])
    assert join_state(AFTER, decisions=d, done=keys(d)) == "ready"


def test_a_failed_run_blocks_and_a_rerun_to_done_clears_it():
    d = plan_intake()
    s1, s2 = keys(d)
    assert join_state(AFTER, decisions=d, done=[s1], failed=[s2]) == "blocked"
    assert join_state(AFTER, decisions=d, done=[s1, s2], failed=[s2]) == "ready"


def test_runs_filed_by_other_paths_are_not_waited_on():
    # review round 3 (B1): an escalation's decision re-filing extract must not
    # reopen (or be needed by) a join it is not part of
    d = plan_intake()
    elsewhere = natural_key("R1", "extract", "s9")
    assert join_state(AFTER, decisions=d, done=keys(d)) == "ready"
    assert join_state(AFTER, decisions=d + [{"action": "file", "handoff": "conventions",
                                              "natural_key": natural_key("R1", "conventions", "R1")}],
                      done=keys(d), failed=[elsewhere]) == "ready"


def test_a_duplicate_the_fan_out_relies_on_is_waited_on():
    first = plan_intake()
    again = plan_intake(existing={x["natural_key"]: x["inputs"] for x in filed(first)})
    assert join_state(AFTER, decisions=again) == "wait"
    assert join_state(AFTER, decisions=again, done=keys(first)) == "ready"


def test_a_join_over_two_fan_outs_waits_for_both():
    tasks = {"tasks": [{"id": "t1", "kind": "backend"}, {"id": "t2", "kind": "handoff"}]}
    d = plan_handoffs(v(PLAN, "plan"), outcome=None, outputs={"tasks": tasks}, run_inputs={"root": "R1"},
                      requests=[], resolve=resolve, chain="R1")
    after = ["build-backend", "build-handoff"]
    (b1,), (h1,) = keys(d, "build-backend"), keys(d, "build-handoff")
    assert join_state(after, decisions=d, done=[b1]) == "wait"
    assert join_state(after, decisions=d, done=[b1, h1]) == "ready"


def test_a_join_after_a_join_waits_until_the_first_is_filed_and_done():
    first = {"action": "wait", "handoff": "merge", "after": AFTER}
    filed_join = {"action": "file", "handoff": "merge", "natural_key": "R1|consolidate|R1|a1"}
    assert join_state(["merge"], decisions=[first]) == "wait"
    assert join_state(["merge"], decisions=[first, filed_join]) == "wait"
    assert join_state(["merge"], decisions=[first, filed_join], done=["R1|consolidate|R1|a1"]) == "ready"
    refused_join = {"action": "refuse", "handoff": "merge", "code": "handoff_refused:conflict", "toward": ["consolidate"]}
    assert join_state(["merge"], decisions=[first, refused_join]) == "blocked"


def test_a_request_naming_a_join_subject_does_not_unblock_a_waiting_join():
    # a refused request on a join must not count as the join having been filed
    d = plan_intake(requests=[{"schema": FILING_REQUEST_SCHEMA, "handoff": "consolidate", "to": "consolidate",
                               "subject": "s1"}])
    assert "handoff_refused:subject" in refused(d)
    assert join_state(["consolidate"], decisions=d) == "wait"


def plan_consolidate(outcome="FETCHED", existing=()):
    return plan_join(v(INTAKE, "intake"), "consolidate", outcome=outcome, outputs={"sources": SOURCES},
                     run_inputs={"root": "R1"}, resolve=resolve, chain="R1", existing=existing)


def test_a_ready_join_files_one_chain_run_with_the_parents_inputs():
    assert plan_consolidate() == [{
        "action": "file", "handoff": "consolidate", "to": {"asop_id": "consolidate", "version": 1},
        "subject": "R1", "inputs": {"root": "R1"}, "natural_key": "R1|consolidate|R1|a1"}]


def test_a_join_filed_twice_is_a_duplicate_and_with_other_inputs_a_conflict():
    assert [x["action"] for x in plan_consolidate(existing={"R1|consolidate|R1|a1": {"root": "R1"}})] == ["duplicate"]
    assert refused(plan_consolidate(existing={"R1|consolidate|R1|a1": {"root": "X"}})) == ["handoff_refused:conflict"]


def test_a_join_resolves_its_target_when_it_fires():
    # review round 3 (B3): nothing about a join is fixed by the graph at completion
    saved = LIBRARY["consolidate"]
    LIBRARY["consolidate"] = {}
    try:
        assert refused(plan_consolidate()) == ["handoff_refused:version"]
    finally:
        LIBRARY["consolidate"] = saved


def test_a_join_not_fired_by_the_outcome_files_nothing():
    assert plan_consolidate(outcome="BLOCKED") == []
    assert plan_consolidate(outcome="OKAY") == []


def test_plan_join_refuses_what_is_not_a_join():
    with pytest.raises(ChainContractError, match="not a join"):
        plan_join(v(INTAKE, "intake"), "extract-each-source", outcome="FETCHED", outputs={}, run_inputs={},
                  resolve=resolve, chain="R1")


@pytest.mark.parametrize("handoffs,match", [
    ([{"id": "j", "to": {"asop_id": "b", "version": 1}, "after": ["h"]},
      {"id": "h", "to": {"asop_id": "a", "version": 1}}], "declared EARLIER"),
    ([{"id": "j", "to": {"asop_id": "b", "version": 1}, "after": ["j"]}], "names itself"),
    ([{"id": "h", "to": {"asop_id": "a", "version": 1}},
      {"id": "j", "to": {"asop_id": "b", "version": 1}, "after": ["h", "h"]}], "twice"),
    ([{"id": "h", "to": {"asop_id": "a", "version": 1}},
      {"id": "j", "to": {"asop_id": "b", "version": 1}, "after": "h"}], "LIST"),
    ([{"id": "h", "to": {"asop_id": "a", "version": 1}},
      {"id": "j", "to": {"asop_id": "b", "version": 1}, "after": []}], "LIST"),
    ([{"id": "h", "to": {"asop_id": "a", "version": 1}},
      {"id": "j", "to": {"asop_id": "b", "version": 1}, "cardinality": "many", "after": ["h"],
       "subject_from": {"output": "o", "array": "a", "id_field": "id"}}], "Make it `one`"),
])
def test_a_join_names_earlier_handoffs_and_is_one(handoffs, match):
    with pytest.raises(SopContractError, match=match):
        v(body("x", outputs=[{"name": "o"}], handoffs=handoffs))


# ------------------------------------------------------------------ starting

def run(a, s, n=1, done=True):
    return {"asop_id": a, "subject": s, "attempt": n, "done": done}


CONV = [{"kind": "run_done", "asop_id": "conventions"}]


def ok(reqs, *runs_, approved=(), chain="R1"):
    return requires_satisfied(reqs, chain=chain, runs=list(runs_), approved=approved)


def test_run_done_is_the_chain_run():
    assert not ok(CONV)
    assert not ok(CONV, run("conventions", "R1", done=False))
    assert ok(CONV, run("conventions", "R1"))


def test_per_subject_runs_never_satisfy_run_done():
    # waiting on a fan-out is a join, declared on the procedure that fans out
    assert not ok([{"kind": "run_done", "asop_id": "extract"}], run("extract", "s1"), run("extract", "s2"))


def test_only_the_latest_attempt_counts():
    assert ok(CONV, run("conventions", "R1", 1, done=False), run("conventions", "R1", 2))
    assert not ok(CONV, run("conventions", "R1", 1), run("conventions", "R1", 2, done=False))
    # by attempt number, not by the order a ledger happens to list them in
    assert not ok(CONV, run("conventions", "R1", 2, done=False), run("conventions", "R1", 1))


def test_requires_is_evaluated_within_one_chain():
    assert not ok(CONV, run("conventions", "R1"), chain="R2")
    assert ok(CONV, run("conventions", 7), chain="7")


def test_every_predicate_must_hold():
    reqs = v(PLAN, "plan")["requires"]
    done = [run("conventions", "R1")]
    assert not ok(reqs, *done)
    assert not ok(reqs, approved=["research-review"])
    assert ok(reqs, *done, approved=["research-review"])


def test_every_refusal_says_what_it_leaves_unfiled():
    # review N4/P7: `toward` is what blocks a join; an undeclared outcome
    # leaves every target unfiled, a refused request leaves none
    d = plan_intake(outcome="OKAY")
    assert d[0]["toward"] == ["consolidate", "conventions", "extract"]
    d = plan_intake(requests=[req(subject="nope")])
    assert [x["toward"] for x in d if x["action"] == "refuse"] == [[]]
    d = plan_intake(outputs={"sources": {"items": []}})
    assert d[0]["toward"] == ["extract"]
    d = plan_extract(existing={"R1|index|R1|a1": {"root": "ELSEWHERE"}})
    assert d[0]["toward"] == ["index"]


def test_a_blank_escalation_subject_is_refused_not_raised():
    d = plan_escalation(v(INTAKE, "intake"), subject=" ", run_inputs={"root": "R1"}, context={"question": "q"},
                        resolve=resolve, chain="R1")
    assert d["code"] == "handoff_refused:malformed" and d["toward"] == ["decide"]


def test_an_output_description_is_text_not_a_name():
    # review M8: the schema used exactName for descriptions; they are prose, trimmed like every description
    assert v(body("x", outputs=[{"name": "o", "description": " d "}]))["outputs"][0]["description"] == "d"
