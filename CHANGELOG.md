# Changelog

Version history for the ASOP specification. The specification itself is
[`ASOP.md`](ASOP.md); this file is where it has been, so the specification can be
about what it is.

## v3.4 — 2026-09-10

### The standalone store may be an embedded plane, and a run has one owner

Two implementations of this contract drifted apart because each wrote its own lifecycle:
two staged-gate implementations that could not consume each other's evidence, two registry
readers, two answers to whether an unclaimed bead may be approved. §7 now says what it
always meant — "a local ASOP store with the same contract" is a requirement about
BEHAVIOUR, not an instruction to write a second implementation of it — and a harness MAY
satisfy it by embedding a plane in-process.

- **§7.1** Filing destination is a declared MODE — `local-only`, `remote-owned`,
  `local-owned` — never inferred from whether a remote answered. §11.8's "the plane owns
  the queue" is the `remote-owned` mode, unchanged and now named as such.
- **§7.2** One run has exactly one owning store, fixed at filing, moved by no verb. Only
  the owner flips its beads; anyone else is refused **`not_the_owner`** — a different
  question from `not_the_holder`, which is about a lease. A non-owning plane receives a
  **journal**: it may read, count, run the lessons pass and route gates, and may not
  complete. Journal entries carry pinned step text, plan-vs-actual, divergence and
  adjudication, and application is idempotent on run identity.
- **§7.3** A plane may route a gate it does not own and may not answer it: the verdict
  returns as an attestation and the owner re-checks before flipping. Routing fails open to
  the owner, bounded by §9's expiry-is-failure-never-a-hang, and the duplicate a partition
  can cause is made harmless rather than prevented — **two prompts, never two
  completions**. A lock across a partition is what a partition denies.
- **§7.4** A journalled run's pin means the OWNING store's ASOP. An unresolvable version
  is counted separately, never folded in.
- **§11.9** Whether an ASOP record replicates downward is OPEN. Both reviewers called it
  non-blocking; the count-separately rule prevents the unsafe outcome meanwhile.

### Distribution

`asop-spec` **0.4.0**. New public API: `may_flip` and the `not_the_owner` code. 0.3.0
(v3.2 + v3.3) stands as its own release.

### Still open

Mode persistence and immutability are stated as rules, not mechanised — no vector can see
that a mode was declared rather than inferred. The journal's idempotency key, conflict
behaviour and transaction boundary are prose. `may_flip` is the ownership DECISION, and
the vectors cannot show it is consulted at the atomic status transition, which is where
the guarantee holds or does not. All three are named in
[`conformance/README.md`](conformance/README.md) rather than left to be discovered.

## v3.3 — 2026-09-10

### The registries have names

v3.2 required a claimed verifier or adjudicator to resolve against "the operator's
declared registry" and never said what that registry is called — the same
name-the-invariant-but-not-the-mechanism gap it was written to close, one level up.
Three implementations read that sentence; three invented their own answer.

- `ASOP_VERIFIERS` and `ASOP_ADJUDICATORS`, alongside the `ASOP_HUMANS` /
  `ASOP_PROTECTED_TAGS` that v3.1 named. Same comma-separated format, same
  exact-spelling match, so an operator declaring who may verify does not learn a
  second syntax to declare who may judge. No legacy fallback: there is no legacy name.
- `asop.revision.verifiers_from_env`, `adjudicators_from_env`, and
  `resolves(actor, registry)` — the rule in one place, so two implementations reading
  the same declaration cannot disagree about what it says. Unset and empty both
  resolve nobody; `None` never resolves.
- §9 now separates the two questions that were being conflated: the transport answers
  *who is calling*, the store answers *did the operator declare them for this role*.
  The reference validator still does only the second.

Answering a gate and adjudicating a divergence are declared separately, because they
are different authorities (§5.3 vs §6.1) and one should not silently grant the other.

### Distribution

`asop-spec` **0.3.0** carries both v3.2 and v3.3. v3.2 announced 0.3.0 and the release was
never cut — PyPI stayed at 0.2.0 — so rather than ship a phantom 0.3.0 for v3.2 and a 0.4.0
minutes later, one minor release carries both. Nothing depended on 0.3.0 meaning v3.2,
because nothing could: it was never there to depend on.

New public API in this release: `verifiers_from_env`, `adjudicators_from_env`, `resolves`,
`may_adjudicate`, and the four `*_ENV_VAR` constants. Two new variables are read
(`ASOP_VERIFIERS`, `ASOP_ADJUDICATORS`), each with the `AGENTCO_*` name as a deprecated
fallback.

## v3.2 — 2026-09-09

A concept-level cross-validation, not an implementation review: three independent builds
of this contract in one week — a Python harness, the Python plane, and a .NET
implementation at Acme — each separately shipped the same two gaps. That is a
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
will see new refusals. (`pyproject.toml` carried `0.2.0` until this was reconciled: the
changelog announced a version the package did not claim, and both consumers were still
locked at `0.1.0`.)

### Still open

`verdict` is encoded in the attestation schema, reference validator, and conformance
vectors. It carries a boolean `passed` and a nonblank `reason`; completion requires
both exit status zero and a positive verdict when present. Registry authentication
(`unauthenticated`) remains a store/transport integration requirement; the standalone
validator receives an already authenticated submitter and does not resolve registries. The §3.5 `uses`
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
