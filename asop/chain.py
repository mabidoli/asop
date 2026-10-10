"""Hand-offs — how one ASOP's finished run files the next (ASOP.md §3.7).

v3 kept sequencing between procedures out of the artefact (§11.4, 2026-09-04)
and put it in the harness's configuration. The first real chained execution —
ten procedures run end to end over a multi-part programme — showed what that
costs: the flow lived in one engine's rules file, an ASOP shared to another
harness arrived without its place in any flow, and porting the engine meant
porting that flow with it. v3.5 moves the *declaration* into the artefact and
leaves the *execution* where it was:

- the predecessor declares where it MAY hand off (`handoffs`), on which of its
  own declared `outcomes`, and how each successor input is computed;
- the successor declares when it may START (`requires`) — joins and approvals
  belong to the procedure that waits, because it is the only one that sees
  every predecessor;
- a finished run MAY narrow or supplement what fires by writing filing
  requests (`asop.filing-request/1`);
- a generic engine runs what is declared and REFUSES anything that is not. It
  never repairs a request.

What stays in the engine — concurrency, budgets, staleness, retries — is listed
in ASOP.md §3.7 and is deliberately not ASOP data: it is the same for every flow.

This module is the reference for three questions, each a pure function so that
any implementation can be checked against the same vectors:

- `validate_chain_fields`   is this record's chaining well formed? (authoring)
- `validate_handoff_targets` do its targets accept what it hands them? (authoring,
                             needs a resolver for the other records)
- `plan_handoffs`            given a finished run, what is filed and what is
                             refused? (filing)
- `requires_satisfied`       may a filed run start? (scheduling)
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Optional

FILING_REQUEST_SCHEMA = "asop.filing-request/1"

CARDINALITIES = ("one", "many")

# Closed. A predicate an engine cannot evaluate identically to another engine is
# a fork of the contract, so the list grows by spec version, never by author.
REQUIRE_KINDS = ("run_done", "all_runs_done", "human_approved")

# Predicates one engine used that are deliberately NOT ASOP data, each with
# where it lives instead. Named so the refusal says where to put it rather than
# reading as a typo.
NOT_REQUIRE_KINDS = {
    "slot_free": "concurrency is engine configuration, the same for every flow — not ASOP data (§3.7)",
    "artefact_current": "staleness is an engine duty, driven by recorded lineage and input hashes (§3.7)",
    "no_run_for_subject": "filing is idempotent on the natural key, so a second run for a subject is never filed (§3.7)",
}

# The refusal code is `handoff_refused:<rule>`, the same shape as
# `revision_policy:<rule>`: one code for the class, the rule after the colon.
HANDOFF_REFUSED = "handoff_refused"
HANDOFF_RULES: dict[str, str] = {
    "malformed": "the filing request is not a valid asop.filing-request/1 document",
    "undeclared": "the request names a hand-off the filing ASOP does not declare, or a target that does not match it",
    "outcome": "the filing run's outcome is not one the hand-off fires on",
    "cardinality": "a `one` hand-off received more than one request",
    "subject": "a request names a subject the hand-off's `subject_from` does not yield",
    "output": "the output a hand-off reads is missing, or does not have the declared shape",
    "inputs": "the successor's inputs are not satisfied: an undeclared input, a missing one, or a request overriding a declared mapping",
    "version": "no ACTIVE version of the successor falls inside the declared version range",
}

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RANGE = re.compile(r"^\s*>=\s*(\d+)\s*(?:,\s*<\s*(\d+)\s*)?$")
_SCALAR = (str, int, float, bool)


class ChainContractError(ValueError):
    """A chaining declaration is malformed. Raised at authoring; the store
    reports it as `sop_refused`, like every other record error."""


# ---------------------------------------------------------------------------
# small parsers
# ---------------------------------------------------------------------------

def parse_version_range(spec: str) -> tuple[int, Optional[int]]:
    """`">=N"` or `">=N,<M"` → (N, M or None). Nothing else: a range language
    two engines parse differently is a contract fork."""
    if not isinstance(spec, str):
        raise ChainContractError(f"a version range must be a string like '>=3' or '>=3,<5', got {spec!r}.")
    m = _RANGE.match(spec)
    if not m:
        raise ChainContractError(
            f"version range {spec!r} is not '>=N' or '>=N,<M'. Those are the only two forms."
        )
    lo, hi = int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
    if lo < 1:
        raise ChainContractError(f"version range {spec!r}: versions start at 1.")
    if hi is not None and hi <= lo:
        raise ChainContractError(f"version range {spec!r} is empty.")
    return lo, hi


def version_in(target: dict, version: int) -> bool:
    """Does `version` satisfy a validated `to` reference?"""
    if "version" in target:
        return version == target["version"]
    lo, hi = parse_version_range(target["versions"])
    return version >= lo and (hi is None or version < hi)


def natural_key(chain: str, asop_id: str, subject: str, attempt: int = 1) -> str:
    """`{chain}|{asop_id}|{subject}|a{attempt}` — unique per filed run, which is
    what makes filing idempotent: a replay files nothing twice."""
    return f"{chain}|{asop_id}|{subject}|a{attempt}"


def _nonblank(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChainContractError(f"{what} must be a non-empty string, got {value!r}.")
    return value.strip()


def _named_records(raw: Any, what: str) -> list[dict]:
    """`[{name, description?, kind?}]` — the shape `inputs` already has, reused
    for `outputs` so an author learns one thing."""
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
            raise ChainContractError(f"'{what}'[{i}]: unknown field(s) {', '.join(sorted(extra))}.")
        name = _nonblank(rec.get("name"), f"'{what}'[{i}].name")
        if name in seen:
            raise ChainContractError(f"'{what}' declares '{name}' twice.")
        seen.add(name)
        clean = {"name": name}
        if rec.get("description") is not None:
            clean["description"] = _nonblank(rec["description"], f"'{what}'[{i}].description")
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
        raise ChainContractError(f"{what}: unknown field(s) {', '.join(sorted(extra))}.")
    out = {"asop_id": _nonblank(raw.get("asop_id"), f"{what}.asop_id")}
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
        out["versions"] = raw["versions"].replace(" ", "")
    return out


def _expr(value: Any, what: str, *, inputs: set[str], outputs: set[str], subject_ok: bool,
          outputs_ok: bool = True) -> Any:
    """An input mapping value: a reference (`$subject`, `$subject.<field>`,
    `$run.inputs.<name>`, `$run.outputs.<name>`) or a scalar literal. A literal
    string that must begin with `$` is written `$$…`."""
    if value is None or not isinstance(value, _SCALAR):
        raise ChainContractError(f"{what} must be a reference string or a scalar literal, got {value!r}.")
    if not isinstance(value, str) or not value.startswith("$") or value.startswith("$$"):
        return value
    if value == "$subject" or value.startswith("$subject."):
        if not subject_ok:
            raise ChainContractError(
                f"{what}: '{value}' — `$subject` exists only on a `many` hand-off, where each "
                f"filed run has a subject drawn from `subject_from`."
            )
        if value.startswith("$subject.") and not value[len("$subject."):]:
            raise ChainContractError(f"{what}: '{value}' names no field.")
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
    return {_nonblank(k, f"{what} key"): _expr(v, f"{what}.{k}", **kw) for k, v in raw.items()}


def _subject_from(raw: Any, what: str, outputs: set[str]) -> dict:
    if not isinstance(raw, dict):
        raise ChainContractError(f"{what} must be {{output, array, id_field, where?, where_not?}}.")
    extra = set(raw) - {"output", "array", "id_field", "where", "where_not"}
    if extra:
        raise ChainContractError(f"{what}: unknown field(s) {', '.join(sorted(extra))}.")
    out = {k: _nonblank(raw.get(k), f"{what}.{k}") for k in ("output", "array", "id_field")}
    if out["output"] not in outputs:
        raise ChainContractError(
            f"{what}.output '{out['output']}' is not a declared output of this ASOP "
            f"(declared: {', '.join(sorted(outputs)) or 'none'}). A fan-out reads only what the "
            f"procedure promises to write."
        )
    where = raw.get("where")
    if where is not None:
        if not isinstance(where, dict) or not where:
            raise ChainContractError(f"{what}.where must be a non-empty mapping of field to allowed values.")
        clean = {}
        for k, v in where.items():
            vals = v if isinstance(v, list) else [v]
            if not vals or not all(isinstance(x, _SCALAR) for x in vals):
                raise ChainContractError(f"{what}.where.{k} must be a scalar or a non-empty list of scalars.")
            clean[_nonblank(k, f"{what}.where key")] = vals
        out["where"] = clean
    where_not = raw.get("where_not")
    if where_not is not None:
        if not isinstance(where_not, dict) or not where_not:
            raise ChainContractError(f"{what}.where_not must be a non-empty mapping of field to excluded value.")
        for k, v in where_not.items():
            if not isinstance(v, _SCALAR):
                raise ChainContractError(f"{what}.where_not.{k} must be a scalar.")
        out["where_not"] = {_nonblank(k, f"{what}.where_not key"): v for k, v in where_not.items()}
    return out


# ---------------------------------------------------------------------------
# authoring: one record
# ---------------------------------------------------------------------------

CHAIN_FIELDS = ("outcomes", "outputs", "handoffs", "on_escalate", "requires")


def validate_chain_fields(payload: dict, *, asop_id: Optional[str] = None,
                          input_names: Iterable[str] = ()) -> dict:
    """Normalise the five optional chaining fields of an ASOP body. Returns only
    the fields present. Raises `ChainContractError`; never repairs.

    `asop_id` is the record's own id when known (a store knows it; a bare body
    may not) — it is used to refuse a hand-off to itself."""
    out: dict = {}
    inputs = set(input_names)

    outcomes: list[str] = []
    if payload.get("outcomes") is not None:
        raw = payload["outcomes"]
        if isinstance(raw, (str, dict)) or not isinstance(raw, (list, tuple)) or not raw:
            raise ChainContractError("'outcomes' must be a non-empty LIST of outcome names. Omit it if there are none.")
        for i, o in enumerate(raw):
            name = _nonblank(o, f"'outcomes'[{i}]")
            if name in outcomes:
                raise ChainContractError(f"'outcomes' declares '{name}' twice.")
            outcomes.append(name)
        out["outcomes"] = outcomes

    outputs_list = _named_records(payload.get("outputs"), "outputs")
    if payload.get("outputs") is not None:
        out["outputs"] = outputs_list
    outputs = {o["name"] for o in outputs_list}

    if payload.get("handoffs") is not None:
        raw = payload["handoffs"]
        if isinstance(raw, (str, dict)) or not isinstance(raw, (list, tuple)) or not raw:
            raise ChainContractError("'handoffs' must be a non-empty LIST. Omit it for a procedure that hands off nothing.")
        seen: set[str] = set()
        handoffs = []
        for i, h in enumerate(raw):
            what = f"'handoffs'[{i}]"
            if not isinstance(h, dict):
                raise ChainContractError(f"{what} must be a mapping.")
            extra = set(h) - {"id", "to", "on_outcomes", "cardinality", "subject_from", "inputs"}
            if extra:
                raise ChainContractError(f"{what}: unknown field(s) {', '.join(sorted(extra))}.")
            hid = _nonblank(h.get("id"), f"{what}.id")
            if not _ID.match(hid):
                raise ChainContractError(f"{what}.id '{hid}' must be letters, digits, '.', '_' or '-'.")
            if hid in seen:
                raise ChainContractError(f"'handoffs' declares id '{hid}' twice.")
            seen.add(hid)
            what = f"hand-off '{hid}'"
            clean: dict = {"id": hid, "to": _target(h.get("to"), f"{what}.to")}
            if asop_id is not None and clean["to"]["asop_id"] == asop_id:
                raise ChainContractError(
                    f"{what} hands off to its own ASOP. A loop is a nested step or a repair (§5.4); "
                    f"a procedure that files itself has no bound."
                )
            if h.get("on_outcomes") is not None:
                oo = h["on_outcomes"]
                if isinstance(oo, (str, dict)) or not isinstance(oo, (list, tuple)) or not oo:
                    raise ChainContractError(f"{what}.on_outcomes must be a non-empty LIST. Omit it to fire on every completion.")
                if not outcomes:
                    raise ChainContractError(
                        f"{what}.on_outcomes names outcomes, but the ASOP declares no 'outcomes'. "
                        f"Declare them, so a misspelt outcome is refused here instead of never firing."
                    )
                cleaned_oo = []
                for o in oo:
                    o = _nonblank(o, f"{what}.on_outcomes entry")
                    if o not in outcomes:
                        raise ChainContractError(
                            f"{what}.on_outcomes names '{o}', which is not a declared outcome "
                            f"(declared: {', '.join(outcomes)})."
                        )
                    if o not in cleaned_oo:
                        cleaned_oo.append(o)
                clean["on_outcomes"] = cleaned_oo
            card = h.get("cardinality", "one")
            if card not in CARDINALITIES:
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
            clean["inputs"] = _input_map(h.get("inputs"), f"{what}.inputs", inputs=inputs, outputs=outputs,
                                         subject_ok=(card == "many"))
            handoffs.append(clean)
        out["handoffs"] = handoffs

    if payload.get("on_escalate") is not None:
        raw = payload["on_escalate"]
        if not isinstance(raw, dict):
            raise ChainContractError("'on_escalate' must be {to, inputs?}.")
        extra = set(raw) - {"to", "inputs"}
        if extra:
            raise ChainContractError(f"'on_escalate': unknown field(s) {', '.join(sorted(extra))}.")
        esc = {"to": _target(raw.get("to"), "'on_escalate'.to")}
        if asop_id is not None and esc["to"]["asop_id"] == asop_id:
            raise ChainContractError("'on_escalate' targets its own ASOP. An escalation must reach a person (§5.4).")
        esc["inputs"] = _input_map(raw.get("inputs"), "'on_escalate'.inputs", inputs=inputs, outputs=outputs,
                                   subject_ok=False, outputs_ok=False)
        out["on_escalate"] = esc

    if payload.get("requires") is not None:
        raw = payload["requires"]
        if isinstance(raw, (str, dict)) or not isinstance(raw, (list, tuple)) or not raw:
            raise ChainContractError("'requires' must be a non-empty LIST. Omit it for a procedure that may start when filed.")
        reqs = []
        for i, r in enumerate(raw):
            what = f"'requires'[{i}]"
            if not isinstance(r, dict):
                raise ChainContractError(f"{what} must be a mapping with a 'kind'.")
            kind = r.get("kind")
            if kind in NOT_REQUIRE_KINDS:
                raise ChainContractError(f"{what}: '{kind}' is not a requires kind — {NOT_REQUIRE_KINDS[kind]}.")
            if kind not in REQUIRE_KINDS:
                raise ChainContractError(
                    f"{what}: kind {kind!r} is not one of {', '.join(REQUIRE_KINDS)}. The list is closed: "
                    f"a predicate two engines evaluate differently is a fork of the contract."
                )
            if kind in ("run_done", "all_runs_done"):
                if set(r) != {"kind", "asop_id"}:
                    raise ChainContractError(f"{what}: '{kind}' takes exactly {{kind, asop_id}}.")
                ref = _nonblank(r["asop_id"], f"{what}.asop_id")
                if asop_id is not None and ref == asop_id:
                    raise ChainContractError(f"{what}: a procedure cannot wait on its own runs.")
                reqs.append({"kind": kind, "asop_id": ref})
            else:  # human_approved
                if set(r) != {"kind", "artefact"}:
                    raise ChainContractError(f"{what}: 'human_approved' takes exactly {{kind, artefact}}.")
                reqs.append({"kind": kind, "artefact": _nonblank(r["artefact"], f"{what}.artefact")})
        out["requires"] = reqs

    return out


# ---------------------------------------------------------------------------
# authoring: across records
# ---------------------------------------------------------------------------

Resolver = Callable[[dict], Optional[tuple[int, dict]]]
"""Given a validated `to` reference, return `(version, validated body)` of the
version that would be filed today — the ACTIVE version inside the pin or range —
or None if there is none. The store owns resolution; this module never guesses."""


def validate_handoff_targets(body: dict, resolve: Resolver) -> None:
    """Check a validated body's hand-offs against the records they name: the
    target resolves, every input it is handed is one it declares, and every
    input it declares is handed to it. An escalation target must contain a step
    a person does — §5.4's second failure "escalates to a human", and a
    decision procedure no person takes part in is not that.

    Run at activation, when the targets are knowable; repeated at filing by
    `plan_handoffs`, because a target may have moved since."""
    for h in body.get("handoffs", []):
        _check_target(h["to"], h["inputs"], f"hand-off '{h['id']}'", resolve)
    esc = body.get("on_escalate")
    if esc:
        target = _check_target(esc["to"], esc["inputs"], "'on_escalate'", resolve, partial=True)
        roles = target.get("roles", {})
        human_step = any(
            "uses" not in st and roles.get(st.get("role"), {}).get("kind") == "human"
            for st in target.get("steps", [])
        )
        if not human_step:
            raise ChainContractError(
                f"'on_escalate' targets '{esc['to']['asop_id']}', which has no step a human role does. "
                f"An escalation reaches a person (§5.4)."
            )


def _check_target(to: dict, mapping: dict, what: str, resolve: Resolver, *, partial: bool = False) -> dict:
    found = resolve(to)
    if found is None:
        raise ChainContractError(f"{what}: no ACTIVE version of '{to['asop_id']}' matches {to.get('version') or to.get('versions')}.")
    _, target = found
    declared = {i["name"] for i in target.get("inputs", [])}
    undeclared = sorted(set(mapping) - declared)
    if undeclared:
        raise ChainContractError(
            f"{what} hands '{to['asop_id']}' input(s) {', '.join(undeclared)}, which it does not declare "
            f"(declared: {', '.join(sorted(declared)) or 'none'})."
        )
    # An escalation's inputs are completed by the engine with the escalation's
    # own context (what failed, and why), so only a regular hand-off must cover
    # every declared input here.
    if not partial:
        missing = sorted(declared - set(mapping))
        if missing:
            raise ChainContractError(
                f"{what} leaves '{to['asop_id']}' input(s) {', '.join(missing)} unsupplied; filing would "
                f"refuse it (`inputs_missing`). Map them, or have the run supply them in its filing request."
            )
    return target


# ---------------------------------------------------------------------------
# filing
# ---------------------------------------------------------------------------

def validate_filing_request(doc: Any) -> dict:
    """`asop.filing-request/1`: `{schema, handoff, to, subject?, inputs?, reason?}`.
    Raises `ChainContractError` (filed as `handoff_refused:malformed`)."""
    if not isinstance(doc, dict):
        raise ChainContractError("a filing request must be a mapping.")
    if doc.get("schema") != FILING_REQUEST_SCHEMA:
        raise ChainContractError(f"a filing request declares schema '{FILING_REQUEST_SCHEMA}', got {doc.get('schema')!r}.")
    extra = set(doc) - {"schema", "handoff", "to", "subject", "inputs", "reason"}
    if extra:
        raise ChainContractError(f"filing request: unknown field(s) {', '.join(sorted(extra))}.")
    out = {"schema": FILING_REQUEST_SCHEMA,
           "handoff": _nonblank(doc.get("handoff"), "filing request 'handoff'"),
           "to": _nonblank(doc.get("to"), "filing request 'to'")}
    if doc.get("subject") is not None:
        s = doc["subject"]
        if not isinstance(s, (str, int)) or isinstance(s, bool) or not str(s).strip():
            raise ChainContractError("filing request 'subject' must be a non-empty string or an integer.")
        out["subject"] = str(s).strip()
    inputs = doc.get("inputs")
    if inputs is not None:
        if not isinstance(inputs, dict):
            raise ChainContractError("filing request 'inputs' must be a mapping.")
        out["inputs"] = dict(inputs)
    if doc.get("reason") is not None:
        out["reason"] = _nonblank(doc["reason"], "filing request 'reason'")
    return out


def _dig(data: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            raise KeyError(path)
        data = data[part]
    return data


def expand_subjects(handoff: dict, outputs: dict) -> list[tuple[str, dict]]:
    """The `(subject id, item)` pairs a `many` hand-off yields from the run's
    outputs, in output order, first occurrence wins. Raises `KeyError` /
    `ValueError` when the output does not have the declared shape."""
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
        if any(item.get(k) not in vals for k, vals in sf.get("where", {}).items()):
            continue
        if any(item.get(k) == v for k, v in sf.get("where_not", {}).items()):
            continue
        if sf["id_field"] not in item or item[sf["id_field"]] in (None, ""):
            raise ValueError(f"an item of '{sf['array']}' has no '{sf['id_field']}'")
        sid = str(item[sf["id_field"]])
        if sid not in seen:
            seen.add(sid)
            pairs.append((sid, item))
    return pairs


def _resolve_value(value: Any, *, subject: Optional[str], item: Optional[dict],
                   run_inputs: dict, outputs: dict) -> Any:
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    if value.startswith("$$"):
        return value[1:]
    if value == "$subject":
        return subject
    if value.startswith("$subject."):
        field = value[len("$subject."):]
        if item is None or field not in item:
            raise KeyError(value)
        return item[field]
    if value.startswith("$run.inputs."):
        name = value[len("$run.inputs."):]
        if name not in run_inputs:
            raise KeyError(value)
        return run_inputs[name]
    name = value[len("$run.outputs."):]
    if name not in outputs:
        raise KeyError(value)
    return outputs[name]


def _refuse(rule: str, detail: str, **ctx) -> dict:
    return {"action": "refuse", "code": f"{HANDOFF_REFUSED}:{rule}", "detail": detail, **ctx}


def plan_handoffs(body: dict, *, outcome: Optional[str], outputs: dict, run_inputs: dict,
                  requests: list, resolve: Resolver, chain: str,
                  existing_keys: Iterable[str] = ()) -> list[dict]:
    """What a finished run files. `body` is the filing ASOP's validated body.

    Returns one decision per filing or refusal, in declaration order:

    - `{"action": "file", "handoff", "to": {asop_id, version}, "subject", "inputs", "natural_key"}`
    - `{"action": "duplicate", "handoff", "natural_key"}` — already filed; nothing happens.
      Not a refusal: five runs handing off to one join is the normal case.
    - `{"action": "refuse", "code": "handoff_refused:<rule>", "detail", ...}` — ledgered,
      never silently dropped, never repaired.

    Semantics (ASOP.md §3.7):
    - a hand-off FIRES when the run completed with an outcome in its `on_outcomes`
      (or on every completion when `on_outcomes` is absent);
    - a fired `one` hand-off files one run whose subject is the chain;
    - a fired `many` hand-off files one run per subject `subject_from` yields — or,
      when the run wrote requests for it, exactly the requested subjects, each of
      which must be one `subject_from` yields;
    - a request may SUPPLY a successor input the declaration does not map; it may
      never override one the declaration maps.
    """
    decisions: list[dict] = []
    filed: set[str] = set(existing_keys)
    by_id = {h["id"]: h for h in body.get("handoffs", [])}

    # 1. requests: shape, target, outcome — refused requests do not count.
    grouped: dict[str, list[dict]] = {}
    for i, raw in enumerate(requests or []):
        try:
            req = validate_filing_request(raw)
        except ChainContractError as e:
            decisions.append(_refuse("malformed", str(e), request=i))
            continue
        h = by_id.get(req["handoff"])
        if h is None or h["to"]["asop_id"] != req["to"]:
            decisions.append(_refuse("undeclared",
                                     f"request {i} names hand-off '{req['handoff']}' to '{req['to']}', "
                                     f"which this ASOP does not declare", request=i))
            continue
        if not _fires(h, outcome):
            decisions.append(_refuse("outcome",
                                     f"request {i}: outcome {outcome!r} is not one hand-off '{h['id']}' fires on "
                                     f"({', '.join(h['on_outcomes'])})", request=i, handoff=h["id"]))
            continue
        grouped.setdefault(h["id"], []).append(req)

    # 2. every declared hand-off that fires
    for h in body.get("handoffs", []):
        if not _fires(h, outcome):
            continue
        reqs = grouped.get(h["id"], [])
        if h["cardinality"] == "one" and len(reqs) > 1:
            decisions.append(_refuse("cardinality",
                                     f"hand-off '{h['id']}' is `one` and received {len(reqs)} requests",
                                     handoff=h["id"]))
            continue
        found = resolve(h["to"])
        if found is None:
            decisions.append(_refuse("version",
                                     f"no ACTIVE version of '{h['to']['asop_id']}' matches "
                                     f"{h['to'].get('version') or h['to'].get('versions')}", handoff=h["id"]))
            continue
        version, target = found
        declared = {i["name"] for i in target.get("inputs", [])}

        if h["cardinality"] == "one":
            if reqs and "subject" in reqs[0]:
                # Ignoring it would be silent; the subject of a `one` hand-off
                # is the chain, and a run does not get to choose another.
                decisions.append(_refuse("subject",
                                         f"hand-off '{h['id']}' is `one`; its subject is the chain, so a "
                                         f"request may not name one", handoff=h["id"]))
                continue
            units = [(chain, None, reqs[0] if reqs else None)]
        else:
            try:
                pairs = expand_subjects(h, outputs)
            except (KeyError, ValueError) as e:
                decisions.append(_refuse("output", f"hand-off '{h['id']}': {e}", handoff=h["id"]))
                continue
            items = dict(pairs)
            if reqs:
                units = []
                for req in reqs:
                    sid = req.get("subject")
                    if sid is None or sid not in items:
                        decisions.append(_refuse("subject",
                                                 f"hand-off '{h['id']}': subject {sid!r} is not one "
                                                 f"'{h['subject_from']['output']}' yields", handoff=h["id"]))
                        continue
                    units.append((sid, items[sid], req))
            else:
                units = [(sid, item, None) for sid, item in pairs]

        for subject, item, req in units:
            try:
                computed = {k: _resolve_value(v, subject=subject, item=item, run_inputs=run_inputs, outputs=outputs)
                            for k, v in h["inputs"].items()}
            except KeyError as e:
                decisions.append(_refuse("inputs", f"hand-off '{h['id']}': {e.args[0]} has no value",
                                         handoff=h["id"], subject=subject))
                continue
            supplied = (req or {}).get("inputs", {})
            clash = sorted(k for k in supplied if k in computed and supplied[k] != computed[k])
            undeclared = sorted((set(computed) | set(supplied)) - declared)
            merged = {**supplied, **computed}
            missing = sorted(declared - set(merged))
            if clash or undeclared or missing:
                parts = []
                if clash:
                    parts.append(f"request overrides declared mapping for {', '.join(clash)}")
                if undeclared:
                    parts.append(f"'{h['to']['asop_id']}' does not declare {', '.join(undeclared)}")
                if missing:
                    parts.append(f"{', '.join(missing)} not supplied")
                decisions.append(_refuse("inputs", f"hand-off '{h['id']}': " + "; ".join(parts),
                                         handoff=h["id"], subject=subject))
                continue
            key = natural_key(chain, h["to"]["asop_id"], subject)
            if key in filed:
                decisions.append({"action": "duplicate", "handoff": h["id"], "natural_key": key})
                continue
            filed.add(key)
            decisions.append({"action": "file", "handoff": h["id"],
                              "to": {"asop_id": h["to"]["asop_id"], "version": version},
                              "subject": subject, "inputs": merged, "natural_key": key})
    return decisions


def _fires(h: dict, outcome: Optional[str]) -> bool:
    return "on_outcomes" not in h or outcome in h["on_outcomes"]


# ---------------------------------------------------------------------------
# scheduling
# ---------------------------------------------------------------------------

def requires_satisfied(requires: list[dict], *, runs: list[dict], fanned_out: Iterable[str] = (),
                       approved: Iterable[str] = ()) -> bool:
    """May a filed run start? All predicates must hold, within ONE chain.

    `runs` are the chain's runs as `{asop_id, subject, attempt, done: bool}`;
    only the latest attempt per (asop_id, subject) counts. `fanned_out` names
    the ASOPs some hand-off in this chain has already been processed toward
    (even when it filed nothing). `approved` names artefacts a declared human
    approved (§9 — the engine resolves the approver; this function does not).

    - `run_done(X)`: at least one run of X exists, and every one is done.
    - `all_runs_done(X)`: a hand-off toward X has been processed, and every run
      it filed is done. Zero runs filed satisfies it — an empty fan-out is a
      finished one, and a join that waits for runs that will never exist is a
      deadlock.
    - `human_approved(A)`: A is approved.
    """
    latest: dict[tuple[str, str], dict] = {}
    for r in runs:
        k = (r["asop_id"], str(r["subject"]))
        if k not in latest or r.get("attempt", 1) > latest[k].get("attempt", 1):
            latest[k] = r
    fanned, ok = set(fanned_out), set(approved)
    for req in requires:
        kind = req["kind"]
        if kind == "human_approved":
            if req["artefact"] not in ok:
                return False
            continue
        mine = [r for (a, _), r in latest.items() if a == req["asop_id"]]
        if kind == "run_done" and not mine:
            return False
        if kind == "all_runs_done" and req["asop_id"] not in fanned:
            return False
        if not all(r["done"] for r in mine):
            return False
    return True
