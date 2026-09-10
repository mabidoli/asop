# Conformance

An implementation is **conforming** when it passes every vector in
[`vectors/`](vectors/) against its own validators.

## What a vector is

A document and the outcome a conforming implementation must reach. Nothing else.

```yaml
- id: gate-refuse-human-without-verifier
  asserts: [A007]
  document: {kind: human}
  expect: refuse
  refusal: gate_invalid
  because: "a human gate nobody is named on is a gate nobody answers"
```

`asserts` cites [`assertions.yaml`](assertions.yaml) — the 107 normative rules
extracted from the specification — so a rule with no vector is visibly untested
rather than quietly untested.

## Claiming conformance

Read the YAML, run each document through your validator, compare. That is the
whole obligation: no dependency on `asop-spec`, no Python, no build step. The
reference runner in [`runner/python.py`](runner/python.py) is ~100 lines and
exists to show the shape, not to be depended on.

    python conformance/runner/python.py --verbose

Exit 0 when every vector matches, 1 otherwise, so CI can gate on it.

## The three questions

| file | asks |
|---|---|
| `vectors/gate.yaml` | is this gate well formed? |
| `vectors/attestation.yaml` | is this evidence admissible for that gate? |
| `vectors/satisfaction.yaml` | given that evidence, **may the step be recorded as done?** |

The third exists because the first two cannot answer it. A failing attestation is
a *valid document* — refusing it would leave failure unrecordable — so an
implementation that conflates "the evidence parsed" with "the gate passed" marks
a red test run as a green deployment and still passes every accept-or-refuse
vector. That gap was found by adversarial review on 2026-09-08, after the suite
already existed and was already green.

## What this suite does NOT yet prove

Stated here because a suite that hides its own coverage is worse than a small one.

- **No vectors for ASOP records, steps, or the revision policy.** Roughly a third
  of the standard's surface. The record shape, role bindings, separation-of-duties
  constraints, the human ratchet and protected tags are all unchecked.
- **Normalised output is not compared.** How an implementation shapes what it
  returns — nulls for absent fields, a stamped schema version — is storage
  convention rather than contract, and pinning it would make the suite a stricter
  opinion than the standard. The cost is that two conforming implementations may
  return different shapes for the same input.
- **A pattern-matcher could still pass.** With 39 vectors, an implementation that
  recognises these specific documents rather than implementing the rules would
  score full marks. Commands are varied across vectors to make that less
  comfortable, but the real answer is more vectors, and the honest statement is
  that passing proves the rules exercised here and nothing beyond them.
- **Only refusal codes are compared, not messages.** Two implementations may
  refuse the same document for different stated reasons.
- **8 of the 107 assertions are cited by a vector.** The other 99 are all
  `testable: true`. 88 of them need artifact types the runner does not have yet —
  an `asop` record, a `step`, a `revision` changeset — so the honest number for
  what is coverable today is smaller than the gap looks.
- **21 refusal sites in the reference implementation are unreachable by any
  vector**, including the `class` read alias, `checks: []`, `checks` as a bare
  string, `schema_version` mismatch, and every `on_timeout` value error.
- **2 refusal sites cannot be reached by the vector FORMAT at all**: the caller's
  `require=("clock",)` option and the park-clock ceiling have no field to express
  them. That is a format gap, not a coverage gap.

## `reaches` — pinning which rule fired

One refusal code covers many rules; `gate_invalid` alone guards 38 of them. So
comparing codes cannot tell whether a vector reached the rule its `because`
claims. Two vectors were found green while testing a different rule entirely, and
nothing in the suite could have caught it.

An optional `reaches` field names a substring the refusal message must contain:

```yaml
  - id: gate-refuse-verifier-on-deterministic
    document: {kind: deterministic, check: "pytest -q", max_park_seconds: 3600, on_timeout: pass, verifier: "dana"}
    expect: refuse
    refusal: gate_invalid
    reaches: "verifier is set on a deterministic gate"
```

**It is non-normative.** A conforming implementation is not required to match
these strings — its wording is its own, and demanding otherwise would make English
part of the contract. The reference runner checks them because it is the
implementation whose messages they quote, and because a vector that silently
drifts onto a different rule is worse than a missing one.

## Adding a vector

Write it, then run it. A vector that has never been executed against a real
implementation is the same unfalsifiable claim this suite replaced — the
specification asserted conformance against a suite that did not exist for
several months, which is how this file came to be written.


## v3.4 — ownership (§7.2)

`vectors/ownership.yaml` states the five outcomes an implementation must reach for the
ownership decision itself. They are **document-level vectors**: they say what must be
accepted or refused, not how a store proves it owns something.

Not yet covered, and listed here rather than left for an adopter to discover — a suite
that hides its own coverage is what this file exists to prevent:

- routing fail-open (§7.3): no vector exercises an unreachable router, because
  reachability is not a property of a document.
- mode transitions (§7.1): a vector cannot observe that a mode was declared rather than
  inferred; only an implementation's own tests can.
- version identity (§7.4): NO vector. The "counts it separately" rule is prose only, and
  an earlier draft of this file claimed otherwise — a suite describing coverage it does
  not have is worse than one admitting a gap, which is the whole reason this file exists.
- journal application (§7.2): NO vector. Idempotency, conflicting deliveries and outcome
  counting are all prose.
- the guarantee vs the primitive: the vectors exercise `may_flip`, which is the ownership
  DECISION. They cannot show that decision is actually consulted at the atomic bead-status
  transition, after the lease, gate, pin and terminal-state checks. That integration is
  where the guarantee either holds or does not, and only an implementation's own tests
  reach it.
