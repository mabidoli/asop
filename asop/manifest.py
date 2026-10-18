"""The chain manifest — one pin for a whole chain (ASOP.md §3.10, v3.6).

v3.5 pins every run to `(asop_id, version)` and lets a hand-off name a version
RANGE, resolved at filing. In a chain that runs for hours that is two pins too
few. A long chain observed in practice moved under three independent clocks —
the procedures, the checker the deterministic gates call, and the engine's own
orchestration rules — and a gate's meaning is the checker that runs it as much
as the step text that names it. A chain filed on Monday and finished on
Thursday, with a range resolved at each filing, could run its first procedure
under one gate semantics and its last under another, and nothing in the record
would say so.

A manifest is the answer: at chain start, the engine resolves every procedure
the chain can reach ONCE, and pins it, together with the digests of whatever
else gives the gates their meaning (`components`: a checker, a rules file).
The chain resolves from the manifest for the rest of its life. Moving a
running chain to a different manifest is an ADOPTION: a person records it,
with an impact report computed from the two manifests, never typed.

Opt-in. An engine that starts chains without a manifest is the v3.5 engine it
always was, resolving ranges at filing; it does not get to claim it pins
chains (§3.10).

Every function is pure:

- `pin_manifest`       resolve once, at chain start
- `manifest_resolver`  a `Resolver` (asop.chain) that answers from the pin
- `manifest_digest`    the digest an adoption and an outcome row refer to
- `validate_manifest`  is this document a manifest?
- `adoption_impact`    what moving from one manifest to another changes
- `validate_adoption`  is this a recorded adoption of exactly that move?
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Iterable, Mapping, Optional

from asop.chain import ChainContractError, Resolver, _ident, _target, version_in

MANIFEST_SCHEMA = "asop.chain-manifest/1"
ADOPTION_SCHEMA = "asop.adoption/1"

#: The refusal an engine returns when a chain run would use a member version
#: or component its manifest does not pin. Moving needs a recorded adoption.
ADOPTION_REQUIRED = "adoption_required"

IMPACT_EFFECTS = ("unchanged", "changed", "added", "removed")

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


#: The fields of a stored ASOP record that the STORE assigns (ASOP.md §3.10,
#: v3.6.1). They change without the procedure changing — a status flip, a
#: superseding version — so they are not part of what a member digest covers.
STORE_FIELDS = ("asop_id", "version", "status", "author", "author_kind", "superseded_by", "created_at")


def canonical_json(value: Any) -> bytes:
    """The canonical serialization of ASOP.md §3.10 (normative since v3.6.1):

    - object keys sorted by Unicode CODE POINT (not UTF-16 code unit — this is
      where it departs from RFC 8785, for keys outside the BMP), no duplicates;
    - no whitespace anywhere: `,` between items, `:` between key and value;
    - strings as UTF-8, unescaped except `"`, `\\`, and the controls
      U+0000–U+001F (`\\b \\f \\n \\r \\t` short, the rest `\\u00xx`
      lowercase); no Unicode normalization — code points are hashed as given;
    - integers in decimal, no leading zeros, no `+`, no exponent; `true`,
      `false`, `null` as written. A float is refused: two languages print
      floats differently, and a digest is no place to find that out."""
    def check(v: Any) -> None:
        if isinstance(v, float):
            raise ChainContractError("a digested document may not contain a float; write it as a string.")
        if isinstance(v, Mapping):
            for k, x in v.items():
                if not isinstance(k, str):
                    raise ChainContractError("a digested document's keys are strings.")
                check(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                check(x)
    check(value)
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as e:  # a lone surrogate has no UTF-8 form
        raise ChainContractError("a digested document's strings must be valid Unicode (no lone surrogates).") from e


def digest_of(value: Any) -> str:
    """`sha256:` and the 64 lowercase hex digits of SHA-256 over
    `canonical_json(value)`."""
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def body_digest(record: Mapping, *, asop_id: Optional[str] = None) -> str:
    """A chain member's digest (ASOP.md §3.10): the digest of the record's
    VALIDATED body.

    1. drop the store-assigned fields (`STORE_FIELDS`) when present — a stored
       record and the body it was authored from digest the same;
    2. normalise the rest with `validate_asop` (which refuses an invalid body,
       `SopContractError`); absent and `null` optional fields are the same
       thing there, and the normalised gate carries every gate field, `null`
       where unset;
    3. `digest_of` the result.

    Normalisation is idempotent, so the normalised body digests the same as
    the document it came from. `asop_id` defaults to the record's own."""
    from asop.sop import validate_asop
    if not isinstance(record, Mapping):
        raise ChainContractError("a member digest is computed over an ASOP record (a mapping).")
    body = {k: v for k, v in record.items() if k not in STORE_FIELDS}
    return digest_of(validate_asop(body, asop_id=asop_id if asop_id is not None else record.get("asop_id")))


def manifest_digest(manifest: Mapping) -> str:
    """The digest of a validated manifest — the identity an adoption, a
    journal and a chain outcome row refer to."""
    return digest_of(validate_manifest(manifest))


def _digest(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ChainContractError(f"{what} must be 'sha256:' and 64 lowercase hex digits, got {value!r}.")
    return value


def _components(raw: Any) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, (str, dict)) or not isinstance(raw, (list, tuple)):
        raise ChainContractError("'components' must be a LIST of {name, version?, digest}.")
    out: list[dict] = []
    for i, c in enumerate(raw):
        what = f"'components'[{i}]"
        if not isinstance(c, Mapping) or set(c) - {"name", "version", "digest"}:
            raise ChainContractError(f"{what} must be {{name, version?, digest}}.")
        clean = {"name": _ident(c.get("name"), f"{what}.name"), "digest": _digest(c.get("digest"), f"{what}.digest")}
        if c.get("version") is not None:
            # A string, so `29` and `"29"` cannot be two spellings of one pin.
            clean["version"] = _ident(c["version"], f"{what}.version")
        if any(x["name"] == clean["name"] for x in out):
            raise ChainContractError(f"'components' names '{clean['name']}' twice.")
        out.append(clean)
    return sorted(out, key=lambda c: c["name"])


def validate_manifest(doc: Any) -> dict:
    """`asop.chain-manifest/1`:
    `{schema, chain, start: {asop_id, version}, members: [{asop_id, version, digest}], components?}`.
    One version per procedure; members and components sorted, so a manifest
    has one spelling and one digest. Raises `ChainContractError`."""
    if not isinstance(doc, Mapping):
        raise ChainContractError("a chain manifest must be a mapping.")
    if doc.get("schema") != MANIFEST_SCHEMA:
        raise ChainContractError(f"a chain manifest declares schema '{MANIFEST_SCHEMA}', got {doc.get('schema')!r}.")
    extra = set(doc) - {"schema", "chain", "start", "members", "components"}
    if extra:
        raise ChainContractError(f"chain manifest: unknown field(s) {', '.join(sorted(map(str, extra)))}.")
    start = doc.get("start")
    if not isinstance(start, Mapping) or set(start) != {"asop_id", "version"}:
        raise ChainContractError("chain manifest 'start' must be {asop_id, version}: the run that started the chain.")
    start = _target(dict(start), "chain manifest 'start'")
    raw = doc.get("members")
    if isinstance(raw, (str, dict)) or not isinstance(raw, (list, tuple)) or not raw:
        raise ChainContractError("chain manifest 'members' must be a non-empty LIST.")
    members: list[dict] = []
    for i, m in enumerate(raw):
        what = f"'members'[{i}]"
        if not isinstance(m, Mapping) or set(m) != {"asop_id", "version", "digest"}:
            raise ChainContractError(f"{what} must be exactly {{asop_id, version, digest}}.")
        pin = _target({"asop_id": m["asop_id"], "version": m["version"]}, what)
        if any(x["asop_id"] == pin["asop_id"] for x in members):
            raise ChainContractError(
                f"chain manifest pins '{pin['asop_id']}' twice. A chain runs ONE version of each procedure."
            )
        members.append({**pin, "digest": _digest(m["digest"], f"{what}.digest")})
    if not any(m["asop_id"] == start["asop_id"] and m["version"] == start["version"] for m in members):
        raise ChainContractError("chain manifest 'start' is not one of its members.")
    return {"schema": MANIFEST_SCHEMA, "chain": _ident(doc.get("chain"), "chain manifest 'chain'"),
            "start": start, "members": sorted(members, key=lambda m: m["asop_id"]),
            "components": _components(doc.get("components"))}


def _edges(body: Mapping) -> list[dict]:
    """Every procedure a run of `body` can cause to be filed: hand-offs (joins
    included), its escalation target, and the upstream owners its rework can
    re-file. `uses` is not here: a nested step already pins an exact version
    in the record, and the record's digest covers it."""
    refs = [h["to"] for h in body.get("handoffs", [])]
    if body.get("on_escalate"):
        refs.append(body["on_escalate"]["to"])
    refs += [e["to"] for e in body.get("rework", []) if "asop_id" in e["to"]]
    return refs


def pin_manifest(asop_id: str, version: int, body: Mapping, resolve: Resolver, *, chain: str,
                 components: Iterable[Mapping] = (),
                 digest: Callable[[Mapping], str] = body_digest) -> dict:
    """Resolve, once, every procedure a chain started from `(asop_id, version)`
    can reach, and pin it (§3.10). `resolve` is the store's resolver AT CHAIN
    START; `body` is the starting record's validated body. `digest` computes a
    member's digest from its validated body; the default is `body_digest`,
    which is the normative member digest (§3.10). Because normalisation is
    idempotent, it equals `digest_of` of an already-validated body.

    Strict, because pinning is the point: a reference that resolves to nothing,
    or two references that would need two versions of one procedure, refuse
    the chain at its start, instead of a filing hours later."""
    pins: dict[str, tuple[int, Mapping]] = {asop_id: (version, body)}
    queue = [body]
    while queue:
        b = queue.pop()
        for ref in _edges(b):
            node = ref["asop_id"]
            if node in pins:
                if not version_in(ref, pins[node][0]):
                    raise ChainContractError(
                        f"the chain already pins '{node}' at v{pins[node][0]}, and a reference to it asks for "
                        f"{ref.get('version') or ref.get('versions')}. A chain runs one version of each procedure."
                    )
                continue
            found = resolve(ref)
            if found is None:
                raise ChainContractError(
                    f"cannot pin '{node}': no ACTIVE version matches {ref.get('version') or ref.get('versions')}. "
                    f"A chain whose members cannot all be pinned is refused at its start."
                )
            pins[node] = found
            queue.append(found[1])
    members = [{"asop_id": a, "version": v, "digest": digest(b)} for a, (v, b) in pins.items()]
    return validate_manifest({"schema": MANIFEST_SCHEMA, "chain": chain,
                              "start": {"asop_id": asop_id, "version": version},
                              "members": members, "components": list(components)})


def manifest_resolver(manifest: Mapping, fetch: Callable[[str, int], Optional[Mapping]]) -> Resolver:
    """A `Resolver` that answers from the manifest. `fetch(asop_id, version)`
    returns the validated body of that exact version from the store (any
    status: a superseded version a running chain pins must stay resolvable,
    §4). A reference the pinned version does not satisfy, or a procedure the
    manifest does not pin, resolves to None — so filing refuses it
    (`handoff_refused:version`) instead of reaching for the active version."""
    pins = {m["asop_id"]: m["version"] for m in validate_manifest(manifest)["members"]}

    def resolve(ref: dict) -> Optional[tuple[int, dict]]:
        v = pins.get(ref["asop_id"])
        if v is None or not version_in(ref, v):
            return None
        found = fetch(ref["asop_id"], v)
        return None if found is None else (v, dict(found))
    return resolve


def is_pinned(manifest: Mapping, asop_id: str, version: int) -> bool:
    """Is `(asop_id, version)` what this chain pins? An engine refuses
    `adoption_required` before it files, runs or re-files anything else."""
    return any(m["asop_id"] == asop_id and m["version"] == version
               for m in validate_manifest(manifest)["members"])


def adoption_impact(old: Mapping, new: Mapping) -> list[dict]:
    """What moving a chain from `old` to `new` changes, one row per procedure
    and per component, sorted: `{kind: member|component, name, from, to,
    effect}`, effect one of `IMPACT_EFFECTS`. Computed, never typed: an impact
    report a person writes by hand is the report that misses the checker."""
    a, b = validate_manifest(old), validate_manifest(new)
    rows: list[dict] = []
    for kind, key, xs, ys in (("member", "asop_id", a["members"], b["members"]),
                              ("component", "name", a["components"], b["components"])):
        before = {x[key]: x for x in xs}
        after = {y[key]: y for y in ys}
        for name in sorted(set(before) | set(after)):
            x, y = before.get(name), after.get(name)
            pick = (lambda r: None if r is None else {k: r[k] for k in ("version", "digest") if k in r})
            effect = ("added" if x is None else "removed" if y is None
                      else "unchanged" if pick(x) == pick(y) else "changed")
            rows.append({"kind": kind, "name": name, "from": pick(x), "to": pick(y), "effect": effect})
    return rows


def validate_adoption(doc: Any, *, old: Mapping, new: Mapping) -> dict:
    """`asop.adoption/1`: `{schema, chain, from, to, adopted_by, adopted_at, impact}`.

    The record is accepted only as an adoption of exactly this move: `from`
    and `to` are the two manifests' digests, `impact` is `adoption_impact(old,
    new)`, and both manifests belong to `chain`. `adopted_by` must be a
    declared human (§6.4) — resolving that registry is the integrator's, as
    for every other identity in this contract (§9); this validator checks the
    record, not the person."""
    if not isinstance(doc, Mapping):
        raise ChainContractError("an adoption must be a mapping.")
    if doc.get("schema") != ADOPTION_SCHEMA:
        raise ChainContractError(f"an adoption declares schema '{ADOPTION_SCHEMA}', got {doc.get('schema')!r}.")
    extra = set(doc) - {"schema", "chain", "from", "to", "adopted_by", "adopted_at", "impact", "reason"}
    if extra:
        raise ChainContractError(f"adoption: unknown field(s) {', '.join(sorted(map(str, extra)))}.")
    a, b = validate_manifest(old), validate_manifest(new)
    chain = _ident(doc.get("chain"), "adoption 'chain'")
    if a["chain"] != chain or b["chain"] != chain:
        raise ChainContractError("an adoption moves ONE chain: both manifests must name the adoption's chain.")
    if doc.get("from") != digest_of(a):
        raise ChainContractError("adoption 'from' is not the digest of the manifest the chain runs under.")
    if doc.get("to") != digest_of(b):
        raise ChainContractError("adoption 'to' is not the digest of the manifest it adopts.")
    if doc["from"] == doc["to"]:
        raise ChainContractError("an adoption that changes nothing is not an adoption.")
    if doc.get("impact") != adoption_impact(a, b):
        raise ChainContractError(
            "adoption 'impact' is not the computed impact of this move. Record what adoption_impact "
            "returns: a hand-written report is the one that leaves out the component that changed."
        )
    out = {"schema": ADOPTION_SCHEMA, "chain": chain, "from": doc["from"], "to": doc["to"],
           "adopted_by": _ident(doc.get("adopted_by"), "adoption 'adopted_by'"),
           "adopted_at": _ident(doc.get("adopted_at"), "adoption 'adopted_at'"),
           "impact": doc["impact"]}
    if doc.get("reason") is not None:
        out["reason"] = _ident(doc["reason"].strip() if isinstance(doc["reason"], str) else doc["reason"],
                               "adoption 'reason'")
    return out
