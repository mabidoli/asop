"""Hand-offs — how one ASOP's finished run files the next (ASOP.md §3.7).

v3 kept sequencing between procedures out of the artefact (§11.4, 2026-09-04)
and put it in the harness's configuration. The first real chained execution —
ten procedures run end to end over a multi-part programme — showed what that
costs: the flow lived in one engine's rules file, an ASOP shared to another
harness arrived without its place in any flow, and porting the engine meant
porting that flow with it. v3.5 moves the *declaration* into the artefact and
leaves the *execution* where it was:

- the predecessor declares where it MAY hand off (`handoffs`), on which of its
  own declared `outcomes`, and exactly how each successor input is computed;
- a JOIN is declared by the procedure that fans out, as a hand-off with
  `after`: "when every run my listed hand-offs filed is finished, file this".
  The engine filed those runs itself, so it knows exactly which to wait for —
  no reasoning over a graph that escalations and new versions keep moving;
- the successor declares only what it needs from OUTSIDE its own fan-in before
  it may START (`requires`): one named chain run done, a human approval;
- a finished run MAY narrow a fan-out by writing filing requests
  (`asop.filing-request/1`). A run chooses WHICH, never WHAT: a request names
  subjects, never inputs;
- a generic engine runs what is declared and REFUSES anything that is not. A
  refused request is as if it had not been written; it never cancels what the
  ASOP declared, and it is never repaired.

What stays in the engine — concurrency, budgets, staleness, retries — is listed
in ASOP.md §3.7 and is deliberately not ASOP data: it is the same for every flow.

Every decision here is a pure function, so any implementation can be checked
against the same conformance vectors:

- `validate_chain_fields`     is this record's chaining well formed? (authoring)
- `validate_handoff_targets`  do its targets accept what they are handed, and is
                              the graph acyclic? (activation; needs a resolver)
- `plan_handoffs`             given a finished run, what is filed and what is
                              refused? (filing)
- `plan_escalation`           given a failed run, what does escalation file?
- `join_state`                may a deferred (`after`) hand-off be filed yet?
- `plan_join`                 file it, once `join_state` says ready
- `requires_satisfied`        may a filed run start? (scheduling)

Comparisons are type-strict throughout (`True` is not `1`, `1` is not `"1"`),
because an engine in a language with strict equality must reach the same
decisions as this one.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Mapping, Optional

FILING_REQUEST_SCHEMA = "asop.filing-request/1"

CARDINALITIES = ("one", "many")

# Closed. A predicate an engine cannot evaluate identically to another engine is
# a fork of the contract, so the list grows by spec version, never by author.
REQUIRE_KINDS = ("run_done", "human_approved", "chain_gate")

# Predicates one engine used that are deliberately NOT ASOP data, each with
# where it lives instead. Named so the refusal says where to put it rather than
# reading as a typo.
NOT_REQUIRE_KINDS = {
    "slot_free": "concurrency is engine configuration, the same for every flow — not ASOP data (§3.7)",
    "artefact_current": "staleness is an engine duty, driven by recorded lineage and input hashes (§3.7)",
    "no_run_for_subject": "filing is idempotent on the natural key, so a second run for a subject is never filed (§3.7)",
    "all_runs_done": "a join is declared by the procedure that fans out, as a hand-off with `after` (§3.7); "
                     "the waiting side cannot know which runs will ever be filed",
}

JOIN_STATES = ("wait", "ready", "blocked")

# The refusal code is `handoff_refused:<rule>`, the same shape as
# `revision_policy:<rule>`: one code for the class, the rule after the colon.
HANDOFF_REFUSED = "handoff_refused"
HANDOFF_RULES: dict[str, str] = {
    "malformed": "the filing request is not a valid asop.filing-request/1 document",
    "undeclared": "the request names a hand-off the filing ASOP does not declare, or a target that does not match it",
    "outcome": "the run's outcome is not one the ASOP declares, or not one the requested hand-off fires on",
    "cardinality": "a `one` hand-off received more than one request",
    "subject": "a request names a subject the hand-off's `subject_from` does not yield, or names one on a `one` hand-off",
    "output": "the output a hand-off reads is missing, or does not have the declared shape",
    "inputs": "the successor's declared inputs are not exactly satisfied by scalar values",
    "version": "no ACTIVE version of the successor falls inside the declared version range",
    "conflict": "a run with this natural key was already filed with different inputs",
    # v3.6
    "order": "a fan-out's dependency field (`order_by`) names a subject the hand-off does not file, or the dependencies form a cycle",
    "approval": "a fan-out over an approved subset has no approval binding the output's current digest, or the approval names a subject the output does not contain",
}

# Rework (§3.9, v3.6): a declared, bounded edge that sends work back — to an
# earlier step of this procedure, or to the upstream procedure that owns a
# finding. Same shape of code as hand-offs: the class, then the rule.
REWORK_REFUSED = "rework_refused"
REWORK_RULES: dict[str, str] = {
    "undeclared": "the rework names an edge this ASOP does not declare, or an edge the trigger does not match",
    "version": "no version of the rework target is available (outside the declared range, or not pinned by the chain)",
    "findings": "the findings output a rework carries is missing, or not a scalar reference",
    "owner": "the engine named no upstream run for a rework to a procedure, so there is nothing to re-file",
}

#: Rounds a rework edge may declare. A rework edge is an autonomous loop with no
#: person in it; §5.4 already allows one repair before a person, and three
#: rounds is the most this contract lets a procedure spend before a person
#: looks. Exhausting the rounds escalates (§5.4) — it never loops on.
MAX_REWORK_ROUNDS = 3

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_RANGE = re.compile(r">=([1-9][0-9]*)(?:,<([1-9][0-9]*))?")
_FIELD = re.compile(r"[^.\s][^.]*")


def _is_scalar(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool))


def _same(a: Any, b: Any) -> bool:
    """Type-strict equality: `True` is not `1`, `1` is not `1.0`."""
    return type(a) is type(b) and a == b


class ChainContractError(ValueError):
    """A chaining declaration is malformed. Raised at authoring; the store
    reports it as `sop_refused`, like every other record error."""


# ---------------------------------------------------------------------------
# small parsers
# ---------------------------------------------------------------------------

def parse_version_range(spec: Any) -> tuple[int, Optional[int]]:
    """`">=N"` or `">=N,<M"` (N >= 1, M > N, ASCII digits, no spaces) → (N, M or
    None). Nothing else: a range language two engines parse differently is a
    contract fork."""
    m = _RANGE.fullmatch(spec) if isinstance(spec, str) else None
    if not m:
        raise ChainContractError(
            f"version range {spec!r} is not '>=N' or '>=N,<M' (no spaces, N >= 1). "
            f"Those are the only two forms."
        )
    lo, hi = int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
    if hi is not None and hi <= lo:
        raise ChainContractError(f"version range {spec!r} is empty: M must be greater than N.")
    return lo, hi


def version_in(target: dict, version: int) -> bool:
    """Does `version` satisfy a validated `to` reference?"""
    if "version" in target:
        return version == target["version"]
    lo, hi = parse_version_range(target["versions"])
    return version >= lo and (hi is None or version < hi)


def _key_part(s: str) -> str:
    return s.replace("%", "%25").replace("|", "%7C")


def natural_key(chain: str, asop_id: str, subject: str, attempt: int = 1) -> str:
    """`{chain}|{asop_id}|{subject}|a{attempt}`, each part with `%` and `|`
    percent-encoded so that no two distinct filings share a key. Unique per
    filed run, which is what makes filing idempotent."""
    return f"{_key_part(chain)}|{_key_part(asop_id)}|{_key_part(subject)}|a{attempt}"


def _ident(value: Any, what: str) -> str:
    """A name or reference: a non-empty string with no surrounding whitespace.
    Refused rather than stripped — stripping is how `"a"` and `" a"` collide."""
    if not isinstance(value, str) or not value.strip():
        raise ChainContractError(f"{what} must be a non-empty string, got {value!r}.")
    if value != value.strip():
        raise ChainContractError(f"{what} {value!r} has surrounding whitespace; names are matched exactly.")
    return value


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChainContractError(f"{what} must be a non-empty string, got {value!r}.")
    return value.strip()


def _list(raw: Any, what: str, hint: str) -> list:
    if isinstance(raw, (str, dict)) or not isinstance(raw, (list, tuple)) or not raw:
        raise ChainContractError(f"{what} must be a non-empty LIST. {hint}")
    return list(raw)


def _named_records(raw: Any, what: str) -> list[dict]:
    """`[{name, description?, kind?}]` — the shape `inputs` has, reused for
    `outputs` so an author learns one thing."""
    if raw is None:
        return []
    if isinstance(raw, (str, dict)) or not isinstance(raw, (list, tuple)):
        raise ChainContractError(f"'{what}' must be a LIST of {{name, description}} records.")
    out: list[dict] = []
    seen: set[str] = set()
    for i, rec in enumerate(raw):
        if not isinstance(rec, dict):
            raise ChainContractError(f"'{what}'[{i}] must be a record with a non-empty 'name'.")
        extra = set(rec) - {"name", "description", "kind"}
        if extra:
            raise ChainContractError(f"'{what}'[{i}]: unknown field(s) {', '.join(sorted(map(str, extra)))}.")
        name = _ident(rec.get("name"), f"'{what}'[{i}].name")
        if name in seen:
            raise ChainContractError(f"'{what}' declares '{name}' twice.")
        seen.add(name)
        clean = {"name": name}
        if rec.get("description") is not None:
            clean["description"] = _text(rec["description"], f"'{what}'[{i}].description")
        if rec.get("kind") is not None:
            clean["kind"] = rec["kind"]   # reserved, unvalidated — as on inputs (§11.5)
        out.append(clean)
    return out


def _target(raw: Any, what: str) -> dict:
    """`{asop_id, version}` (a pin) or `{asop_id, versions}` (a range resolved
    at filing, and recorded as a pin on the filed run)."""
    if not isinstance(raw, dict):
        raise ChainContractError(f"{what} must be {{asop_id, version}} or {{asop_id, versions}}.")
    if "asop" in raw and "asop_id" not in raw:
        raise ChainContractError(f"{what}: the key is 'asop_id', as in `uses` and every pin — not 'asop'.")
    extra = set(raw) - {"asop_id", "version", "versions"}
    if extra:
        raise ChainContractError(f"{what}: unknown field(s) {', '.join(sorted(map(str, extra)))}.")
    out = {"asop_id": _ident(raw.get("asop_id"), f"{what}.asop_id")}
    has_pin, has_range = "version" in raw, "versions" in raw
    if has_pin == has_range:
        raise ChainContractError(
            f"{what} must carry exactly one of 'version' (a pin) or 'versions' (a range like '>=3')."
        )
    if has_pin:
        v = raw["version"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise ChainContractError(f"{what}.version must be an integer >= 1, got {v!r}.")
        out["version"] = v
    else:
        parse_version_range(raw["versions"])
        out["versions"] = raw["versions"]
    return out


def _expr(value: Any, what: str, *, inputs: set[str], outputs: set[str], subject_ok: bool,
          outputs_ok: bool = True) -> Any:
    """An input mapping value: a reference (`$subject`, `$subject.<field>`,
    `$run.inputs.<name>`, `$run.outputs.<name>`) or a scalar literal. A literal
    string that must begin with `$` is written `$$…`. `<field>` is ONE key."""
    if not _is_scalar(value):
        raise ChainContractError(f"{what} must be a reference string or a scalar literal, got {value!r}.")
    if not isinstance(value, str) or not value.startswith("$") or value.startswith("$$"):
        return value
    if value == "$subject" or value.startswith("$subject."):
        if not subject_ok:
            raise ChainContractError(
                f"{what}: '{value}' — `$subject` exists only on a `many` hand-off, where each "
                f"filed run has a subject drawn from `subject_from`."
            )
        if value.startswith("$subject.") and not _FIELD.fullmatch(value[len("$subject."):]):
            raise ChainContractError(
                f"{what}: '{value}' must name one field of the subject item — `$subject.<field>`, "
                f"no dots, no path."
            )
        return value
    for prefix, declared, label in (("$run.inputs.", inputs, "inputs"), ("$run.outputs.", outputs, "outputs")):
        if value.startswith(prefix):
            name = value[len(prefix):]
            if label == "outputs" and not outputs_ok:
                raise ChainContractError(
                    f"{what}: '{value}' — an escalation happens before the run has finished, "
                    f"so it may read the run's inputs, never its outputs."
                )
            if name not in declared:
                raise ChainContractError(
                    f"{what}: '{value}' names {label[:-1]} '{name}', which this ASOP does not declare "
                    f"(declared: {', '.join(sorted(declared)) or 'none'})."
                )
            return value
    raise ChainContractError(
        f"{what}: '{value}' is not a reference. References are `$subject`, `$subject.<field>`, "
        f"`$run.inputs.<name>` and `$run.outputs.<name>`; write a literal '$' as '$$'."
    )


def _input_map(raw: Any, what: str, **kw) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ChainContractError(f"{what} must be a mapping of successor input name to value.")
    return {_ident(k, f"{what} key"): _expr(v, f"{what}.{k}", **kw) for k, v in raw.items()}


def _subject_from(raw: Any, what: str, outputs: set[str]) -> dict:
    if not isinstance(raw, dict):
        raise ChainContractError(f"{what} must be {{output, array, id_field, where?, where_not?}}.")
    extra = set(raw) - {"output", "array", "id_field", "where", "where_not",
                        "order_by", "concurrency", "approved_subset"}   # the last three: v3.6
    if extra:
        raise ChainContractError(f"{what}: unknown field(s) {', '.join(sorted(map(str, extra)))}.")
    out = {k: _ident(raw.get(k), f"{what}.{k}") for k in ("output", "array", "id_field")}
    if out["output"] not in outputs:
        raise ChainContractError(
            f"{what}.output '{out['output']}' is not a declared output of this ASOP "
            f"(declared: {', '.join(sorted(outputs)) or 'none'}). A fan-out reads only what the "
            f"procedure promises to write."
        )
    if not all(_FIELD.fullmatch(p) for p in out["array"].split(".")):
        raise ChainContractError(f"{what}.array '{out['array']}' must be dot-separated keys, none empty.")
    where = raw.get("where")
    if where is not None:
        if not isinstance(where, dict) or not where:
            raise ChainContractError(f"{what}.where must be a non-empty mapping of field to allowed values.")
        clean = {}
        for k, v in where.items():
            vals = v if isinstance(v, list) else [v]
            if not vals or not all(_is_scalar(x) for x in vals):
                raise ChainContractError(f"{what}.where.{k} must be a scalar or a non-empty list of scalars.")
            clean[_ident(k, f"{what}.where key")] = vals
        out["where"] = clean
    where_not = raw.get("where_not")
    if where_not is not None:
        if not isinstance(where_not, dict) or not where_not:
            raise ChainContractError(f"{what}.where_not must be a non-empty mapping of field to excluded value.")
        clean = {}
        for k, v in where_not.items():
            if not _is_scalar(v):
                raise ChainContractError(f"{what}.where_not.{k} must be a scalar.")
            clean[_ident(k, f"{what}.where_not key")] = v
        out["where_not"] = clean
    if raw.get("order_by") is not None:
        out["order_by"] = _ident(raw["order_by"], f"{what}.order_by")
        if out["order_by"] == out["id_field"]:
            raise ChainContractError(f"{what}.order_by names the id field; it names the field that lists "
                                     f"each item's dependencies.")
    if raw.get("concurrency") is not None:
        c = raw["concurrency"]
        if isinstance(c, bool) or not isinstance(c, int) or c < 1:
            raise ChainContractError(f"{what}.concurrency must be a positive integer, got {c!r}.")
        out["concurrency"] = c
    if raw.get("approved_subset") is not None:
        if raw["approved_subset"] is not True:
            raise ChainContractError(
                f"{what}.approved_subset must be true or absent. When true, only the subjects a person "
                f"approved, in an approval that binds the output's current digest, are filed."
            )
        out["approved_subset"] = True
    return out


# ---------------------------------------------------------------------------
# authoring: one record
# ---------------------------------------------------------------------------

CHAIN_FIELDS = ("outcomes", "outputs", "handoffs", "on_escalate", "requires",
                "entry_gates", "rework")   # v3.6


def validate_chain_fields(payload: dict, *, asop_id: Optional[str] = None,
                          input_names: Iterable[str] = (), steps: Optional[int] = None) -> dict:
    """Normalise the five optional chaining fields of an ASOP body. Returns only
    the fields present. Raises `ChainContractError`; never repairs.

    `asop_id` is the record's own id when known (a store knows it; a bare body
    may not) — it is used to refuse direct self-reference. Longer cycles need
    the other records: `validate_handoff_targets`. `steps` is the record's
    step count when known, so a rework edge to a step that does not exist is
    refused here (v3.6)."""
    out: dict = {}
    inputs = set(input_names)

    outcomes: list[str] = []
    if payload.get("outcomes") is not None:
        raw = _list(payload["outcomes"], "'outcomes'", "Omit it if there are none.")
        for i, o in enumerate(raw):
            name = _ident(o, f"'outcomes'[{i}]")
            if name in outcomes:
                raise ChainContractError(f"'outcomes' declares '{name}' twice.")
            outcomes.append(name)
        out["outcomes"] = outcomes

    outputs_list = _named_records(payload.get("outputs"), "outputs")
    if payload.get("outputs") is not None:
        out["outputs"] = outputs_list
    outputs = {o["name"] for o in outputs_list}

    if payload.get("handoffs") is not None:
        raw = _list(payload["handoffs"], "'handoffs'", "Omit it for a procedure that hands off nothing.")
        seen: set[str] = set()
        targets: dict[str, str] = {}
        handoffs = []
        for i, h in enumerate(raw):
            what = f"'handoffs'[{i}]"
            if not isinstance(h, dict):
                raise ChainContractError(f"{what} must be a mapping.")
            extra = set(h) - {"id", "to", "on_outcomes", "cardinality", "subject_from", "inputs", "after",
                              "preflight"}
            if extra:
                raise ChainContractError(f"{what}: unknown field(s) {', '.join(sorted(map(str, extra)))}.")
            hid = _ident(h.get("id"), f"{what}.id")
            if not _ID.fullmatch(hid):
                raise ChainContractError(f"{what}.id '{hid}' must be letters, digits, '.', '_' or '-'.")
            if hid in seen:
                raise ChainContractError(f"'handoffs' declares id '{hid}' twice.")
            seen.add(hid)
            what = f"hand-off '{hid}'"
            clean: dict = {"id": hid, "to": _target(h.get("to"), f"{what}.to")}
            tid = clean["to"]["asop_id"]
            if asop_id is not None and tid == asop_id:
                raise ChainContractError(
                    f"{what} hands off to its own ASOP. A loop is a nested step or a repair (§5.4); "
                    f"a procedure that files itself has no bound."
                )
            if tid in targets:
                raise ChainContractError(
                    f"{what} targets '{tid}', which hand-off '{targets[tid]}' already targets. One hand-off "
                    f"per target: two would share natural keys, and which inputs a run gets would depend "
                    f"on order. Route subjects with `where` on one hand-off instead."
                )
            targets[tid] = hid
            if h.get("on_outcomes") is not None:
                oo = _list(h["on_outcomes"], f"{what}.on_outcomes", "Omit it to fire on every completion.")
                if not outcomes:
                    raise ChainContractError(
                        f"{what}.on_outcomes names outcomes, but the ASOP declares no 'outcomes'. "
                        f"Declare them, so a misspelt outcome is refused here instead of never firing."
                    )
                cleaned_oo = []
                for o in oo:
                    o = _ident(o, f"{what}.on_outcomes entry")
                    if o not in outcomes:
                        raise ChainContractError(
                            f"{what}.on_outcomes names '{o}', which is not a declared outcome "
                            f"(declared: {', '.join(outcomes)})."
                        )
                    if o in cleaned_oo:
                        raise ChainContractError(f"{what}.on_outcomes names '{o}' twice.")
                    cleaned_oo.append(o)
                clean["on_outcomes"] = cleaned_oo
            card = h.get("cardinality", "one")
            if not isinstance(card, str) or card not in CARDINALITIES:
                raise ChainContractError(f"{what}.cardinality must be 'one' or 'many', got {card!r}.")
            clean["cardinality"] = card
            if card == "many":
                if h.get("subject_from") is None:
                    raise ChainContractError(
                        f"{what} is `many` but has no 'subject_from'. A fan-out names the output that "
                        f"lists its items, so the engine can expand it without a model enumerating them."
                    )
                clean["subject_from"] = _subject_from(h["subject_from"], f"{what}.subject_from", outputs)
            elif h.get("subject_from") is not None:
                raise ChainContractError(f"{what} is `one`, so 'subject_from' has nothing to fan out over. Remove it.")
            if h.get("after") is not None:
                after = _list(h["after"], f"{what}.after", "Omit it for a hand-off filed at completion.")
                if card != "one":
                    raise ChainContractError(
                        f"{what} has 'after', so it is a join, and a join files ONE run once the runs it waits "
                        f"on are finished. Make it `one`; fan out again from the joined procedure."
                    )
                cleaned_after: list[str] = []
                for a in after:
                    a = _ident(a, f"{what}.after entry")
                    if a == hid:
                        raise ChainContractError(f"{what}.after names itself.")
                    if a not in seen - {hid}:
                        raise ChainContractError(
                            f"{what}.after names '{a}', which is not a hand-off declared EARLIER in this "
                            f"record. A join waits on runs its own procedure filed, and listing them in "
                            f"order keeps joins acyclic."
                        )
                    if a in cleaned_after:
                        raise ChainContractError(f"{what}.after names '{a}' twice.")
                    cleaned_after.append(a)
                clean["after"] = cleaned_after
            clean["inputs"] = _input_map(h.get("inputs"), f"{what}.inputs", inputs=inputs, outputs=outputs,
                                         subject_ok=(card == "many"))
            if h.get("preflight") is not None:
                if not isinstance(h["preflight"], bool):
                    raise ChainContractError(f"{what}.preflight must be a boolean, got {h['preflight']!r}.")
                if h["preflight"]:
                    clean["preflight"] = True
            handoffs.append(clean)
        out["handoffs"] = handoffs

    if payload.get("on_escalate") is not None:
        raw = payload["on_escalate"]
        if not isinstance(raw, dict):
            raise ChainContractError("'on_escalate' must be {to, inputs?}.")
        extra = set(raw) - {"to", "inputs"}
        if extra:
            raise ChainContractError(f"'on_escalate': unknown field(s) {', '.join(sorted(map(str, extra)))}.")
        esc = {"to": _target(raw.get("to"), "'on_escalate'.to")}
        if asop_id is not None and esc["to"]["asop_id"] == asop_id:
            raise ChainContractError("'on_escalate' targets its own ASOP. An escalation must reach a person (§5.4).")
        esc["inputs"] = _input_map(raw.get("inputs"), "'on_escalate'.inputs", inputs=inputs, outputs=outputs,
                                   subject_ok=False, outputs_ok=False)
        out["on_escalate"] = esc

    if payload.get("requires") is not None:
        raw = _list(payload["requires"], "'requires'", "Omit it for a procedure that may start when filed.")
        reqs = []
        for i, r in enumerate(raw):
            what = f"'requires'[{i}]"
            if not isinstance(r, dict):
                raise ChainContractError(f"{what} must be a mapping with a 'kind'.")
            kind = r.get("kind")
            if not isinstance(kind, str):
                raise ChainContractError(f"{what}: 'kind' must be one of {', '.join(REQUIRE_KINDS)}, got {kind!r}.")
            if kind in NOT_REQUIRE_KINDS:
                raise ChainContractError(f"{what}: '{kind}' is not a requires kind — {NOT_REQUIRE_KINDS[kind]}.")
            if kind not in REQUIRE_KINDS:
                raise ChainContractError(
                    f"{what}: kind {kind!r} is not one of {', '.join(REQUIRE_KINDS)}. The list is closed: "
                    f"a predicate two engines evaluate differently is a fork of the contract."
                )
            if kind == "run_done":
                if set(r) != {"kind", "asop_id"}:
                    raise ChainContractError(f"{what}: '{kind}' takes exactly {{kind, asop_id}}.")
                ref = _ident(r["asop_id"], f"{what}.asop_id")
                if asop_id is not None and ref == asop_id:
                    raise ChainContractError(f"{what}: a procedure cannot wait on its own runs.")
                reqs.append({"kind": kind, "asop_id": ref})
            elif kind == "human_approved":
                if not {"kind", "artefact"} <= set(r) or set(r) - {"kind", "artefact", "binds_digest"}:
                    raise ChainContractError(
                        f"{what}: 'human_approved' takes {{kind, artefact, binds_digest?}}."
                    )
                req = {"kind": kind, "artefact": _ident(r["artefact"], f"{what}.artefact")}
                if r.get("binds_digest") is not None:
                    if not isinstance(r["binds_digest"], bool):
                        raise ChainContractError(f"{what}.binds_digest must be a boolean.")
                    if r["binds_digest"]:
                        req["binds_digest"] = True
                reqs.append(req)
            else:  # chain_gate (v3.6)
                if set(r) != {"kind", "id", "gate"}:
                    raise ChainContractError(f"{what}: 'chain_gate' takes exactly {{kind, id, gate}}.")
                gid = _ident(r["id"], f"{what}.id")
                if any(x.get("kind") == "chain_gate" and x["id"] == gid for x in reqs):
                    raise ChainContractError(f"'requires' declares chain gate '{gid}' twice.")
                reqs.append({"kind": kind, "id": gid,
                             "gate": _deterministic_gate(r["gate"], f"{what}.gate")})
        out["requires"] = reqs

    if payload.get("entry_gates") is not None:
        out["entry_gates"] = _entry_gates(payload["entry_gates"], inputs)

    if payload.get("rework") is not None:
        out["rework"] = _rework_edges(payload["rework"], asop_id=asop_id, outcomes=outcomes,
                                      outputs=outputs, handoffs=out.get("handoffs", []), steps=steps)

    return out


def _deterministic_gate(raw: Any, what: str) -> dict:
    """A gate the ENGINE runs over documents — an entry gate, a chain gate.
    Normalised by the shared gate schema, and deterministic only: a judge or a
    person reached through an entry gate would be a step nobody declared."""
    from asop.errors import Refusal
    from asop.gates import validate_gate
    try:
        gate = validate_gate(raw)
    except Refusal as e:
        raise ChainContractError(f"{what} is not a valid gate — {e.message}") from e
    if gate["kind"] != "deterministic":
        raise ChainContractError(
            f"{what} is kind '{gate['kind']}'. An entry or chain gate is run by the engine over "
            f"documents, so it is deterministic; a judgment or a sign-off is a step."
        )
    return gate


def _entry_gates(raw: Any, inputs: set[str]) -> list[dict]:
    """`entry_gates: [{id, over: [declared input], gate}]` (§3.8, v3.6) — the
    consumer's precondition over what it is handed, or over a source it is
    given at chain start (a readiness gate). Evaluated before the run may
    start; never a step, never charged to this run's executor."""
    items = _list(raw, "'entry_gates'", "Omit it for a procedure that checks nothing before it starts.")
    out: list[dict] = []
    seen: set[str] = set()
    for i, g in enumerate(items):
        what = f"'entry_gates'[{i}]"
        if not isinstance(g, dict):
            raise ChainContractError(f"{what} must be a mapping {{id, over, gate}}.")
        extra = set(g) - {"id", "over", "gate"}
        if extra:
            raise ChainContractError(f"{what}: unknown field(s) {', '.join(sorted(map(str, extra)))}.")
        gid = _ident(g.get("id"), f"{what}.id")
        if not _ID.fullmatch(gid):
            raise ChainContractError(f"{what}.id '{gid}' must be letters, digits, '.', '_' or '-'.")
        if gid in seen:
            raise ChainContractError(f"'entry_gates' declares id '{gid}' twice.")
        seen.add(gid)
        over: list[str] = []
        for name in _list(g.get("over"), f"entry gate '{gid}'.over",
                          "Name the declared inputs this gate reads; a gate over nothing checks nothing."):
            name = _ident(name, f"entry gate '{gid}'.over entry")
            if name not in inputs:
                raise ChainContractError(
                    f"entry gate '{gid}' reads input '{name}', which this ASOP does not declare "
                    f"(declared: {', '.join(sorted(inputs)) or 'none'}). An entry gate checks what the "
                    f"run is handed, so it may read only declared inputs."
                )
            if name in over:
                raise ChainContractError(f"entry gate '{gid}'.over names '{name}' twice.")
            over.append(name)
        out.append({"id": gid, "over": over, "gate": _deterministic_gate(g.get("gate"), f"entry gate '{gid}'.gate")})
    return out


def _rework_edges(raw: Any, *, asop_id: Optional[str], outcomes: list[str], outputs: set[str],
                  handoffs: list[dict], steps: Optional[int]) -> list[dict]:
    """`rework: [...]` (§3.9, v3.6). Two shapes:

    - to an EARLIER STEP of this procedure: `{id, to: {step: n}, on_step_failure: k,
      max_rounds, consumes_attempt, tier_bump?}`, k > n — when step k's gate
      fails, re-execute steps n..k instead of repairing k alone;
    - to an UPSTREAM PROCEDURE: `{id, to: {asop_id, version|versions}, on_outcomes,
      findings?, max_rounds, consumes_attempt, tier_bump?}` — when this run
      finishes with one of those outcomes, re-file the upstream run that owns the
      finding as its next attempt, carrying `findings` as its prior findings.

    `consumes_attempt` has no default: whether a round spends §5.4's budget is
    a decision, and a library-level guess would make it for somebody else."""
    items = _list(raw, "'rework'", "Omit it for a procedure that sends nothing back.")
    out: list[dict] = []
    seen: set[str] = set()
    triggers: dict[Any, str] = {}
    fired_forward = {o for h in handoffs for o in h.get("on_outcomes", outcomes)}
    if any("on_outcomes" not in h for h in handoffs):
        fired_forward = set(outcomes)
    for i, e in enumerate(items):
        what = f"'rework'[{i}]"
        if not isinstance(e, dict):
            raise ChainContractError(f"{what} must be a mapping.")
        extra = set(e) - {"id", "to", "on_outcomes", "on_step_failure", "findings", "max_rounds",
                          "consumes_attempt", "tier_bump"}
        if extra:
            raise ChainContractError(f"{what}: unknown field(s) {', '.join(sorted(map(str, extra)))}.")
        rid = _ident(e.get("id"), f"{what}.id")
        if not _ID.fullmatch(rid):
            raise ChainContractError(f"{what}.id '{rid}' must be letters, digits, '.', '_' or '-'.")
        if rid in seen:
            raise ChainContractError(f"'rework' declares id '{rid}' twice.")
        seen.add(rid)
        what = f"rework '{rid}'"
        to = e.get("to")
        clean: dict = {"id": rid}
        if isinstance(to, dict) and set(to) == {"step"}:
            n = to["step"]
            if isinstance(n, bool) or not isinstance(n, int) or n < 1 or (steps is not None and n > steps):
                raise ChainContractError(f"{what}.to.step must be a step of this ASOP, got {n!r}.")
            k = e.get("on_step_failure")
            if isinstance(k, bool) or not isinstance(k, int) or (steps is not None and k > steps):
                raise ChainContractError(
                    f"{what} reworks a step, so 'on_step_failure' names the step of this ASOP whose gate "
                    f"failure triggers it, got {k!r}."
                )
            if k <= n:
                raise ChainContractError(
                    f"{what}: step {k} failing re-executes step {n}, which is not earlier. A failing "
                    f"step re-running itself is a repair (§5.4), not rework."
                )
            for bad in ("on_outcomes", "findings"):
                if e.get(bad) is not None:
                    raise ChainContractError(
                        f"{what} reworks a step, so '{bad}' has nothing to read: the trigger is a "
                        f"gate failure inside the run, and the findings are that gate's attestation."
                    )
            clean["to"] = {"step": n}
            clean["on_step_failure"] = k
            key = ("step", k)
        else:
            clean["to"] = _target(to, f"{what}.to")
            if asop_id is not None and clean["to"]["asop_id"] == asop_id:
                raise ChainContractError(
                    f"{what} reworks its own ASOP. Rework inside a procedure targets a step "
                    f"(`to: {{step: n}}`)."
                )
            if e.get("on_step_failure") is not None:
                raise ChainContractError(
                    f"{what} reworks a procedure, so its trigger is an outcome of this run "
                    f"('on_outcomes'), not a step failure."
                )
            oo = _list(e.get("on_outcomes"), f"{what}.on_outcomes",
                       "Name the outcomes of this run that send the work back.")
            if not outcomes:
                raise ChainContractError(f"{what}.on_outcomes names outcomes, but the ASOP declares no 'outcomes'.")
            cleaned: list[str] = []
            for o in oo:
                o = _ident(o, f"{what}.on_outcomes entry")
                if o not in outcomes:
                    raise ChainContractError(f"{what}.on_outcomes names '{o}', which is not a declared outcome.")
                if o in fired_forward:
                    raise ChainContractError(
                        f"{what} fires on '{o}', and so does a hand-off. A run that sends its work back "
                        f"does not also send it forward: give the rework its own outcome."
                    )
                if o in cleaned:
                    raise ChainContractError(f"{what}.on_outcomes names '{o}' twice.")
                cleaned.append(o)
            clean["on_outcomes"] = cleaned
            if e.get("findings") is not None:
                f = _ident(e["findings"], f"{what}.findings")
                if f not in outputs:
                    raise ChainContractError(
                        f"{what}.findings '{f}' is not a declared output. The findings a rework carries "
                        f"upstream are something this procedure promises to write."
                    )
                clean["findings"] = f
            key = None
            for o in cleaned:
                if ("outcome", o) in triggers:
                    raise ChainContractError(
                        f"{what} and rework '{triggers[('outcome', o)]}' both fire on '{o}'. One trigger, "
                        f"one edge: which owner is re-filed must not depend on order."
                    )
                triggers[("outcome", o)] = rid
        if key is not None:
            if key in triggers:
                raise ChainContractError(
                    f"{what} and rework '{triggers[key]}' both fire when step {key[1]} fails. One trigger, one edge."
                )
            triggers[key] = rid
        rounds = e.get("max_rounds")
        if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= MAX_REWORK_ROUNDS:
            raise ChainContractError(
                f"{what}.max_rounds must be an integer 1..{MAX_REWORK_ROUNDS}, got {rounds!r}. A rework edge is "
                f"a loop with no person in it, so it is bounded; exhausting it escalates (§5.4)."
            )
        clean["max_rounds"] = rounds
        if not isinstance(e.get("consumes_attempt"), bool):
            raise ChainContractError(
                f"{what}.consumes_attempt must be true or false. Whether a round spends §5.4's budget is the "
                f"author's decision; there is deliberately no default."
            )
        clean["consumes_attempt"] = e["consumes_attempt"]
        if e.get("tier_bump") is not None:
            if not isinstance(e["tier_bump"], bool):
                raise ChainContractError(f"{what}.tier_bump must be a boolean.")
            if e["tier_bump"]:
                clean["tier_bump"] = True
        out.append(clean)
    return out


# ---------------------------------------------------------------------------
# authoring: across records
# ---------------------------------------------------------------------------

Resolver = Callable[[dict], Optional[tuple[int, dict]]]
"""Given a validated `to` reference, return `(version, validated body)` of the
version that would be filed today — the ACTIVE version inside the pin or range —
or None if there is none. The store owns resolution; this module never guesses."""


def validate_handoff_targets(body: dict, resolve: Resolver, *, asop_id: str) -> None:
    """Check a validated body against the records it names. Run at activation,
    when the targets are knowable; `plan_handoffs` repeats the input check at
    filing, because a target may have moved since. `asop_id` is the record's
    own id and is required: without it a cycle back to a record that is not yet
    active cannot be seen.

    - every hand-off target resolves, and is handed exactly the inputs it
      declares — no more, no fewer. A run cannot fill a gap (it names subjects,
      never inputs), so a gap here is a filing that will always be refused;
    - an escalation target resolves, declares every input it is handed, and has
      a step a human role does — §5.4's second failure "escalates to a human".
      Its unmapped inputs are filled from the engine's escalation context;
    - the HAND-OFF graph reachable from this record has no cycle. With
      run-generated subjects, a cycle through a `many` hand-off has no bound.
      Escalation edges are not part of it: a person sits in every escalation,
      which is the bound, and "the decision procedure re-files the step that
      escalated" is a normal pattern;
    - this record does not `require` an ASOP it can itself (transitively) file.
      It would wait for a run that can only exist after it finishes. This is
      checked from THIS record's activation only; a later version of a
      descendant can still add such a path, which is why a run whose
      `requires` never hold is bounded by the engine's liveness clock (§3.7)."""
    _ident(asop_id, "validate_handoff_targets asop_id")
    for h in body.get("handoffs", []):
        target = _resolve_or_raise(h["to"], f"hand-off '{h['id']}'", resolve)
        declared = {i["name"] for i in target.get("inputs", [])}
        undeclared = sorted(set(h["inputs"]) - declared)
        missing = sorted(declared - set(h["inputs"]))
        if undeclared:
            raise ChainContractError(
                f"hand-off '{h['id']}' hands '{h['to']['asop_id']}' input(s) {', '.join(undeclared)}, which it "
                f"does not declare (declared: {', '.join(sorted(declared)) or 'none'})."
            )
        if missing:
            raise ChainContractError(
                f"hand-off '{h['id']}' leaves '{h['to']['asop_id']}' input(s) {', '.join(missing)} unmapped. A "
                f"run cannot supply them — a filing request names subjects, never inputs — so every filing "
                f"would be refused. Map them."
            )
    esc = body.get("on_escalate")
    if esc:
        target = _resolve_or_raise(esc["to"], "'on_escalate'", resolve)
        declared = {i["name"] for i in target.get("inputs", [])}
        undeclared = sorted(set(esc["inputs"]) - declared)
        if undeclared:
            raise ChainContractError(
                f"'on_escalate' hands '{esc['to']['asop_id']}' input(s) {', '.join(undeclared)}, which it does "
                f"not declare (declared: {', '.join(sorted(declared)) or 'none'})."
            )
        roles = target.get("roles", {})
        if not any("uses" not in st and roles.get(st.get("role"), {}).get("kind") == "human"
                   for st in target.get("steps", [])):
            raise ChainContractError(
                f"'on_escalate' targets '{esc['to']['asop_id']}', which has no step a human role does. "
                f"An escalation reaches a person (§5.4)."
            )
    descendants = _walk_handoffs(body, resolve, asop_id)
    for r in body.get("requires", []):
        if r["kind"] == "run_done" and r["asop_id"] in descendants:
            raise ChainContractError(
                f"'requires' waits on '{r['asop_id']}', which this ASOP itself (transitively) hands off to. "
                f"Those runs can only be filed after this one finishes, so it would wait forever."
            )
    # v3.6: a preflight runs the consumer's entry gates, so the consumer must have some.
    for h in body.get("handoffs", []):
        if h.get("preflight") and not _resolve_or_raise(h["to"], f"hand-off '{h['id']}'", resolve).get("entry_gates"):
            raise ChainContractError(
                f"hand-off '{h['id']}' asks for a preflight, but '{h['to']['asop_id']}' declares no entry gates. "
                f"A preflight runs the consumer's lint; with none, it would check nothing and look like it did."
            )
    # v3.6: a rework edge to a procedure goes UPSTREAM — to one that leads here.
    # Otherwise it is a hand-off wearing a loop's name, and escapes the cycle ban.
    for e in body.get("rework", []):
        if "asop_id" not in e["to"]:
            continue
        found = resolve(e["to"])
        if found is None:
            raise ChainContractError(
                f"rework '{e['id']}': no ACTIVE version of '{e['to']['asop_id']}' matches "
                f"{e['to'].get('version') or e['to'].get('versions')}."
            )
        try:
            upstream_reaches = _walk_handoffs(found[1], resolve, e["to"]["asop_id"])
        except ChainContractError:
            upstream_reaches = set()
        if asop_id not in upstream_reaches:
            raise ChainContractError(
                f"rework '{e['id']}' targets '{e['to']['asop_id']}', which does not (transitively) hand off "
                f"to '{asop_id}'. Rework sends work back to the procedure that produced it; a filing anywhere "
                f"else is a hand-off, and must be declared as one."
            )


def _handoff_edges(body: dict) -> list[dict]:
    return [h["to"] for h in body.get("handoffs", [])]


def _walk_handoffs(body: dict, resolve: Resolver, start: str) -> set[str]:
    """Every asop_id reachable from `body` through hand-offs (not escalations).
    Raises on a cycle."""
    path: list[str] = [start]
    seen: set[str] = set()

    def walk(b: dict) -> None:
        for to in _handoff_edges(b):
            node = to["asop_id"]
            if node in path:
                cycle = " → ".join(path[path.index(node):] + [node])
                raise ChainContractError(
                    f"hand-offs form a cycle: {cycle}. A chain must end; a loop is a nested step, a "
                    f"repair (§5.4) or an escalation, and a cycle through a fan-out has no bound."
                )
            if node in seen:
                continue
            seen.add(node)
            found = resolve(to)
            if found is None:
                continue  # unresolvable downstream is that record's own activation problem
            path.append(node)
            walk(found[1])
            path.pop()

    walk(body)
    return seen


def _resolve_or_raise(to: dict, what: str, resolve: Resolver) -> dict:
    found = resolve(to)
    if found is None:
        raise ChainContractError(
            f"{what}: no ACTIVE version of '{to['asop_id']}' matches {to.get('version') or to.get('versions')}."
        )
    return found[1]


# ---------------------------------------------------------------------------
# filing
# ---------------------------------------------------------------------------

def validate_filing_request(doc: Any) -> dict:
    """`asop.filing-request/1`: `{schema, handoff, to, subject?, reason?}`.
    A request names WHICH subjects, never WHAT inputs — there is no `inputs`.
    Raises `ChainContractError` (filed as `handoff_refused:malformed`)."""
    if not isinstance(doc, dict):
        raise ChainContractError("a filing request must be a mapping.")
    if doc.get("schema") != FILING_REQUEST_SCHEMA:
        raise ChainContractError(f"a filing request declares schema '{FILING_REQUEST_SCHEMA}', got {doc.get('schema')!r}.")
    if "inputs" in doc:
        raise ChainContractError(
            "a filing request carries no 'inputs'. A run chooses which declared hand-offs and subjects "
            "are filed; what each successor is handed is declared on the ASOP (§3.7)."
        )
    extra = set(doc) - {"schema", "handoff", "to", "subject", "reason"}
    if extra:
        raise ChainContractError(f"filing request: unknown field(s) {', '.join(sorted(map(str, extra)))}.")
    out = {"schema": FILING_REQUEST_SCHEMA,
           "handoff": _ident(doc.get("handoff"), "filing request 'handoff'"),
           "to": _ident(doc.get("to"), "filing request 'to'")}
    if "subject" in doc:
        s = doc["subject"]
        if isinstance(s, int) and not isinstance(s, bool):
            s = str(s)
        out["subject"] = _ident(s, "filing request 'subject'")
    if doc.get("reason") is not None:
        out["reason"] = _text(doc["reason"], "filing request 'reason'")
    return out


def _dig(data: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            raise KeyError(f"'{path}' is not in the output")
        data = data[part]
    return data


def expand_subjects(handoff: dict, outputs: dict) -> list[tuple[str, dict]]:
    """The `(subject id, item)` pairs a `many` hand-off yields, in output order.

    The output is a mapping; `array` is a dot-separated path of keys to a list
    of mappings. An item is kept when every `where` field is present with one
    of the listed values, and no `where_not` field equals its value — both
    type-strict. Its `id_field` must be a string or an integer (not a bool),
    and is the subject as a string. Two kept items with the same subject are
    an error, not a de-duplication: which one was meant is not ours to guess.
    Raises `KeyError` / `ValueError` when the output lacks this shape."""
    sf = handoff["subject_from"]
    if sf["output"] not in outputs:
        raise KeyError(f"output '{sf['output']}' was not written")
    items = _dig(outputs[sf["output"]], sf["array"])
    if not isinstance(items, list):
        raise ValueError(f"'{sf['array']}' in output '{sf['output']}' is not a list")
    pairs: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"an item of '{sf['array']}' is not a mapping")
        if not all(k in item and any(_same(item[k], v) for v in vals) for k, vals in sf.get("where", {}).items()):
            continue
        if any(k in item and _same(item[k], v) for k, v in sf.get("where_not", {}).items()):
            continue
        sid = item.get(sf["id_field"])
        if isinstance(sid, bool) or not isinstance(sid, (str, int)) or (isinstance(sid, str) and not sid.strip()):
            raise ValueError(f"an item of '{sf['array']}' has no usable '{sf['id_field']}' (a string or an integer)")
        sid = str(sid)
        if sid in seen:
            raise ValueError(f"two items of '{sf['array']}' share the subject '{sid}'")
        seen.add(sid)
        pairs.append((sid, item))
    return pairs


def _resolve_value(value: Any, *, subject: Optional[str], item: Optional[dict],
                   run_inputs: Mapping, outputs: Mapping) -> Any:
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    if value.startswith("$$"):
        return value[1:]
    if value == "$subject":
        return subject
    if value.startswith("$subject."):
        field = value[len("$subject."):]
        if item is None or field not in item:
            raise KeyError(f"{value} has no value")
        got = item[field]
    elif value.startswith("$run.inputs."):
        name = value[len("$run.inputs."):]
        if name not in run_inputs:
            raise KeyError(f"{value} has no value")
        got = run_inputs[name]
    else:
        name = value[len("$run.outputs."):]
        if name not in outputs:
            raise KeyError(f"{value} has no value")
        got = outputs[name]
    if not _is_scalar(got):
        # A structure handed through would bypass the scalar rule on literals,
        # and leave staleness hashing undefined. Hand a name or a path instead.
        raise KeyError(f"{value} is a {type(got).__name__}, not a scalar")
    return got


def _refuse(rule: str, detail: str, *, toward: Iterable[str] = (), **ctx) -> dict:
    """`toward` lists the procedures this refusal leaves unfiled; a join over
    that hand-off is then blocked (`join_state`). A refused REQUEST blocks
    nothing (`toward` empty): the declared hand-off still files without it."""
    return {"action": "refuse", "code": f"{HANDOFF_REFUSED}:{rule}", "detail": detail,
            "toward": sorted(set(toward)), **ctx}


def _file_or_dup(decisions: list, filed: dict, *, handoff: Optional[str], to: dict, version: int,
                 subject: str, inputs: dict, chain: str, attempt: int) -> None:
    key = natural_key(chain, to["asop_id"], subject, attempt)
    ctx = {"handoff": handoff} if handoff else {}
    if key in filed:
        prev = filed[key]
        if prev is not None and (set(prev) != set(inputs) or not all(_same(prev[k], inputs[k]) for k in inputs)):
            decisions.append(_refuse("conflict", f"{key} was already filed with different inputs",
                                     natural_key=key, toward=[to["asop_id"]], **ctx))
        else:
            decisions.append({"action": "duplicate", "natural_key": key, **ctx})
        return
    filed[key] = inputs
    decisions.append({"action": "file", **ctx, "to": {"asop_id": to["asop_id"], "version": version},
                      "subject": subject, "inputs": inputs, "natural_key": key})


def _filed_map(existing: Any) -> dict:
    """`existing` is natural key -> the inputs it was filed with. A bare list of
    keys is accepted for engines that do not keep inputs; their duplicates are
    then never checked for conflict."""
    if isinstance(existing, Mapping):
        return dict(existing)
    return {k: None for k in existing or ()}


def plan_handoffs(body: dict, *, outcome: Optional[str], outputs: Mapping, run_inputs: Mapping,
                  requests: Any, resolve: Resolver, chain: str,
                  existing: Any = (), attempt: int = 1,
                  approvals: Optional[Mapping] = None, output_digests: Optional[Mapping] = None) -> list[dict]:
    """What a finished run files. `body` is the filing ASOP's validated body.

    Returns decisions in a normative order — the run's outcome first, then each
    request in the order written, then each declared hand-off in declaration
    order, its subjects in output (or request) order:

    - `{"action": "file", "handoff", "to": {asop_id, version}, "subject", "inputs", "natural_key"}`
    - `{"action": "duplicate", "handoff", "natural_key"}` — already filed with
      the same inputs; nothing happens. Not a refusal: a replayed ledger, or
      several runs handing off to one chain run, is the normal case.
    - `{"action": "wait", "handoff", "after"}` — a join that fired; it is filed
      by `plan_join` once `join_state` says `ready`.
    - `{"action": "refuse", "code": "handoff_refused:<rule>", "detail", ...}` —
      recorded, never silently dropped, never repaired.

    Semantics (ASOP.md §3.7):
    - when the ASOP declares `outcomes`, a run whose outcome is not one of them
      files nothing and is refused (`outcome`): a closed set is closed;
    - a hand-off FIRES when the outcome is in its `on_outcomes` (or always,
      when it has none);
    - a fired `one` hand-off files one run whose subject is the chain — at
      completion, or, when it has `after`, once those runs are finished;
    - a fired `many` hand-off files one run per subject `subject_from` yields,
      or — when the run wrote ACCEPTED requests for it — exactly those subjects;
    - a REFUSED request is as if it had not been written. It never narrows,
      cancels or alters what the ASOP declared;
    - `attempt` is the attempt being filed; the engine passes a higher one when
      it re-files downstream of a stale input. `existing` holds what was filed
      before (key -> inputs); the same key with different inputs is a
      `conflict`, refused.

    v3.6, all opt-in by declaration on `subject_from`:
    - `approved_subset`: only subjects in a person's approval are yielded.
      `approvals` maps an output name to `{digest, subset?}`, and
      `output_digests` maps it to the digest of what was written; an approval
      whose digest is not the current one approves something else, and the
      hand-off is refused (`approval`). `subset` absent approves every subject;
    - `order_by`: each `file` decision carries `depends_on`, the natural keys
      of the subjects its item lists. A dependency the hand-off does not file,
      or a cycle, is refused (`order`); `fanout_startable` decides which may
      start, within `concurrency`.
    """
    decisions: list[dict] = []
    filed = _filed_map(existing)
    declared_outcomes = body.get("outcomes")
    if declared_outcomes and outcome not in declared_outcomes:
        return [_refuse("outcome", f"outcome {outcome!r} is not one this ASOP declares "
                                   f"({', '.join(declared_outcomes)}); nothing is filed",
                        toward=[h["to"]["asop_id"] for h in body.get("handoffs", [])])]
    by_id = {h["id"]: h for h in body.get("handoffs", [])}

    # 1. requests: shape, target, outcome. Refused ones are dropped here and
    #    have no further effect.
    if requests is None:
        requests = []
    if not isinstance(requests, (list, tuple)):
        decisions.append(_refuse("malformed", "requests must be a list of filing requests"))
        requests = []
    grouped: dict[str, list[tuple[int, dict]]] = {}
    for i, raw in enumerate(requests):
        try:
            req = validate_filing_request(raw)
        except ChainContractError as e:
            decisions.append(_refuse("malformed", str(e), request=i))
            continue
        h = by_id.get(req["handoff"])
        if h is None or h["to"]["asop_id"] != req["to"]:
            decisions.append(_refuse("undeclared", f"request {i} names hand-off '{req['handoff']}' to "
                                                   f"'{req['to']}', which this ASOP does not declare", request=i))
            continue
        if not _fires(h, outcome):
            decisions.append(_refuse("outcome", f"request {i}: outcome {outcome!r} is not one hand-off "
                                                f"'{h['id']}' fires on ({', '.join(h['on_outcomes'])})",
                                     request=i, handoff=h["id"]))
            continue
        grouped.setdefault(h["id"], []).append((i, req))

    # 2. every declared hand-off that fires. A join (`after`) is not filed
    #    now: it is recorded as waiting, and filed by `plan_join` once
    #    `join_state` says its runs are finished.
    for h in body.get("handoffs", []):
        if not _fires(h, outcome):
            continue
        reqs = grouped.get(h["id"], [])
        if h["cardinality"] == "one":
            _check_one_requests(decisions, h, reqs)
        if "after" in h:
            decisions.append({"action": "wait", "handoff": h["id"], "after": list(h["after"])})
            continue
        _file_handoff(decisions, filed, h, reqs, outputs=outputs, run_inputs=run_inputs,
                      resolve=resolve, chain=chain, attempt=attempt,
                      approvals=approvals, output_digests=output_digests)
    return decisions


def _check_one_requests(decisions: list, h: dict, reqs: list) -> None:
    if len(reqs) > 1:
        decisions.append(_refuse("cardinality", f"hand-off '{h['id']}' is `one` and received "
                                                f"{len(reqs)} requests; all are disregarded",
                                 handoff=h["id"]))
    elif reqs and "subject" in reqs[0][1]:
        decisions.append(_refuse("subject", f"hand-off '{h['id']}' is `one`; its subject is the chain, "
                                            f"so a request may not name one", handoff=h["id"],
                                 request=reqs[0][0]))


def _file_handoff(decisions: list, filed: dict, h: dict, reqs: list, *, outputs: Mapping,
                  run_inputs: Mapping, resolve: Resolver, chain: str, attempt: int,
                  approvals: Optional[Mapping] = None, output_digests: Optional[Mapping] = None) -> None:
    """File one fired hand-off: resolve its target, expand its subjects, compute
    its inputs, and file or refuse each unit."""
    found = resolve(h["to"])
    if found is None:
        decisions.append(_refuse("version", f"no ACTIVE version of '{h['to']['asop_id']}' matches "
                                            f"{h['to'].get('version') or h['to'].get('versions')}",
                                 handoff=h["id"], toward=[h["to"]["asop_id"]]))
        return
    version, target = found
    declared = {i["name"] for i in target.get("inputs", [])}

    if h["cardinality"] == "one":
        units = [(chain, None)]
    else:
        try:
            pairs = expand_subjects(h, outputs)
        except (KeyError, ValueError) as e:
            decisions.append(_refuse("output", f"hand-off '{h['id']}': {e.args[0]}", handoff=h["id"],
                                     toward=[h["to"]["asop_id"]]))
            return
        if h["subject_from"].get("approved_subset"):
            subset = _approved_subset(h, outputs, approvals, output_digests)
            if isinstance(subset, str):
                decisions.append(_refuse("approval", f"hand-off '{h['id']}': {subset}", handoff=h["id"],
                                         toward=[h["to"]["asop_id"]]))
                return
            if subset is not None:
                pairs = [p for p in pairs if p[0] in subset]
        items = dict(pairs)
        accepted: list[tuple[str, dict]] = []
        for i, req in reqs:
            sid = req.get("subject")
            if sid is None or sid not in items:
                decisions.append(_refuse("subject", f"hand-off '{h['id']}': subject {sid!r} is not one "
                                                    f"'{h['subject_from']['output']}' yields",
                                         handoff=h["id"], request=i))
                continue
            accepted.append((sid, items[sid]))
        units = accepted or pairs
        if "order_by" in h["subject_from"]:
            problem = _order_problem(h, units)
            if problem and accepted:
                # The REQUESTS narrowed the fan-out into an order it cannot keep. A refused
                # request is as if it had not been written: refuse them, file the declaration.
                decisions.append(_refuse("order", f"hand-off '{h['id']}': the requested subjects {problem}; "
                                                  f"the requests are disregarded", handoff=h["id"]))
                units = pairs
                problem = _order_problem(h, units)
            if problem:
                decisions.append(_refuse("order", f"hand-off '{h['id']}': {problem}", handoff=h["id"],
                                         toward=[h["to"]["asop_id"]]))
                return

    for subject, item in units:
        try:
            inputs = {k: _resolve_value(v, subject=subject, item=item, run_inputs=run_inputs, outputs=outputs)
                      for k, v in h["inputs"].items()}
        except KeyError as e:
            decisions.append(_refuse("inputs", f"hand-off '{h['id']}': {e.args[0]}",
                                     handoff=h["id"], subject=subject, toward=[h["to"]["asop_id"]]))
            continue
        if set(inputs) != declared:
            decisions.append(_refuse("inputs", _input_gap(h["to"]["asop_id"], inputs, declared),
                                     handoff=h["id"], subject=subject, toward=[h["to"]["asop_id"]]))
            continue
        _file_or_dup(decisions, filed, handoff=h["id"], to=h["to"], version=version, subject=subject,
                     inputs=inputs, chain=chain, attempt=attempt)
        if item is not None and "order_by" in h["subject_from"] and decisions[-1]["action"] == "file":
            decisions[-1]["depends_on"] = [natural_key(chain, h["to"]["asop_id"], d, attempt)
                                           for d in _deps(item, h["subject_from"]["order_by"])]


def _deps(item: dict, field: str) -> list[str]:
    raw = item.get(field, [])
    if not isinstance(raw, list) or any(isinstance(d, bool) or not isinstance(d, (str, int)) for d in raw):
        raise ValueError(f"'{field}' must be a list of subject ids")
    out: list[str] = []
    for d in raw:
        if str(d) not in out:
            out.append(str(d))
    return out


def _order_problem(h: dict, units: list) -> Optional[str]:
    """Why these units cannot be filed in dependency order, or None."""
    field = h["subject_from"]["order_by"]
    filed = [s for s, _ in units]
    graph: dict[str, list[str]] = {}
    for subject, item in units:
        try:
            deps = _deps(item, field)
        except ValueError as e:
            return f"subject '{subject}': {e.args[0]}"
        for d in deps:
            if d == subject:
                return f"subject '{subject}' depends on itself"
            if d not in filed:
                return f"subject '{subject}' depends on '{d}', which this hand-off does not file"
        graph[subject] = deps
    state: dict[str, int] = {}

    def visit(n: str) -> Optional[str]:
        state[n] = 1
        for d in graph[n]:
            if state.get(d) == 1:
                return f"dependencies form a cycle through '{n}' and '{d}'"
            if d not in state:
                found = visit(d)
                if found:
                    return found
        state[n] = 2
        return None

    for n in filed:
        if n not in state:
            found = visit(n)
            if found:
                return found
    return None


def _approved_subset(h: dict, outputs: Mapping, approvals: Optional[Mapping],
                     output_digests: Optional[Mapping]) -> Any:
    """The approved subjects (a set), None for 'all approved', or a string
    saying why there is no usable approval."""
    name = h["subject_from"]["output"]
    approval = (approvals or {}).get(name)
    current = (output_digests or {}).get(name)
    if not isinstance(approval, Mapping):
        return f"output '{name}' has no approval"
    digest = approval.get("digest")
    if not isinstance(digest, str) or not digest.strip() or not isinstance(current, str):
        return f"the approval of output '{name}' binds no digest the engine can compare"
    if digest != current:
        return (f"the approval of output '{name}' binds {digest}, but the output is now {current}: "
                f"it approved something else, and is stale")
    subset = approval.get("subset")
    if subset is None:
        return None
    if not isinstance(subset, list) or any(isinstance(x, bool) or not isinstance(x, (str, int)) for x in subset):
        return f"the approval of output '{name}' has a subset that is not a list of subject ids"
    subset = {str(x) for x in subset}
    try:
        everything = _dig(outputs[name], h["subject_from"]["array"])
        ids = {str(i.get(h["subject_from"]["id_field"])) for i in everything if isinstance(i, dict)}
    except (KeyError, TypeError):
        ids = set()
    unknown = sorted(subset - ids)
    if unknown:
        return f"the approval of output '{name}' names {', '.join(unknown)}, which the output does not contain"
    return subset


def fanout_startable(handoff: dict, decisions: Iterable[Mapping], *, done: Iterable[str] = (),
                     started: Iterable[str] = ()) -> list[str]:
    """Which runs a fan-out filed may START now (§3.7, v3.6), in filing order.

    `handoff` is the validated hand-off; `decisions` are the parent run's
    recorded decisions; `started` holds natural keys whose run has started
    (running or finished) and `done` those finished done. A run may start when
    it has not started, every key in its `depends_on` is done, and starting it
    keeps the hand-off's running runs within `subject_from.concurrency`. A run
    whose dependency failed never becomes startable; the join over this
    hand-off is blocked (`join_state`), and that goes to a person."""
    done, started = set(done), set(started)
    mine = [d for d in decisions if d.get("handoff") == handoff["id"] and d.get("action") == "file"]
    limit = handoff.get("subject_from", {}).get("concurrency")
    running = sum(1 for d in mine if d["natural_key"] in started and d["natural_key"] not in done)
    out: list[str] = []
    for d in mine:
        key = d["natural_key"]
        if key in started or not all(k in done for k in d.get("depends_on", [])):
            continue
        if limit is not None and running + len(out) >= limit:
            break
        out.append(key)
    return out


def join_state(after: Iterable[str], *, decisions: Iterable[Mapping], done: Iterable[str] = (),
               failed: Iterable[str] = ()) -> str:
    """May a join be filed yet? `"wait"`, `"ready"` or `"blocked"`.

    `after` is the join's list of hand-off ids. `decisions` are the PARENT
    run's recorded hand-off decisions — from `plan_handoffs`, and from
    `plan_join` for any join already filed. `done` and `failed` are natural
    keys whose run finished done, or ended without done (a run re-run to done
    under its own key is done).

    The join waits on exactly the runs those hand-offs of THIS run filed or
    found already filed (`duplicate`): nothing else. A run of the same
    procedure filed by another path — an escalation's decision, another
    parent — is not part of it, however the graph moves.

    - `blocked` when a refusal of one of those hand-offs left a target unfiled
      (`toward` non-empty), when a run-level refusal (an undeclared outcome)
      did, or when one of its runs ended without done. A refusal
      is never an empty result. The engine surfaces a blocked join to a
      person; it clears by re-running the failed run to done under its own
      key, or by re-attempting the parent, which files afresh.
    - `wait` while a listed hand-off is itself a join not yet filed, or one of
      the runs is not yet done.
    - `ready` otherwise — INCLUDING when the hand-offs filed nothing. An
      empty fan-out is a finished one; a join waiting for runs that will never
      exist is a deadlock.
    A refused REQUEST (`toward` empty) blocks nothing.

    `decisions` are ONE attempt's. When the parent is re-attempted, the engine
    retires the waiting joins of its earlier attempts: they are never filed,
    or a stale fan-out would file a join after the fresh one (§3.7)."""
    after = set(after)
    keys: list[str] = []
    waiting: set[str] = set()
    filed_ids: set[str] = set()
    for d in decisions:
        hid = d.get("handoff")
        if hid is None and d.get("action") == "refuse" and d.get("toward"):
            return "blocked"  # a run-level refusal (an undeclared outcome) left every target unfiled
        if hid not in after:
            continue
        action = d.get("action")
        if action == "wait":
            waiting.add(hid)
        elif action == "refuse":
            if d.get("toward"):
                return "blocked"
        elif action in ("file", "duplicate"):
            filed_ids.add(hid)
            keys.append(d["natural_key"])
    done, failed = set(done), set(failed)
    if any(k in failed and k not in done for k in keys):
        return "blocked"
    if waiting - filed_ids or not all(k in done for k in keys):
        return "wait"
    return "ready"


def plan_join(body: dict, handoff_id: str, *, outcome: Optional[str], outputs: Mapping,
              run_inputs: Mapping, resolve: Resolver, chain: str, existing: Any = (),
              attempt: int = 1) -> list[dict]:
    """File a join (a hand-off with `after`) of a finished run, once
    `join_state` says `ready`. Same decisions as `plan_handoffs` for that one
    hand-off: its target resolves NOW (a range picks the version active when
    the join fires), and its inputs read the parent run's inputs and outputs.
    Returns [] when the run's outcome does not fire it."""
    h = next((x for x in body.get("handoffs", []) if x["id"] == handoff_id), None)
    if h is None or "after" not in h:
        raise ChainContractError(f"'{handoff_id}' is not a join (a hand-off with 'after') of this ASOP.")
    declared_outcomes = body.get("outcomes")
    if (declared_outcomes and outcome not in declared_outcomes) or not _fires(h, outcome):
        return []
    decisions: list[dict] = []
    _file_handoff(decisions, _filed_map(existing), h, [], outputs=outputs, run_inputs=run_inputs,
                  resolve=resolve, chain=chain, attempt=attempt)
    return decisions


def _input_gap(target: str, inputs: Mapping, declared: set) -> str:
    parts = []
    if set(inputs) - declared:
        parts.append(f"'{target}' does not declare {', '.join(sorted(set(inputs) - declared))}")
    if declared - set(inputs):
        parts.append(f"{', '.join(sorted(declared - set(inputs)))} not supplied")
    return "; ".join(parts)


def _fires(h: dict, outcome: Optional[str]) -> bool:
    return "on_outcomes" not in h or outcome in h["on_outcomes"]


def plan_escalation(body: dict, *, subject: str, run_inputs: Mapping, context: Mapping,
                    resolve: Resolver, chain: str, existing: Any = (), attempt: int = 1) -> Optional[dict]:
    """What §5.4's second failure files, when the ASOP declares `on_escalate`;
    None when it does not (the gate's own `escalate_to` then applies).

    `subject` names the escalation — by convention
    `<asop_id>/<run subject>/<step>` — and is chosen by the engine, never the run.
    The target's declared inputs are filled from the `on_escalate` mapping first
    and then, for inputs the mapping leaves open, from `context`, the engine's
    account of what failed and why. Context keys the target does not declare
    are ignored; a declared input neither supplies is refused (`inputs`).

    The escalated run is not cancelled: once the person answers and the run
    completes, its hand-offs fire at completion like any other run's."""
    esc = body.get("on_escalate")
    if not esc:
        return None
    if not isinstance(subject, str) or not subject.strip() or subject != subject.strip():
        return _refuse("malformed", f"escalation subject {subject!r} is not an exact non-empty name",
                       toward=[esc["to"]["asop_id"]])
    found = resolve(esc["to"])
    if found is None:
        return _refuse("version", f"no ACTIVE version of '{esc['to']['asop_id']}' matches "
                                  f"{esc['to'].get('version') or esc['to'].get('versions')}",
                       toward=[esc["to"]["asop_id"]])
    version, target = found
    declared = {i["name"] for i in target.get("inputs", [])}
    try:
        mapped = {k: _resolve_value(v, subject=None, item=None, run_inputs=run_inputs, outputs={})
                  for k, v in esc["inputs"].items()}
    except KeyError as e:
        return _refuse("inputs", f"'on_escalate': {e.args[0]}", toward=[esc["to"]["asop_id"]])
    inputs = {k: context[k] for k in declared if k not in mapped and k in context}
    if not all(_is_scalar(v) for v in inputs.values()):
        return _refuse("inputs", "'on_escalate': the escalation context supplied a non-scalar value",
                       toward=[esc["to"]["asop_id"]])
    inputs.update(mapped)
    if set(inputs) != declared:
        return _refuse("inputs", _input_gap(esc["to"]["asop_id"], inputs, declared), toward=[esc["to"]["asop_id"]])
    out: list[dict] = []
    _file_or_dup(out, _filed_map(existing), handoff=None, to=esc["to"], version=version,
                 subject=subject, inputs=inputs, chain=chain, attempt=attempt)
    return out[0]


# ---------------------------------------------------------------------------
# scheduling
# ---------------------------------------------------------------------------

def requires_satisfied(requires: list[dict], *, chain: str, runs: Iterable[Mapping],
                       approved: Iterable[str] = (), approvals: Optional[Mapping] = None,
                       digests: Optional[Mapping] = None, chain_gates: Optional[Mapping] = None) -> bool:
    """May a filed run start? Every predicate must hold, within ONE chain.

    `runs` are this chain's runs as `{asop_id, subject, attempt, done}`.
    `approved` holds artefacts a declared human approved IN THIS CHAIN (§9 —
    the engine resolves the approver; this function does not).

    - `run_done(X)`: X's CHAIN run — the one whose subject is the chain, as a
      `one` hand-off files it — exists, and its highest attempt is done. A
      per-subject run of X is not it: waiting on a fan-out is a join, declared
      with `after` on the procedure that fanned out.
    - `human_approved(A)`: A is approved in this chain.

    `requires` is a START condition. A later attempt of X, filed after this
    run started, does not un-start it; that is staleness, which the engine
    handles by lineage (§3.7). A run whose `requires` never hold is held, and
    the engine's liveness clock surfaces it to a person.

    v3.6:
    - `human_approved(A, binds_digest)`: the approval must bind A's CURRENT
      digest — `approvals[A]["digest"] == digests[A]`. An approval of an
      earlier A approved something else. Without the digests this fails closed;
    - `chain_gate(id)`: the engine ran the chain-level gate over the run
      ledger and `chain_gates[id]` is `"passed"`. `failed`, `cannot_evaluate`
      and absent all hold the run."""
    latest: dict[str, Mapping] = {}
    for r in runs:
        if str(r["subject"]) != chain:
            continue
        a = r["asop_id"]
        if a not in latest or r.get("attempt", 1) > latest[a].get("attempt", 1):
            latest[a] = r
    ok = set(approved) | set(approvals or {})
    for req in requires:
        if req["kind"] == "human_approved":
            if req["artefact"] not in ok:
                return False
            if req.get("binds_digest"):
                rec = (approvals or {}).get(req["artefact"])
                current = (digests or {}).get(req["artefact"])
                if (not isinstance(rec, Mapping) or not isinstance(rec.get("digest"), str)
                        or not isinstance(current, str) or rec["digest"] != current):
                    return False
        elif req["kind"] == "chain_gate":
            if (chain_gates or {}).get(req["id"]) != "passed":
                return False
        else:
            r = latest.get(req["asop_id"])
            if r is None or not r["done"]:
                return False
    return True


# ---------------------------------------------------------------------------
# v3.6: entry gates (§3.8) and rework (§3.9)
# ---------------------------------------------------------------------------

ENTRY_ACTIONS = ("start", "wait", "not_ready")


def plan_entry(entry_gates: Iterable[Mapping], *, outcomes: Mapping, producers: Mapping) -> dict:
    """May a filed run start, as far as its entry gates are concerned? (§3.8)

    `entry_gates` is the record's validated list; `outcomes` maps a gate id to
    the outcome the engine's run of it reached (`asop.gates.GATE_OUTCOMES`);
    `producers` maps each declared input to the natural key of the run that
    handed it on, or None when it was supplied at chain start (a source).

    - `{"action": "start"}` when every entry gate passed;
    - `{"action": "wait"}` while one has not been evaluated and none refused;
    - `{"action": "not_ready", "refile": [...], "hold": [...]}` otherwise:
      - a gate over an input a PREDECESSOR handed on re-files that producer as
        its next attempt — `charged: true` when the gate `failed` (the
        producer's output broke the declared contract, which §5.4 counts
        against the producer), `charged: false` when it `cannot_evaluate`;
      - a gate over a SOURCE holds the run for a person (`source_not_ready`
        or `source_rejected`): there is no producer in the chain to re-file,
        and a change of the source re-evaluates it (§5.7).

    The consumer is never charged either way: it did not start, so it did not
    fail, and no attempt of it is consumed. Dependents are never released."""
    refile: dict[str, dict] = {}
    hold: list[dict] = []
    waiting = False
    for g in entry_gates:
        got = outcomes.get(g["id"])
        if got == "passed":
            continue
        if got not in ("failed", "cannot_evaluate"):
            waiting = True
            continue
        for name in g["over"]:
            producer = producers.get(name)
            if producer is None:
                hold.append({"gate": g["id"], "input": name,
                             "reason": "source_not_ready" if got == "cannot_evaluate" else "source_rejected"})
            else:
                prev = refile.get(producer)
                charged = got == "failed" or bool(prev and prev["charged"])
                gates = (prev["gates"] if prev else []) + ([g["id"]] if not prev or g["id"] not in prev["gates"] else [])
                refile[producer] = {"natural_key": producer, "gates": gates, "charged": charged}
    if refile or hold:
        return {"action": "not_ready", "refile": list(refile.values()), "hold": hold}
    return {"action": "wait"} if waiting else {"action": "start"}


def preflight_checks(body: dict, resolve: Resolver) -> list[dict]:
    """The consumer's lint, for the producer to run (§3.8, v3.6).

    For every hand-off declaring `preflight: true`, the entry gates of the
    target it would file, with the input mapping that feeds them. The engine
    runs these before the producer's run is recorded done; one that does not
    pass fails the producer's last step, where the defect was made, instead of
    surfacing hours later as the consumer's failure."""
    out: list[dict] = []
    for h in body.get("handoffs", []):
        if not h.get("preflight"):
            continue
        found = resolve(h["to"])
        if found is None:
            continue  # filing refuses it (`version`); nothing to preflight against
        for g in found[1].get("entry_gates", []):
            out.append({"handoff": h["id"], "to": {"asop_id": h["to"]["asop_id"], "version": found[0]},
                        "entry_gate": g["id"], "gate": g["gate"],
                        "inputs": {k: h["inputs"][k] for k in g["over"] if k in h["inputs"]}})
    return out


def _refuse_rework(rule: str, detail: str, **ctx) -> dict:
    return {"action": "refuse", "code": f"{REWORK_REFUSED}:{rule}", "detail": detail, **ctx}


def plan_rework(body: dict, *, outcome: Optional[str] = None, failed_step: Optional[int] = None,
                rounds_used: Optional[Mapping] = None, outputs: Optional[Mapping] = None,
                owner: Optional[Mapping] = None, resolve: Optional[Resolver] = None,
                chain: str = "") -> Optional[dict]:
    """What a declared rework edge does (§3.9, v3.6). None when no edge
    matches the trigger: the run then goes where it would have gone under
    v3.5 — a repair for a failed step, its hand-offs for a finished run.

    `failed_step` triggers a step edge; `outcome` a procedure edge.
    `rounds_used` maps an edge id to the rounds already spent IN THIS CHAIN
    (for a procedure edge, against this owner). `owner` is the upstream run the
    engine resolved by lineage: `{asop_id, subject, attempt, inputs}`.

    - `{"action": "rework", ...}`: re-execute steps `from_step`..`through_step`
      of this run, or re-file the owner as its next attempt with the SAME
      inputs it had, and `prior_findings` (the scalar the `findings` output
      holds) as run context outside its inputs. `consumes_attempt` and
      `tier_bump` are carried as declared;
    - `{"action": "escalate", "reason": "rounds_exhausted"}` once `max_rounds`
      are spent — §5.4's person, never another round;
    - `{"action": "refuse", "code": "rework_refused:<rule>"}`."""
    edge = None
    for e in body.get("rework", []):
        if failed_step is not None and e.get("on_step_failure") == failed_step:
            edge = e
        elif failed_step is None and outcome is not None and outcome in e.get("on_outcomes", []):
            edge = e
        if edge:
            break
    if edge is None:
        return None
    used = (rounds_used or {}).get(edge["id"], 0)
    if used >= edge["max_rounds"]:
        return {"action": "escalate", "rework": edge["id"], "reason": "rounds_exhausted", "rounds": used}
    base = {"action": "rework", "rework": edge["id"], "round": used + 1,
            "consumes_attempt": edge["consumes_attempt"], "tier_bump": bool(edge.get("tier_bump"))}
    if "step" in edge["to"]:
        return {**base, "from_step": edge["to"]["step"], "through_step": edge["on_step_failure"]}
    target = edge["to"]["asop_id"]
    if not isinstance(owner, Mapping) or owner.get("asop_id") != target:
        return _refuse_rework("owner", f"rework '{edge['id']}' re-files '{target}', and the engine named no "
                                       f"upstream run of it in this chain", rework=edge["id"])
    found = resolve(edge["to"]) if resolve else None
    if found is None:
        return _refuse_rework("version", f"rework '{edge['id']}': no version of '{target}' matches "
                                         f"{edge['to'].get('version') or edge['to'].get('versions')}",
                              rework=edge["id"])
    decision = {**base, "to": {"asop_id": target, "version": found[0]}, "subject": str(owner["subject"]),
                "inputs": dict(owner.get("inputs") or {}), "attempt": int(owner.get("attempt", 1)) + 1}
    decision["natural_key"] = natural_key(chain, target, decision["subject"], decision["attempt"])
    if "findings" in edge:
        value = (outputs or {}).get(edge["findings"])
        if value is None or not _is_scalar(value):
            return _refuse_rework("findings", f"rework '{edge['id']}': output '{edge['findings']}' is "
                                              f"{'missing' if value is None else 'not a scalar reference'}",
                                  rework=edge["id"])
        decision["prior_findings"] = value
    return decision
