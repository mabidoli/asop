# Changelog

Version history for the ASOP specification. The specification itself is
[`ASOP.md`](ASOP.md); this file is where it has been, so the specification can be
about what it is.

## v3.6.1 / asop-spec 0.6.1 — unreleased

### The member digest is normative (§3.10)

v3.6 said "a member's digest is the store's" and stopped there. That let every store
invent its own digest, and a manifest written by one engine could not be verified by
another. v3.6.1 defines it, as exactly what the first store to verify manifests already
computes, so none of the digests it has recorded change:

- **Input:** the record's validated body. The seven store-assigned fields (`asop_id`,
  `version`, `status`, `author`, `author_kind`, `superseded_by`, `created_at`) are
  removed, and the rest is normalised by `validate_asop`: absent and `null` are the same,
  text is stripped, tags are folded, and the gate carries every field (`null` where
  unset).
- **Serialization:** keys sorted by code point, no whitespace, UTF-8, only `"`, `\`
  and U+0000–U+001F escaped, no Unicode normalization, decimal integers. Floats and lone
  surrogates are refused.
- **Hash:** `sha256:` followed by 64 lowercase hex digits.

v3.6 claimed this serialization was RFC 8785's. That was wrong for keys outside the
Basic Multilingual Plane, because RFC 8785 sorts by UTF-16 code unit and this sorts by
code point. The text now says which.

One forward rule: a later version MUST NOT change the normalised body of a record that
uses none of its new fields, or every pinned chain would be stranded.

### Refusal codes for storing chain records, and the approval validator (§10, §3.7)

The first plane to store manifests, adoptions and approvals shipped eight refusal codes
the specification did not define. A second implementation would have had to guess them.
They are now in §10, each reviewed before it was frozen:

| code | when |
|---|---|
| `manifest_invalid` | the document is not a valid manifest |
| `manifest_member_unknown` | a member is absent from the store, or is only a DRAFT |
| `manifest_digest_mismatch` | a member's digest is not the store's (§3.10) |
| `manifest_unknown` | **new name**: an impact or adoption target manifest is not recorded for that chain |
| `chain_unknown` | no manifest has been recorded for the chain |
| `adoption_invalid` | the record is not exactly the computed move |
| `approval_invalid` | the document is not a valid approval |
| `contract_unsupported` | the receiver's contract does not implement the record kind |

The shared rules: every one of these stores nothing; the order of checks is normative;
each is permanent for the same request; and HTTP statuses are not part of the code.

- **One rename.** The plane raised `manifest_member_unknown` for an unrecorded *target*
  manifest too. That is not a member, so the case is now `manifest_unknown`. The old use
  is recorded in `asop.refusals.LEGACY_USES`, and readers accept both until the plane
  migrates.
- **`unknown_field` stays out of the vocabulary.** It is the plane's HTTP-surface
  refusal for a route key. It is catalogued with the plane's other transport codes
  (`bad_json` and the like) and is not part of §10, because every record already
  refuses its own unknown fields under its own code.
- **Left open (§11.11).** The plane refuses a non-human adoption or approval with
  `capability_mismatch`. §8.2 (v3.6) says `revision_policy:human_only`. Neither is
  frozen here.
- `asop.chain.validate_approval` (new) validates `asop.approval/1` as the schema states
  it. It mirrors the plane's own validator, which agrees on every vector.
- Store-side checks that raise the codes, all new in `asop.manifest`:
  - `verify_manifest(doc, fetch)`
  - `adoption_target(chain, to, current=, manifests=)`
  - `check_pinned(manifest, asop_id, version)`
  - `require_schema(doc, supported)`
- Conformance: `chain-store.yaml`, 22 vectors with a refusal case per code and accept
  cases beside them. Assertions A164–A171.

**Compatibility:** a clarification. No document changes validity, and no digest the
reference implementation computed under 0.6.0 changes. An implementation that computed
member digests another way must switch (see `MIGRATION.md`). The refusal codes are
additive: they name refusals the plane already made, and one of them, for one
situation, under a new name.

### Distribution

- `asop.manifest.body_digest(record, *, asop_id=None)` (new) is the member digest;
  `STORE_FIELDS`, `canonical_json` and `digest_of` are now exported from `asop`.
  `pin_manifest`'s default `digest` is `body_digest`, which equals the previous default
  on the validated body it is given. `canonical_json` refuses a lone surrogate with
  `ChainContractError` instead of raising `UnicodeEncodeError`.
- Conformance: `digest.yaml`, 12 vectors. One fixed document pins its digest string.
  Seven invariance cases (reordered keys, a pretty-printed JSON source with `\u`
  escapes, store fields present, `null` for absent, a gate field written as `null`,
  stripped whitespace and folded tag case, and the normalised body itself) must give
  the same string. Two must differ (changed text, and decomposed Unicode). Two are
  refused (a float, and an invalid record). Assertions A160–A163.
- The pinned string was checked byte for byte against the hub's own `store_digest`,
  under both 0.6.0 and this branch.

## v3.6 / asop-spec 0.6.0 — 2026-10-17

### Chains: what a long run taught, as declarations (§3.3, §3.7–§3.10, §5.2–§5.8)

**Additive, every part of it.** A v3.5 record is a valid v3.6 record and means the same
thing; a v3.5 gate has the same outcomes; an engine that declares nothing new behaves as
it did. Each new behaviour is opt-in by a declaration on the record or the gate, or by an
engine choosing to pin chains. What an adopter at 0.5.0 must do: nothing. What to adopt
first, and what was left out because it could not be additive, is in
[`MIGRATION.md`](MIGRATION.md).

The evidence is a real run of a sixteen-procedure chain: hours per work item, tens to
hundreds of agent steps per run, and a defect registry of about a hundred classes with
hundreds of sightings. Most of the costly defects were not in any step's prose — the
revision target v3 points at — but between steps: in gates that could not evaluate, in
loops and pins that lived in engine code, in defaults nobody saw, and in a chain whose
outcome nobody recorded. The seven changes, each with what an implementation must now
do differently **if it declares the feature**:

1. **Chain pin and adoption (§3.10).** A chain may pin one **manifest** at its start:
   every reachable procedure resolved once (one version each) plus the digests of the
   components that give its gates meaning (a checker, a rules file). The chain resolves
   from the pin for the rest of its life; a hand-off range no longer resolves at each
   filing. A chain that cannot be pinned whole is refused at its start. Moving a running
   chain is an **adoption**, recorded by a declared human with an impact report computed
   from the two manifests. New: `asop.chain-manifest/1`, `asop.adoption/1`,
   `adoption_required`. *Opt-in:* an engine without manifests resolves at filing as in
   v3.5 and may not claim it pins chains; making the pin mandatory is a v4 candidate.
2. **Declared, bounded rework (§3.9).** A `rework` edge sends work back — to an earlier
   step when a later step's gate fails, or to the upstream procedure that owns a finding
   when the run ends with a rework outcome — with `max_rounds` (1..3), an explicit
   `consumes_attempt`, and optional `tier_bump`. A procedure edge re-files the owner as
   its next attempt with its inputs, carrying the declared `findings` output as prior
   findings. Exhausted rounds escalate. Rework edges must go upstream and are excluded
   from the cycle check, as escalations are. New refusal: `rework_refused:<rule>`.
3. **The hand-off gate and the third outcome (§3.3, §3.8).** `entry_gates` declare a
   consumer's deterministic precondition over its declared inputs. A gate may declare
   `cannot_evaluate_exit` and `min_cases`; `cannot_evaluate` never releases dependents
   and is never charged to the executor. A refused entry gate re-files the producer (a
   `failed` one charged to the producer, a `cannot_evaluate` to nobody); the consumer is
   never charged. A hand-off may declare `preflight` so the producer runs the consumer's
   lint. Conformance now refuses, as a pass, a gate that exits 0 having run zero cases.
4. **Generated plans as typed data (§3.7).** `subject_from` gains `order_by` (each filed
   run carries `depends_on` and starts only after them), `concurrency`, and
   `approved_subset` (only subjects a person approved, in an approval bound to the
   output's current digest). `human_approved` gains `binds_digest`. A stale approval
   files nothing. New rules: `handoff_refused:order`, `handoff_refused:approval`; new
   record `asop.approval/1`.
5. **Non-blocking human gates (§3.3).** `on_timeout: proceed_on_default`, human gates
   only, with a required `default: {option, consequence, audience}`. The default is
   recorded as taken; a late answer that differs makes what was built on it stale.
6. **Chain outcome and chain-level done (§5.8).** A terminal procedure may `require` a
   `chain_gate` over the run ledger. `asop.chain-outcome/1` records wall time, spend,
   human wait, failures and escalations by fault class, escalations by audience, member
   versions and any procedure run at two versions; it is the chain's row in
   `outcomes_by_version`.
7. **Per-step journaling and resume (§5.4, §5.6).** `asop.step-journal/1` entries are
   appended when a step starts and ends, never batched; `resume_point` resumes from the
   journal alone; a crash re-file is not an attempt; only `work` faults (or unclassified
   ones) count toward §5.4's budget.

And three folded in:

- **(a) Source readiness** is an entry gate over a source input (§3.8): evaluated
  deterministically before the procedure starts, held for a person when not ready,
  recorded with the source's digest, and invalidated when the source changes (§5.7).
- **(b) Answers are inputs** (§5.7): a run built while a question was open records it
  with digest `null`; writing the answer makes it stale and re-files it. The general rule
  — stale when an artefact read has changed digest — is now stated once, for outputs,
  sources, approvals and answers alike.
- **(c) Re-filing an exhausted owner with the findings** is the procedure rework edge of
  (2); there is no second mechanism.

**Compatibility.** For records, gates and engines: additive. For readers: as in v3.5,
the record schema is closed, so a 0.5.0 reader refuses a record or gate that uses a v3.6
field. That is deliberate — dropping an entry gate or a rework bound it did not
understand would run the procedure without it. Adopt 0.6.0 before accepting such
records. A v3.6-normalised gate that uses no v3.6 field carries only nulls in the new
keys, which a 0.5.0 reader drops before validating, so it round-trips.

One existing test pinned the closed hand-off rule set; it now lists v3.6's two rules
beside the nine it had. No other existing test, and no existing vector, changed.

### Distribution

- Reference implementation, all exported from `asop`:
  - `asop.gates`: `GATE_OUTCOMES`, `gate_outcome`, `timeout_resolution`; `gate_satisfied`
    honours `cannot_evaluate_exit` and `min_cases`;
  - `asop.chain`: `plan_entry`, `preflight_checks`, `plan_rework`, `fanout_startable`,
    `MAX_REWORK_ROUNDS`, `REWORK_RULES`; `plan_handoffs` takes `approvals` and
    `output_digests`; `requires_satisfied` takes `approvals`, `digests` and `chain_gates`;
  - `asop.manifest` (new): `pin_manifest`, `manifest_resolver`, `manifest_digest`,
    `validate_manifest`, `is_pinned`, `adoption_impact`, `validate_adoption`;
  - `asop.journal` (new): `validate_journal_entry`, `resume_point`, `charged_failures`,
    `stale_runs`, `chain_outcome`, `FAULT_CLASSES`.

  No existing signature changed; every new parameter is a keyword with a default that
  reproduces v3.5.
- Schema: `gate.yaml`, `attestation.yaml` and `asop.yaml` gain the optional fields above;
  five new schemas: `chain-manifest.yaml`, `adoption.yaml`, `approval.yaml`,
  `step-journal.yaml`, `chain-outcome.yaml`.
- Conformance: 89 new vectors, 229 in all — seven new files (`entry`, `rework`,
  `fanout`, `manifest`, `journal`, `staleness`, `chain-outcome`) and additions to
  `gate`, `attestation`, `satisfaction`, `chain-record`, `handoff` and `requires`.
  25 new assertions, A135–A159; 23 are cited by a vector, and A148 and A152 need a
  resolver, so the Python tests cover them. The runner learns seven artifact kinds and
  a `pinned` hand-off vector.
- Checked by 16 deliberate mutants of the new rules: the vectors caught all 16; the
  Python tests caught 15 until the one that slipped through (a running run holding a
  concurrency slot) got a test, and now catch all 16.

## v3.5 / asop-spec 0.5.0 — 2026-10-10

### Hand-offs: an ASOP declares where a finished run may go next (§3.7)

**This reverses §11.4 (2026-09-04), which dropped `next_sop` and put all sequencing
between procedures in the harness.** The first real chained execution showed what that
costs. Ten procedures ran end to end, and the flow lived in one engine's rules file and
driver, outside every ASOP:

- a procedure shared to another harness arrived without its place in any flow;
- a new flow meant editing the engine;
- porting the engine meant porting one organisation's flow with it.

The owner chose declared hand-offs (option C) over fixed successors (A, which cannot fan
out over a count known only at run time) and run-time requests alone (B, under which a
misbehaving run could file anything). §11.4 now records the reversal and its reasons.

Five optional record fields:

- `outcomes`, a closed set of run outcomes;
- `outputs`, `[{name, description?, kind?}]`, the mirror of `inputs`;
- `handoffs` on the predecessor, each with:
  - `id`;
  - `to`, a pin or a `>=N[,<M]` range resolved at filing to the ACTIVE version;
  - `on_outcomes`;
  - `cardinality: one | many`;
  - `subject_from` with `where` / `where_not`;
  - an `inputs` mapping;
  - `after`, which makes the hand-off a join;
- `on_escalate`, whose target must contain a human step;
- `requires` on the successor: `run_done` or `human_approved`, as a closed list.

There is also a new output schema, `asop.filing-request/1`
([`schema/v1/filing-request.yaml`](schema/v1/filing-request.yaml)), and a new refusal,
`handoff_refused:<rule>`, with nine rules: malformed, undeclared, outcome, cardinality,
subject, output, inputs, version and conflict. The verb table gains
`handoff(run_id, requests?)`.

**The rules that matter most:**

- **A run chooses which, never what.** A filing request names a declared hand-off and,
  on a fan-out, a subject. It carries no inputs.
- **A refused request is as if it had not been written.** It never narrows, cancels or
  alters what the ASOP declared, so one bad request cannot silence a declared fan-out.
- **A closed outcome set is enforced.** A run that finishes with an outcome its ASOP does
  not declare files nothing, and that is refused rather than silent.
- **A join lives on the procedure that fans out.** It is a hand-off with `after`,
  filed once, when every run the listed hand-offs of the same parent run filed is done.
  - An empty fan-out makes it ready at once; a refused fan-out or a failed run blocks it
    for a person.
  - Runs filed by any other path are not part of it, so escalations and new versions
    cannot move what it waits on.
  - Every refusal names the targets it leaves unfiled (`toward`); a refused request
    leaves none, and blocks nothing.
- **`requires` is narrow:** `run_done(X)` is X's chain run, and `human_approved(A)` is a
  person's approval in this chain. A run whose `requires` never hold is surfaced by the
  engine's liveness clock.
- **Filing is idempotent on a percent-encoded natural key.** A re-filing carries the next
  attempt. The same key with different inputs is a `conflict`.
- **Comparisons are type-strict.** Subject ids are strings or integers and must be unique;
  values handed on are scalars; names are exact (refused, never stripped); version
  ranges are `>=N[,<M]` in ASCII digits with no spaces.
- **Activation refuses:**
  - hand-off cycles across records (a loop through an escalation is allowed, because a
    person bounds it);
  - any target input left unmapped;
  - a `requires` on anything the record itself can transitively file.

**Corrected from the proposal that motivated it, each for a stated reason:**

- **`outcomes` and `outputs` are declared.** Otherwise `on_outcomes` and `subject_from`
  name things nothing declares. A misspelt outcome would be a hand-off that silently
  never fires, and a fan-out would read an output nobody promised to write. That is the
  inter-procedure contract whose absence let a missing field crash a gate in the same
  execution.
- **`requires` keeps two of the six predicates proposed.**
  - `slot_free` is concurrency, which the same proposal called engine configuration.
  - `artefact_current` is staleness, which it recommended as an engine duty.
  - `no_run_for_subject` is redundant with filing being idempotent on the natural key.
  - `all_runs_done` became joins (below).

  Each is refused by name, with where it belongs instead.
- **Joins moved to the procedure that fans out.** The proposal asked where joins should
  live and recommended the waiting procedure. This release first shipped that, and it
  did not hold up (below).
- **Requests carry no inputs.** The proposal let a request supply inputs, which made a
  successor's inputs depend on what a run wrote, and made the activation check either
  too strict or meaningless.
- **No per-ASOP `retry` section.** §5.4 already fixes the policy at one repair, then a
  person.
- **A duplicate with the same inputs is recorded, not refused.** Several predecessors
  handing off to one chain run is the normal case.
- **Targets use `asop_id`, not `asop`,** as every other pin does. There is one hand-off
  per target.

**Hardened before release by three rounds of adversarial review, and one redesign.** A first cut of this
release was reviewed before anything was published. The first round confirmed 13 defects,
and each now has a test that fails when the defect is reintroduced; 18 deliberate mutants
were run and every one was caught. The defects:

- one refused request could cancel a declared fan-out;
- an undeclared outcome silently suppressed every hand-off;
- a join fired when the first predecessor finished, and read a refused fan-out as empty;
- the activation check contradicted the prose;
- the natural key ignored attempts, could not tell two hand-offs apart, and could be
  forged with `|`;
- a non-string `requires` kind crashed instead of being refused;
- names were stripped instead of refused, so two keys could collide;
- the schema and the Python disagreed on the range grammar and on subjects;
- `where` and subject ids used loose equality;
- the decision order was normative but unstated;
- path semantics were unstated;
- a cycle across records passed.

The second round, a verification of those fixes, found that the new join rule had its own
holes:
- a join could deadlock on its own run through an escalation edge, or by requiring
  something it files;
- a `conflict` did not hold the join it was about;
- an undeclared-outcome refusal named no targets, so a join downstream could start early;
- cycle detection was blind without the record's own id, and refused a normal
  escalation loop.

All of them are fixed, and 6 more mutants were caught.

The third round found three more, all in the join:
- an escalation from a sibling branch could re-file a run after the join that waited on
  it had already fired;
- a join filed per subject with a `one` hand-off either conflicted for ever or, on a
  re-attempt, silently dropped inputs;
- activating a newer version of a target added an edge that deadlocked a run already
  waiting.

All three came from the same design. A join declared on the WAITING procedure
(`requires: all_runs_done`, "settled") had to decide whether any further run could
still be filed: global reasoning over a graph that escalations and version changes keep
moving. Each round fixed the cases found and the next round found new ones. Patching
it again was not going to converge, so the join moved to the procedure that fans out,
where the question is local: "are the runs **I** filed done?" `all_runs_done`, the
"settled" rule, `predecessors_of` and the `waiter` argument were removed before
release. The three cases are now defined behaviour or impossible:
- runs filed by other paths are not part of a join;
- a per-subject `one` hand-off is no longer how a join is written; two filings of one
  chain run with different inputs are refused as `conflict`, and recorded;
- a join no longer depends on the graph.

A fourth review, of the redesign, found no blocker. It found that an undeclared outcome
did not block a join when asked directly, that a superseded attempt's join could still
be filed, and two join rules no vector pinned. All are fixed, with vectors.

**Compatibility.** Additive for records: every v3.4 record is a valid v3.5 record. It is
not additive for readers. The schema is closed, so a v3.4 reader refuses a record with
hand-offs. That is deliberate: dropping hand-offs it did not understand would turn the
procedure into a dead end. Adopt 0.5.0 before accepting chained records.

### Distribution

- Reference implementation: `asop.chain`, exported from `asop`:
  - `validate_chain_fields`, `validate_handoff_targets` and `validate_filing_request`;
  - `plan_handoffs` and `plan_escalation`;
  - `join_state` and `plan_join`;
  - `requires_satisfied`.

  `validate_asop` gains an optional `asop_id` keyword, so a store can refuse
  self-reference.
- Conformance:
  - four new vector files, `chain-record.yaml` (asop), `handoff.yaml`, `join.yaml` and
    `requires.yaml`, with 79 vectors, for 140 in all;
  - 25 new assertions, A110–A134, of which 23 are cited by a vector. A130 and A132 need
    a resolver, so the Python tests cover them;
  - the reference runner learns four artifact kinds.
- Found while cross-checking, and predating this release: `validate_gate`'s normalised
  output (nulls, `schema_version`) does not validate against the stored step and gate
  schema. It is recorded in `conformance/README.md` and not changed here.

## asop-spec 0.4.1 — 2026-10-10

### `common_mistakes` holds five, not three

The reference implementation's cap on a step's `common_mistakes` rises from 3 to 5
(`MAX_COMMON_MISTAKES`), at both the v3 step and the flat-field validator. The first real
coding-domain execution of a chained library — ten procedures over a multi-epic legacy
modernization — produced steps with more measured failure modes, each traced to an
origin, than three slots held; authors were dropping real ones to fit. It is still a cap
and still a ranking: an unbounded list is a wiki page, and a wiki page is not read at
handoff time.

The specification text is unchanged — ASOP.md and the schema never stated the number —
so this is a package release, not a spec version. Relaxing a bound refuses nothing that
was accepted before. A test now pins the number, so a future change to it fails a test
rather than drifting.

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
