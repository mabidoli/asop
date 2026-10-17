# asop

**The ASOP contract — a versioned, verified, self-revising procedure standard for agentic work.**

This repository is the normative specification ([`ASOP.md`](ASOP.md)) and its
reference implementation. It is deliberately standalone: it depends on nothing,
and nothing in it imports a coordination plane or a runtime.

Two implementations speak it today — the
[AgentCo Hub](https://github.com/agentic-co/agentic-co-hub) (coordination) and the
[AgentCo Harness](https://github.com/agentic-co/agentic-co-harness) (execution).
They are independent of each other, and both are mine: the standard has not yet
been implemented by anyone who did not also write it, which is the honest status
and the thing I would most like to change.

What exists to make that possible: a normative [schema](schema/v1/) as JSON
Schema in YAML, and [229 conformance vectors](conformance/) as data, so an
implementation in any language can prove it conforms without depending on this
package or reading a line of Python.


**The ASOP contract, as code.** A procedure that is versioned, verified, and
self-revising is only useful if two independently-owned pieces of software —
a coordination plane and the harness actually running the work — agree on
what a gate, an attestation, and a procedure record *are*, without either
importing the other. This package is that agreement.

## Who imports this

- The [AgentCo Hub](https://github.com/agentic-co/agentic-co-hub) (`agentco/gates.py`,
  `agentco/errors.py`, `agentco/sop.py` and `agentco/policy.py` are thin shims
  over this package, keeping every existing caller and test unchanged).
- The [AgentCo Harness](https://github.com/agentic-co/agentic-co-harness), the
  standalone execution runtime, from its P2 (adopting this schema in place
  of its own `VERIFY_KEYS`/`validate_verify`).
- Anyone else building a plane or a harness that wants to speak the same
  gate shape as either, without depending on the code that runs either one.

## What's here

| Module | Contract |
|---|---|
| `asop.errors` | `Refusal` — the one exception type: a stable machine `code`, a human `message`, and a `remediation` sentence. Every refusal in this package (and, by re-export, in the plane) is one of these. |
| `asop.gates` | The unified gate schema. `validate_gate(payload, *, require=(), max_park_seconds_ceiling=None)` normalises a `deterministic` / `judged` / `human` gate — merging the plane's park-clock fields with the Harness's staged-check and runtime-hint fields into one shape — or refuses. `validate_attestation`, `attestation_passes`, `retry_decision` cover the evidence side. |
| `asop.sop` | The SOP **record** contract: `SopStatus`, the `SOP` and `ASOP` dataclasses, `validate_fields` / `validate_asop` / `validate_step`. Not the store — keeping versions, locking a file, and filing runs from a template is plane- or harness-side machinery, layered on top of this record shape. |
| `asop.revision` | The **revision policy** of [`ASOP.md` §6.4](ASOP.md): the four rules an agent reviser is bound by — protected tags, the human ratchet (absolute on a first activation), no-undoing-a-human, and the `human_only` verbs — plus the four operator declarations they read (`ASOP_HUMANS`, `ASOP_PROTECTED_TAGS`, `ASOP_VERIFIERS`, `ASOP_ADJUDICATORS`), each still falling back to its pre-split `AGENTCO_*` name for the two implementations that predate the split; whether that fallback is a permanent courtesy or gets a deprecation date is still open. Shared rather than plane-side because a rule that held on the plane and not in the runtime executing its work would be a rule with a door beside it. Which verb calls which rule stays each side's own wiring. |
| `asop.chain` | Hand-offs between procedures ([`ASOP.md` §3.7](ASOP.md)): validating a record's chaining, the filing decision, joins, `requires`, escalation — and from v3.6 ordered and approved fan-outs, entry gates (§3.8) and rework edges (§3.9). Every function is pure. |
| `asop.manifest` | *(v3.6)* The chain manifest (§3.10): pin every reachable procedure once at chain start, resolve from the pin, and move a chain only by a recorded adoption with a computed impact report. |
| `asop.journal` | *(v3.6)* Per-step journaling and resume (§5.6), what §5.4's budget charges, the staleness rule (§5.7), and the chain outcome record (§5.8). |
| `asop.refusals` | The refusal-code vocabulary — every machine-readable `code` a `Refusal` carries across the reference plane, named once with a one-line meaning, for a reader scanning codes rather than call sites. |

## The gate schema

One canonical field decides the shape: `kind` (`deterministic \| judged \|
human`). `class` is accepted as a read alias — the Harness's historical
name for the same field — and normalised output always uses `kind`.
Sending both, disagreeing, is refused.

Three field groups:

- **core** (always required) — `kind`, and exactly one of `check` (one
  command) or `checks` (an ordered, staged list; a one-element `checks`
  normalises into `check`).
- **clock** (the plane's park clock) — `max_park_seconds`, `on_timeout`,
  `escalate_to` (only with `on_timeout: escalate`), `verifier` (required
  for `human`, refused for `deterministic`, optional for `judged`).
  Declared as a whole or not at all — a gate carrying some of these fields
  but not both `max_park_seconds` and `on_timeout` is always refused,
  regardless of whether a caller requires the clock at all.
- **execution** (the Harness's runtime hints) — `cwd`, `timeout_s`,
  `rubric` (`judged` only), `judge_route` (`judged` only), and from v3.6
  `cannot_evaluate_exit` and `min_cases` (`deterministic` only): the third
  outcome, `cannot_evaluate`, which never passes and is never charged to the
  executor. `gate_outcome` says which outcome evidence reaches.

From v3.6 the clock group also takes `on_timeout: proceed_on_default` with a
required `default: {option, consequence, audience}`, on `human` gates only.

Whether the clock group is mandatory is the one thing that differs by
caller, and the one knob `validate_gate` exposes: `require=("clock",)` is
the plane's convention today; `require=()` is a caller with no park-clock
concept, such as the Harness before it adopts one. The 30-day park-clock
ceiling is **not** a package constant — it is the plane's own decision
about its own registry, passed in as `max_park_seconds_ceiling`. A caller
with no ceiling passes `None`.

Output is always fully normalised: every known field present (`None` where
absent), plus `"schema_version": 1`.

## Versioning

The specification is at **v3.6** and the package at **0.6.0**. v3.6 is additive: an
adopter at 0.5.0 needs to change nothing, and [`MIGRATION.md`](MIGRATION.md) says what
to adopt first and what was left for v4.

`asop.SCHEMA_VERSION` covers the gate schema's normalised shape. It bumps
only on a breaking change — a new required field, a renamed one, a changed
type. An additive optional field or a new refusal code does not bump it,
because existing readers already tolerate both. See `asop/__init__.py`.

## Install

    pip install asop-spec      # or: uv add asop-spec

The distribution is `asop-spec`; the import is `asop`:

    from asop import validate_gate, Refusal

(The bare `asop` name on PyPI was taken by an unrelated placeholder before this
package existed.)

**Standard library only, and that is a rule rather than a coincidence.** This
package is imported by every side of the contract, so a dependency taken here is
forced on everyone who speaks it. A standard that drags a dependency tree behind
it is a framework wearing a standard's clothes.

## Testing

    uv run --extra dev pytest -q                                         # 353 tests
    uv run --with pyyaml python conformance/runner/python.py --verbose   # 229 vectors

`pytest` is a dev dependency, not a default one — a bare `uv run pytest` fails with
"Failed to spawn: `pytest`" because nothing is installed to spawn; `--extra dev` is
what pulls it in. The conformance runner takes PyYAML the same way, inline rather
than as a dependency, for the reason given above: `asop` itself has none. See
[`conformance/README.md`](conformance/README.md) for what a vector is, how counts
are recomputed rather than typed by hand, and what the suite does not yet prove.

## Licence

[Apache License 2.0](LICENSE). The patent grant is the point: this package is the
contract other people's harnesses are asked to depend on, and a contract adopted
across organisations needs the grant stated in the file rather than implied.

Copyright 2026 Marcelo Bidoli Fernandes and the ASOP Authors.
