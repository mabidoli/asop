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
- the successor declares when it may START (`requires`) — joins and approvals
  belong to the procedure that waits, because it is the only one that sees
  every predecessor;
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
    "outcome": "the run's outcome is not one the ASOP declares, or not one the requested hand-off fires on",
    "cardinality": "a `one` hand-off received more than one request",
    "subject": "a request names a subject the hand-off's `subject_from` does not yield, or names one on a `one` hand-off",
    "output": "the output a hand-off reads is missing, or does not have the declared shape",
    "inputs": "the successor's declared inputs are not exactly satisfied by scalar values",
    "version": "no ACTIVE version of the successor falls inside the declared version range",
    "conflict": "a run with this natural key was already filed with different inputs",
}

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
    extra = set(raw) - {"output", "array", "id_field", "where", "where_not"}
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
    may not) — it is used to refuse direct self-reference. Longer cycles need
    the other records: `validate_handoff_targets`."""
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
            extra = set(h) - {"id", "to", "on_outcomes", "cardinality", "subject_from", "inputs"}
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
            if kind in ("run_done", "all_runs_done"):
                if set(r) != {"kind", "asop_id"}:
                    raise ChainContractError(f"{what}: '{kind}' takes exactly {{kind, asop_id}}.")
                ref = _ident(r["asop_id"], f"{what}.asop_id")
                if asop_id is not None and ref == asop_id:
                    raise ChainContractError(f"{what}: a procedure cannot wait on its own runs.")
                reqs.append({"kind": kind, "asop_id": ref})
            else:  # human_approved
                if set(r) != {"kind", "artefact"}:
                    raise ChainContractError(f"{what}: 'human_approved' takes exactly {{kind, artefact}}.")
                reqs.append({"kind": kind, "artefact": _ident(r["artefact"], f"{what}.artefact")})
        out["requires"] = reqs

    return out


# ---------------------------------------------------------------------------
# authoring: across records
# ---------------------------------------------------------------------------

Resolver = Callable[[dict], Optional[tuple[int, dict]]]
"""Given a validated `to` reference, return `(version, validated body)` of the
version that would be filed today — the ACTIVE version inside the pin or range —
or None if there is none. The store owns resolution; this module never guesses."""


def validate_handoff_targets(body: dict, resolve: Resolver, *, asop_id: Optional[str] = None) -> None:
    """Check a validated body against the records it names. Run at activation,
    when the targets are knowable; `plan_handoffs` repeats the input check at
    filing, because a target may have moved since.

    - every hand-off target resolves, and is handed exactly the inputs it
      declares — no more, no fewer. A run cannot fill a gap (it names subjects,
      never inputs), so a gap here is a filing that will always be refused;
    - an escalation target resolves, declares every input it is handed, and has
      a step a human role does — §5.4's second failure "escalates to a human".
      Its unmapped inputs are filled from the engine's escalation context;
    - the hand-off graph reachable from this record (hand-offs and escalations,
      through the resolver) has no cycle. With run-generated subjects, a cycle
      through a `many` hand-off has no bound."""
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
    _refuse_cycles(body, resolve, asop_id or "<this record>")


def _edges(body: dict) -> list[dict]:
    out = [h["to"] for h in body.get("handoffs", [])]
    if body.get("on_escalate"):
        out.append(body["on_escalate"]["to"])
    return out


def _refuse_cycles(body: dict, resolve: Resolver, start: str) -> None:
    path: list[str] = [start]
    done: set[str] = set()

    def walk(b: dict) -> None:
        for to in _edges(b):
            node = to["asop_id"]
            if node in path:
                cycle = " → ".join(path[path.index(node):] + [node])
                raise ChainContractError(
                    f"hand-offs form a cycle: {cycle}. A chain must end; a loop is a nested step or a "
                    f"repair (§5.4), and a cycle through a fan-out has no bound."
                )
            if node in done:
                continue
            found = resolve(to)
            if found is None:
                continue  # unresolvable downstream is that record's own activation problem
            path.append(node)
            walk(found[1])
            path.pop()
            done.add(node)

    walk(body)


def _resolve_or_raise(to: dict, what: str, resolve: Resolver) -> dict:
    found = resolve(to)
    if found is None:
        raise ChainContractError(
            f"{what}: no ACTIVE version of '{to['asop_id']}' matches {to.get('version') or to.get('versions')}."
        )
    return found[1]


def predecessors_of(bodies: Mapping[str, dict]) -> dict[str, set[str]]:
    """asop_id -> the asop_ids that can file it (by hand-off or escalation),
    from the validated bodies of the ACTIVE versions. What `requires_satisfied`
    needs to know that no more runs of an ASOP can still be filed."""
    out: dict[str, set[str]] = {}
    for src, b in bodies.items():
        for to in _edges(b):
            out.setdefault(to["asop_id"], set()).add(src)
    return out


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


def _refuse(rule: str, detail: str, **ctx) -> dict:
    return {"action": "refuse", "code": f"{HANDOFF_REFUSED}:{rule}", "detail": detail, **ctx}


def _file_or_dup(decisions: list, filed: dict, *, handoff: Optional[str], to: dict, version: int,
                 subject: str, inputs: dict, chain: str, attempt: int) -> None:
    key = natural_key(chain, to["asop_id"], subject, attempt)
    ctx = {"handoff": handoff} if handoff else {}
    if key in filed:
        prev = filed[key]
        if prev is not None and (set(prev) != set(inputs) or not all(_same(prev[k], inputs[k]) for k in inputs)):
            decisions.append(_refuse("conflict", f"{key} was already filed with different inputs",
                                     natural_key=key, **ctx))
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
                  existing: Any = (), attempt: int = 1) -> list[dict]:
    """What a finished run files. `body` is the filing ASOP's validated body.

    Returns decisions in a normative order — the run's outcome first, then each
    request in the order written, then each declared hand-off in declaration
    order, its subjects in output (or request) order:

    - `{"action": "file", "handoff", "to": {asop_id, version}, "subject", "inputs", "natural_key"}`
    - `{"action": "duplicate", "handoff", "natural_key"}` — already filed with
      the same inputs; nothing happens. Not a refusal: several runs handing off
      to one join is the normal case.
    - `{"action": "refuse", "code": "handoff_refused:<rule>", "detail", ...}` —
      recorded, never silently dropped, never repaired.

    Semantics (ASOP.md §3.7):
    - when the ASOP declares `outcomes`, a run whose outcome is not one of them
      files nothing and is refused (`outcome`): a closed set is closed;
    - a hand-off FIRES when the outcome is in its `on_outcomes` (or always,
      when it has none);
    - a fired `one` hand-off files one run whose subject is the chain;
    - a fired `many` hand-off files one run per subject `subject_from` yields,
      or — when the run wrote ACCEPTED requests for it — exactly those subjects;
    - a REFUSED request is as if it had not been written. It never narrows,
      cancels or alters what the ASOP declared;
    - `attempt` is the attempt being filed; the engine passes a higher one when
      it re-files downstream of a stale input. `existing` holds what was filed
      before (key -> inputs); the same key with different inputs is a
      `conflict`, refused.
    """
    decisions: list[dict] = []
    filed = _filed_map(existing)
    declared_outcomes = body.get("outcomes")
    if declared_outcomes and outcome not in declared_outcomes:
        return [_refuse("outcome", f"outcome {outcome!r} is not one this ASOP declares "
                                   f"({', '.join(declared_outcomes)}); nothing is filed")]
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

    # 2. every declared hand-off that fires
    for h in body.get("handoffs", []):
        if not _fires(h, outcome):
            continue
        reqs = grouped.get(h["id"], [])
        found = resolve(h["to"])
        if found is None:
            decisions.append(_refuse("version", f"no ACTIVE version of '{h['to']['asop_id']}' matches "
                                                f"{h['to'].get('version') or h['to'].get('versions')}",
                                     handoff=h["id"]))
            continue
        version, target = found
        declared = {i["name"] for i in target.get("inputs", [])}

        if h["cardinality"] == "one":
            if len(reqs) > 1:
                decisions.append(_refuse("cardinality", f"hand-off '{h['id']}' is `one` and received "
                                                        f"{len(reqs)} requests; all are disregarded",
                                         handoff=h["id"]))
            elif reqs and "subject" in reqs[0][1]:
                decisions.append(_refuse("subject", f"hand-off '{h['id']}' is `one`; its subject is the chain, "
                                                    f"so a request may not name one", handoff=h["id"],
                                         request=reqs[0][0]))
            units = [(chain, None)]
        else:
            try:
                pairs = expand_subjects(h, outputs)
            except (KeyError, ValueError) as e:
                decisions.append(_refuse("output", f"hand-off '{h['id']}': {e.args[0]}", handoff=h["id"]))
                continue
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

        for subject, item in units:
            try:
                inputs = {k: _resolve_value(v, subject=subject, item=item, run_inputs=run_inputs, outputs=outputs)
                          for k, v in h["inputs"].items()}
            except KeyError as e:
                decisions.append(_refuse("inputs", f"hand-off '{h['id']}': {e.args[0]}",
                                         handoff=h["id"], subject=subject))
                continue
            if set(inputs) != declared:
                decisions.append(_refuse("inputs", _input_gap(h["to"]["asop_id"], inputs, declared),
                                         handoff=h["id"], subject=subject))
                continue
            _file_or_dup(decisions, filed, handoff=h["id"], to=h["to"], version=version, subject=subject,
                         inputs=inputs, chain=chain, attempt=attempt)
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
    found = resolve(esc["to"])
    if found is None:
        return _refuse("version", f"no ACTIVE version of '{esc['to']['asop_id']}' matches "
                                  f"{esc['to'].get('version') or esc['to'].get('versions')}")
    version, target = found
    declared = {i["name"] for i in target.get("inputs", [])}
    try:
        mapped = {k: _resolve_value(v, subject=None, item=None, run_inputs=run_inputs, outputs={})
                  for k, v in esc["inputs"].items()}
    except KeyError as e:
        return _refuse("inputs", f"'on_escalate': {e.args[0]}")
    inputs = {k: context[k] for k in declared if k not in mapped and k in context}
    if not all(_is_scalar(v) for v in inputs.values()):
        return _refuse("inputs", "'on_escalate': the escalation context supplied a non-scalar value")
    inputs.update(mapped)
    if set(inputs) != declared:
        return _refuse("inputs", _input_gap(esc["to"]["asop_id"], inputs, declared))
    out: list[dict] = []
    _file_or_dup(out, _filed_map(existing), handoff=None, to=esc["to"], version=version,
                 subject=_ident(subject, "escalation subject"), inputs=inputs, chain=chain, attempt=attempt)
    return out[0]


# ---------------------------------------------------------------------------
# scheduling
# ---------------------------------------------------------------------------

def requires_satisfied(requires: list[dict], *, runs: Iterable[Mapping],
                       predecessors: Mapping[str, Iterable[str]] = (),
                       approved: Iterable[str] = ()) -> bool:
    """May a filed run start? Every predicate must hold, within ONE chain.

    `runs` are this chain's runs as `{asop_id, subject, attempt, done,
    processed, refused_toward?}`. Only the highest attempt per (asop_id,
    subject) counts. `processed` means its completion was evaluated and its
    hand-off decisions recorded — true also when nothing fired.
    `refused_toward` lists targets a refused hand-off of this run would have
    filed. `predecessors` is `predecessors_of(...)` over the active library.
    `approved` holds artefacts a declared human approved IN THIS CHAIN (§9 —
    the engine resolves the approver; this function does not).

    X is SETTLED when no further run of X can be filed: every run of every
    ASOP that can (transitively) file X is done and processed, and none has a
    refused hand-off toward X or toward one of those ASOPs. A refusal is a
    blocker for a person, never an empty result. An ancestor that never ran —
    because its own predecessor's outcome closed that branch — has no runs and
    blocks nothing.

    - `run_done(X)`: X is settled, at least one run of X exists, and all are done.
    - `all_runs_done(X)`: X is settled and all of its runs are done — zero runs
      included. An empty fan-out is a finished one; a join waiting for runs a
      filter guaranteed would never be filed is a deadlock.
    - `human_approved(A)`: A is approved in this chain.
    """
    latest: dict[tuple[str, str], Mapping] = {}
    for r in runs:
        k = (r["asop_id"], str(r["subject"]))
        if k not in latest or r.get("attempt", 1) > latest[k].get("attempt", 1):
            latest[k] = r
    preds = {k: set(v) for k, v in dict(predecessors).items()}
    ok = set(approved)

    def ancestors(x: str) -> set[str]:
        seen: set[str] = set()
        todo = list(preds.get(x, ()))
        while todo:
            a = todo.pop()
            if a not in seen:
                seen.add(a)
                todo.extend(preds.get(a, ()))
        return seen

    def settled(x: str) -> bool:
        up = ancestors(x)
        blocking = up | {x}
        for (a, _), r in latest.items():
            if a in up and not (r["done"] and r.get("processed", False)):
                return False
            if a in up and blocking & set(r.get("refused_toward") or ()):
                return False
        return True

    for req in requires:
        kind = req["kind"]
        if kind == "human_approved":
            if req["artefact"] not in ok:
                return False
            continue
        x = req["asop_id"]
        mine = [r for (a, _), r in latest.items() if a == x]
        if kind == "run_done" and not mine:
            return False
        if not settled(x) or not all(r["done"] for r in mine):
            return False
    return True
