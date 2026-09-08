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

`asserts` cites [`assertions.yaml`](assertions.yaml) — the 102 normative rules
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
- **A pattern-matcher could still pass.** With 37 vectors, an implementation that
  recognises these specific documents rather than implementing the rules would
  score full marks. Commands are varied across vectors to make that less
  comfortable, but the real answer is more vectors, and the honest statement is
  that passing proves the rules exercised here and nothing beyond them.
- **Only refusal codes are compared, not messages.** Two implementations may
  refuse the same document for different stated reasons.

## Adding a vector

Write it, then run it. A vector that has never been executed against a real
implementation is the same unfalsifiable claim this suite replaced — the
specification asserted conformance against a suite that did not exist for
several months, which is how this file came to be written.
