# Migrating to v3.6 / asop-spec 0.6.0

## v3.6.1 / 0.6.1: the member digest

**What changes:** nothing for an implementation that digests members with
`asop.manifest.digest_of` over the validated body (or now `body_digest`). An
implementation that computed member digests any other way — over the authored document
instead of the normalised one, with store fields included, pretty-printed, sorted by
UTF-16, or with NFC normalization — must switch. Until it does, a manifest it writes will
not verify against a store that follows §3.10.

**The check:** run `conformance/vectors/digest.yaml` against your digest function.
`dg-fixed-document` must produce exactly
`sha256:81235e55f50bed91912dde6fb856bde8a05194f5bc72b2915884108eb28e6555`.
If it does not, `dg-normalised-body-digests-the-same` tells you which half is wrong:
it carries the normalised body itself, so when that vector passes and the fixed one
fails, the difference is in your normaliser, and when both fail, it is in your
serializer.

Manifests already recorded with non-conforming digests do not become valid by
upgrading. Re-pin those chains, or adopt them to a re-pinned manifest (§3.10).


## What an adopter at 0.5.0 must do

**Nothing.** v3.6 is additive:

- every v3.5 record is a valid v3.6 record, and means the same thing;
- every v3.5 gate has the same outcomes — exit 3 is still a failure, exit 0 is still a
  pass, unless the gate declares otherwise;
- every existing function keeps its signature; each new parameter is a keyword whose
  default reproduces v3.5;
- an engine that pins nothing, journals nothing new and classifies no faults conforms to
  v3.6 as it is.

The pin `asop-spec>=0.4.0` or `>=0.4.1` keeps working, and so does `>=0.5.0`.

**One thing to know before accepting v3.6 records.** The record and gate schemas are
closed, as in v3.5: a 0.5.0 reader **refuses** a record or gate that uses a v3.6 field.
That is deliberate — a reader that dropped an entry gate or a rework bound it did not
understand would run the procedure without it. Upgrade every reader (plane, harness,
store) to 0.6.0 before any author writes a v3.6 field. A v3.6-normalised gate that uses
no v3.6 field round-trips through a 0.5.0 reader: its new keys are null, and nulls are
dropped before validating.

## What to adopt first

In order of cost against value, from what the evidence behind v3.6 found most expensive:

1. **`cannot_evaluate_exit` and `min_cases` on deterministic gates (§3.3).** One line per
   gate. It stops an empty or uncollectable test suite from passing, and stops an
   environment that could not run a check from spending the executor's repair budget.
   Engines read `gate_outcome` instead of `gate_satisfied` where they need to tell
   `failed` from `cannot_evaluate`.
2. **Per-step journaling (§5.6) and fault classes (§5.4).** Append an
   `asop.step-journal/1` entry at each step's start and end; resume with `resume_point`;
   count the budget with `charged_failures`. This is engine-side only; no record changes.
   It fixes wrong step timings, missing values between steps, and crashes charged as
   failures.
3. **The chain manifest (§3.10).** Pin at chain start with `pin_manifest`, resolve with
   `manifest_resolver`, and move a running chain only with an adoption. Include the
   checker and any rules file as `components`. Engine-side; no record changes.
4. **Entry gates and preflight (§3.8),** starting with a readiness gate on the source
   document a chain starts from, then the hand-off gate on the consumers whose failures
   surface late.
5. **`proceed_on_default` (§3.3)** on product questions that should not block the chain,
   with the staleness rule (§5.7) so a late answer reopens what was built on the default.
6. **Generated plans (§3.7):** `order_by`, `concurrency`, `approved_subset`, and
   `binds_digest` on approvals.
7. **Rework edges (§3.9)** for loops your engine already runs in code; then delete that
   code.
8. **`chain_gate` and the chain outcome record (§5.8),** once the ledger it reads exists.

## Candidates for v4

Each was left out of v3.6 because it cannot be added without changing what an existing
record, gate or engine means.

- **Mandatory chain pinning.** Requiring every chain to resolve from a manifest would
  make every v3.5 engine — which resolves ranges at filing — non-conforming. v3.6 makes
  it opt-in; v4 could make it the rule.
- **`cannot_evaluate` by default.** Reading exit 3 (or zero cases) as `cannot_evaluate`
  for every deterministic gate, without a declaration, would change the outcome of
  existing gates.
- **Judged entry and chain gates.** v3.6 allows deterministic ones only; allowing a judge
  later is additive, but retracting one would not be, so it waits for evidence.
- **Normative `inputs[].kind` and `outputs[].kind`.** Still reserved and unvalidated
  (§11.5). Validating them would refuse records that use them freely today.
- **Declared per-step capabilities and environment.** A step declaring what it needs to
  run, a harness declaring what it can run, and "not runnable here" as a refusal like
  `role_unbound`. Additive as a field, but it needs a vocabulary two harnesses agree on,
  which does not exist yet.
- **Run-instance identity across machines.** §7.4's version identity extended to
  `(operator, machine, instance)`. It touches ownership (§7.2), which is not additive.
- **The revision policy over v3.6 fields.** §6.4 polices an agent's revision by role and
  gate `kind`, protected tags and no-undo on prose fields. It does not yet stop an agent
  moving a human gate to `proceed_on_default`, raising a rework edge's `max_rounds`, or
  dropping an entry gate. Each is a weakening a person should approve, and refusing it is
  a tightening — it would refuse agent revisions accepted today.
- **A structural proposal type "move this check upstream"** (§6.3). v3.6 names it as
  evidence for an entry gate; making it a first-class proposal changes the lessons pass.
