# Changelog

Version history for the ASOP specification. The specification itself is
[`ASOP.md`](ASOP.md); this file is where it has been, so the specification can be
about what it is.

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

## Unreleased

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
