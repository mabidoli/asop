# Changelog

Version history for the ASOP specification. The specification itself is
[`ASOP.md`](ASOP.md); this file is where it has been, so the specification can be
about what it is.

## Unreleased

_Nothing yet._

## v3.2 — 2026-09-09

A concept-level cross-validation, not an implementation review: three independent builds
of this contract in one week — a Python harness, the Python plane, and a .NET
implementation at FrontSteps — each separately shipped the same two gaps. That is a
stronger signal than any one code review, so it goes in the specification rather than
one team's bug tracker. v3's seven decisions (§11) stand unchanged, no ASOP/Step record
shape moved, and `ASOP_VERSION` stays at `3`.

- **A claimed identity is now authenticated, not merely compared.** §5.3, §6.1 and §9 now
  say explicitly that a `judged`/`human` attestation's `submitted_by`, and an
  adjudication's adjudicator, must resolve against the operator's declared registry
  before being accepted — not just differ from the executor's name. All three
  implementations had shipped "differs from the executor" as the entire check; two were
  caught only after the fact. The new refusal is `unauthenticated` (§10); the verb tables
  in §8.3/§8.4 are updated to return it.
- **An attestation now carries a verdict, not just a name.** §5.3 requires a `judged` or
  `human` attestation to say which part of the gate's `check` (or which rung of a
  `checks` ladder) was found true or false. An identity and a timestamp with no verdict
  is `attestation_invalid` — it shows a party was named, not that a party looked. One of
  the three implementations had already documented this exact distinction, in a real
  procedure, in its own author's words, hours before an independent model review said the
  same thing about a second implementation.

### Distribution

`asop-spec` 0.3.0 — minor rather than patch. An attestation that used to pass with a bare
name now needs a resolvable identity and a verdict; anyone who had accepted the former
will see new refusals.

### Still open

`schema/v1/attestation.yaml` and the conformance vectors do not yet encode `verdict` or
`unauthenticated` — this release states the rule in prose; making it machine-checkable is
follow-up work, tracked separately rather than silently assumed done. The §3.5 `uses`
erratum from v3.1 is also still open.

## v3.1 — 2026-09-08

Corrections and a conformance layer. v3's seven decisions (§11 of the
specification) stand unchanged, no record shape moved, and `ASOP_VERSION` stays
at `3` — anything that reads a v3 record reads a v3.1 record.

- The gate on a staged `checks` ladder takes one attestation per rung, pinned to
  its index, and is answered only when every rung is present and passing. Added
  because the feature was previously unusable: a staged gate normalised to
  `check: None` and refused every possible attestation.
- The park clock is optional, and the prose now says so. It had been stated as an
  absolute while every implementation treated it as optional — two implementers,
  one reading each, would have disagreed about whether the same gate was valid.
- `ASOP_HUMANS` / `ASOP_PROTECTED_TAGS` replace `AGENTCO_*`, which are read as a
  deprecated fallback. A standard should not ask an adopter to set an environment
  variable named after somebody's company.
- A normative [schema](schema/v1/) and [39 conformance vectors](conformance/),
  so conformance is a thing an implementation can demonstrate rather than assert.
- 107 normative assertions extracted from the prose, each citing the section it
  comes from, so a rule with no vector is visibly untested rather than quietly
  untested. 8 are exercised by a vector today; the rest, and the reasons, are
  listed in [`conformance/README.md`](conformance/README.md) rather than left for
  an adopter to find out.

### Distribution

`asop-spec` 0.2.0 — minor rather than patch. A staged gate that refused every
possible attestation now accepts them, which changes validation outcomes for
anyone who had declared one.

### Still open

The §3.5 `uses` erratum. The prose says the outer step's gate, if any, applies
to the inner run as a whole; the record contract refuses any body on a `uses`
step, and `schema/v1/step.yaml` follows the stricter contract. Two readings of
one rule is the thing this document exists to prevent, so it needs a prose
decision before the schema's v1 is called final.

## v3 — 2026-09-04

### What changed from v2, and why

v2 defined an ASOP as one procedure record with prose fields and a gate "declared at
authoring time." Two things turned out to be true of the shipped implementation that
the text did not say:

1. **One ASOP filed one work item.** A multi-step task was a chain of separate ASOPs
   linked by `next_sop` — a reading aid, not an executable structure. Nothing walked
   the chain; the steps could not be gated, attested or revised individually inside
   one versioned artefact.
2. **The gate was not on the procedure.** The record had no gate field; whoever filed
   work supplied the gate at filing time. Where the filer is on the executor's side —
   the ordinary case for a single-operator organisation — the executor's side authored
   its own gate, which is the failure mode the contract exists to prevent.

v3 fixes both by changing the grain: **the ASOP is the sequence; the step is what v2
called the procedure.** Everything v2 got right — the three properties, adjudication,
the revision policy, the enforcement model, the decomposition bounds — carries forward
unchanged and now applies per step.

---

### Migration note for existing implementations

**v3 (2026-09-04).** The record contract now carries `ASOP` — a versioned,
ordered sequence of `Step`s for one type of task, with the gate on the step
— alongside the legacy single-record `SOP`, which stays importable only until
the plane's store migrates and is then deleted. `validate_asop` /
`validate_step` are the v3 entry points; `validate_fields` is v2's. The
definition, verbs and the seven decisions behind v3 are in
[`ASOP.md`](ASOP.md).
