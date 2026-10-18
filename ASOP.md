# ASOP — Agentic Standard Operating Procedure

> *A standard operating procedure for one type of task, written as a sequence of
> beads, versioned as one artefact, executed by any harness, verified by a party
> other than the one that did the work, and revised from the evidence its own
> runs produce.*

**Status:** v3.6.1. v3's review questions (§11) stand as decided on 2026-09-04, except
§11.4, which was reversed on 2026-10-10 by the hand-offs of §3.7. v3.1 through v3.4 are
corrections; v3.5 and v3.6 are additive, and v3.6.1 makes the member digest
normative ([`CHANGELOG.md`](CHANGELOG.md),
[`MIGRATION.md`](MIGRATION.md)). Every v3.6 behaviour is opt-in by declaration: a v3.5
record, gate or engine means exactly what it meant.
Supersedes v2.
**Home:** this repository — the specification, its normative [schema](schema/v1/), its
[conformance vectors](conformance/) and a reference implementation. Distributed as
`asop-spec`, importable as `asop`; a plane or a harness adopts it by version, not by path.
**Audience:** anyone building a harness, a plane, or a procedure. Nothing here names a
vendor, a company, or a tool.

---

## 1. Definition

An **ASOP** is a versioned, ordered sequence of **steps** that together accomplish one
**type of task**. Each step says what must be done, what must be true before starting,
how the result is proven, where the result is written, and which **role** does it.
Filing work from an ASOP produces a tree of beads — one per step — that any harness can
execute, and every bead pins the exact ASOP version it came from. When execution
departs from the procedure, the departure is adjudicated by a party other than the
executor and becomes the input to the next version.

It is a standard in the literal sense: the same procedure, the same version, the same
gates, run by different harnesses in different organisations, producing outcomes that
can be compared because they are counted against the same artefact.

### Running example

The document uses one procedure throughout:

```
ASOP  feature-dev  v3
  step 1  validate-requirements    role: analyst
  step 2  write-tests              role: implementer
  step 3  implement                role: implementer
  step 4  run-tests                role: implementer     gate: deterministic
  step 5  validate                 role: validator       gate: judged
                                   constraint: validator ≠ implementer
```

---

## 2. The three properties

A procedure is an ASOP when it has all three. Missing one, it is documentation.

### 2.1 Versioned — outcomes attach to versions, not to prose

Every revision of the ASOP is a distinct, immutable version. A run pins
`(asop_id, version)` and each of its beads pins `(asop_id, version, step)`. Outcomes
are recorded against that triple. "Did the v3 rewrite of step 2 help?" is answered by
counting, never by recollection.

*Corollary — template and instance are different things.* The ASOP never enters a work
queue. Runs do. A later version does not reach back into a run already filed; the run
finishes under the version it started under, and `drifted()` is how an in-flight run
learns its procedure has moved.

### 2.2 Verified — every step carries its own gate, authored with the version

A step's gate is part of the step, written by the ASOP's author, and immutable to
whoever executes or files it. It is one of three kinds:

| kind | proof | trust model |
|---|---|---|
| `deterministic` | a command exits 0, re-run fresh by the *completing* process | machine-checkable; the executor is its own attester, so this is a trust floor, not a proof |
| `judged` | a rubric fixed at authoring time, evaluated by a **route different from the executor's** | no self-grading |
| `human` | a named person acknowledges; the bead parks until they do | for irreversible or outward-facing steps |

The gate is enforced **where completion is recorded**, not where the work is done. A
run whose step cannot pass its gate did not complete that step, whatever the transcript
says. A gate MAY carry a park clock and an escalation path (§9), and where work can
wait — a plane that parks a human gate and comes back to it — a gate with no
timeout is a deadlock, so the clock is required there. A caller with no parking
concept omits the group entirely and treats a timeout as an ordinary failure.
The clock is declared as a whole or not at all: a gate carrying some of its
fields but not the rest is always refused, whether or not the caller requires
the group.

This was stated as an absolute in earlier drafts, while every implementation
made it optional. An implementer reading the prose and one reading the code
disagreed about whether the same gate was valid, which is the disagreement a
standard exists to prevent.

A gate may declare a single `check` or an ordered ladder of `checks`. A ladder
takes **one attestation per rung**, each pinned to its index, and is answered
only when every rung is present and passing. Evidence that cannot say which rung
it climbed is not evidence about the ladder, and a ladder with a missing rung is
not a ladder that was climbed — the failure that hides is the one staging exists
to surface. A gate with a single `check` has no stage to attest to and refuses
one.

### 2.3 Self-revising — divergence is input

When execution departs from a step, the departure is captured and **adjudicated** by a
party other than the executor:

- **good** — the procedure was wrong. Feeds the next version's `proposals`.
- **bad** — the execution took a shortcut. Feeds `common_mistakes` and root-cause
  analysis.

A plan-vs-actual review is written per step at the moment that step completes, while
the context still exists. Revision is deliberate: proposals accumulate, a pass drafts
the next version from them, a human or a policed agent activates it. Never silent.

---

## 3. Structure

### 3.1 The ASOP record

```yaml
asop_id: feature-dev          # stable identity across versions
version: 3                    # immutable once activated
title: Develop a feature
status: active                # draft | active | superseded | retired
task_type: feature            # the type of task this standardises (free label)
purpose: >
  Take a specified feature from requirements to verified, merged code.
trigger: >
  A feature bead exists with a written requirement and an owner.
inputs:                       # what a RUN must be given, by name
  - name: requirement
    description: the requirement document or bead the feature answers
  - name: repo
    description: the repository the feature lands in
    # kind: reserved, optional, unvalidated in v3 — see §11.5
roles:                        # declared here, bound by the harness (§7)
  analyst:     {kind: agent}
  implementer: {kind: agent}
  validator:   {kind: agent}
constraints:
  - distinct: [implementer, validator]   # the same binding may not fill both
steps: [...]                  # §3.2, ordered
author: mabidoli
author_kind: human            # human | agent — set by the operator, never inferred
proposals: []                 # open revision proposals, carried until addressed
superseded_by: null
created_at: 2026-09-04T00:00:00Z
```

**Bounds.** An ASOP has at most **7 steps** at one level and nests at most **3 deep**
(§3.5). These are the decomposition bounds the plane already enforces on bead trees,
and they mean the same thing here: a procedure sized to what an accountable person can
sanity-check. A procedure that needs more is usually two procedures.

### 3.2 The step

A step is what v2 called the procedure. Its fields are unchanged in meaning; what is new
is that a step belongs to an ASOP, carries a gate, and names a role.

```yaml
- step: 5
  name: validate
  role: validator
  purpose: >
    Confirm the implemented feature satisfies the requirement as written, not as
    interpreted.
  entry_check: >
    Step 4 is done with its gate passed; the requirement from the run's inputs is in
    hand; the diff is available.
  inputs: >
    The requirement, the diff, the test report from step 4.
  definition_of_done: >
    Every acceptance criterion in the requirement is traced to a test that exercises
    it and passes, or is explicitly listed as not covered with a reason.
  validation: >
    A traceability table exists mapping each criterion to a test id; no row is empty.
  write_back: >
    The traceability table is attached to the run's parent bead; gaps are filed as
    sibling repair beads against step 3.
  common_mistakes:
    - Validating against the implementation's own description of itself.
    - Marking a criterion covered because a test with a similar name exists.
  gate:                        # §3.3 — authored HERE, with the version
    kind: judged
    check: >
      Every acceptance criterion maps to a passing test id, or to a stated gap.
    rubric: asop://feature-dev/v3/rubrics/validate
    judge_route: distinct-from-executor
    max_park_seconds: 86400
    on_timeout: escalate
    escalate_to: role:owner
  after: [4]                   # §3.4 — ordering; default is the previous step
  tags: []
```

Field semantics, carried from v2:

- `entry_check` — what must be true and in hand *before* starting, phrased so a missing
  item becomes a question to ask rather than an assumption to make.
- `definition_of_done` and `validation` are kept distinct on purpose: the first is
  what is being *claimed*, the second is the check that would *fail if the claim were
  false*. Collapsing them is how "done" comes to mean "I believe I finished."
- `write_back` — where the outcome lands, for whom. The half of a handoff that gets
  dropped, and the half the next step's `entry_check` is written against.
- `common_mistakes` — grows from bad adjudications (§6).

### 3.3 The gate

The gate schema is the one `asop.gates.validate_gate` already enforces (schema v1),
unchanged. It has three groups:

| group | fields | notes |
|---|---|---|
| core | `kind`, `check` **or** `checks[]` | `checks` is a staged ladder; a failure names the stage. `class` is accepted as a read-alias of `kind` for records written before the schema was unified |
| clock | `max_park_seconds`, `on_timeout` (`pass` \| `fail` \| `escalate` \| `proceed_on_default`), `escalate_to`, `verifier`, `default` | all-or-nothing as a group. A `human` gate must name its `verifier`; a `deterministic` gate must not |
| execution | `cwd`, `timeout_s`, `rubric`, `judge_route`, `cannot_evaluate_exit`, `min_cases` | hints to the executing domain; the last two are the third outcome (below) |

**The third outcome** *(v3.6)*. A check that could not judge the work is not a check
that judged it wrong, and neither is one that passed. A long chain observed in
practice escalated more often for this than for anything the work did: a test suite
that collected nothing on a clean machine exited 0 or 1 according to its runner, and
was read as a pass or charged to the executor. Two optional fields name the case:

- `cannot_evaluate_exit` (1..255, 3 by convention): that exit status is
  `cannot_evaluate`;
- `min_cases` (≥ 1): an exit 0 that ran fewer cases is `cannot_evaluate`. The
  attestation then carries `cases_run`, because an exit status alone cannot tell a
  green suite from an empty one.

Both are for `deterministic` gates only. `cannot_evaluate` never releases dependents
and is never charged to the executor (§5.2, §5.4); what happens next is §3.8's. A gate
declaring neither field has exactly the two outcomes it had in v3.5, so no existing
gate changes meaning.

**Non-blocking human gates** *(v3.6)*. `on_timeout: proceed_on_default` lets a `human`
gate's question stop blocking the chain: when the clock expires, the step is `done` on
its declared `default` — `{option, consequence, audience}`, each required. The engine
records the default as taken and what was built on it, and a later answer is an input
like any other (§5.7): if it differs, everything built on the default is stale. A
default with no consequence or no audience is a decision nobody owns, so it is
refused. Deterministic and judged gates may not proceed on a default: a check that did
not run did not pass.

Whether the clock group is mandatory is a caller decision (`require=("clock",)`): a
coordination plane requires it, because a parked gate it cannot expire is a queue it
cannot drain; a runtime may relax it for deterministic gates that cannot park.

**What v3 changes about gates is only where they live:** on the step, authored with the
version. A run supplies none. A harness that lets a filer or executor override a step's
gate is not conforming.

### 3.4 Ordering

Steps are a sequence by default: step *n* is `after: [n-1]`. A step may declare `after`
explicitly to run in parallel with siblings — the tree it files carries that as
`blocked_by`. The bound on width is the bound on steps. `after` is validated at
`sop_create`: a reference to a step that does not exist, or a cycle, is `sop_refused`.

### 3.5 Nesting

A step may be an ASOP: `uses: {asop_id, version}` in place of a body. Filing it files
the inner ASOP's tree as that step's children, pinned to the inner version. Depth counts
against the 3-deep bound. The inner ASOP's gates are its own; the outer step's gate, if
any, applies to the inner run as a whole.

### 3.6 Roles and separation of duties

An ASOP names **roles**, never agents, models, vendors or people. `implementer` and
`validator` are role names; what fills them is a harness decision at run time (§7).
This is what makes the artefact shareable: two organisations run `feature-dev v3` with
different bindings and their outcomes are still counted against the same version.

`constraints` express separation of duties over roles. `distinct: [a, b]` means the
binding that fills `a` in a run may not fill `b` in that run. The contract requires one
constraint by construction: **a step whose gate is `judged` is evaluated by a route
distinct from the route that executed it**, and a step of role `validator` over another
step's output may not share a binding with that step's role. A harness that cannot
satisfy a constraint **refuses the run** (`role_unbound`, `constraint_unsatisfiable`) —
it does not quietly bind the same agent twice.

Roles have a `kind`: `agent` or `human`. A `human` role's steps carry `human` gates by
construction, and so does a step carrying a **protected tag** (`money`, `irreversible`
by default — §6.4): the tag means a person looks before the step counts as done, and
the record refuses a gate that means nobody does. The revision policy protects both
classifications.

### 3.7 Hand-offs — chaining between procedures *(v3.5)*

**This section reverses §11.4.** v3 kept sequencing between procedures out of the
artefact. The first real chained execution showed the cost of that: ten procedures run
end to end over a multi-part legacy modernisation. The flow lived in one engine's
rules file and in one 2,000-line driver that bound procedure roles to specific rules,
so three things went wrong:

- an ASOP shared to another harness arrived without its place in any flow;
- a new flow meant editing the engine;
- porting the engine meant porting one organisation's flow with it.

What moves into the artefact is the **declaration**; **execution** stays in the harness.

| field | on | says |
|---|---|---|
| `outcomes` | any ASOP | the closed set of outcomes a run can finish with |
| `outputs` | any ASOP | what a run writes that a hand-off may read: `[{name, description?, kind?}]`, the mirror of `inputs` |
| `handoffs` | the predecessor | where a finished run **may** file next, and how each successor input is computed; a hand-off with `after` is a **join** |
| `on_escalate` | any ASOP | which procedure an escalation files (§5.4) |
| `requires` | the successor | what a filed run needs from outside its own fan-in before it may **start** |

All five are optional. An ASOP without them is standalone, exactly as in v3.4.

**A hand-off.**

```yaml
outcomes: [FETCHED, NOTHING_TO_FETCH, BLOCKED]
outputs:
  - {name: sources, description: every source the run found}
handoffs:
  - id: extract-each-source
    to: {asop_id: extract, versions: ">=2"}       # or {asop_id, version: 2}
    on_outcomes: [FETCHED, NOTHING_TO_FETCH]        # absent = every completion
    cardinality: many                               # default: one
    subject_from: {output: sources, array: sources, id_field: id,
                   where: {kind: [spec, feature]}, where_not: {removed: true}}
    inputs: {source_id: $subject, title: $subject.title, root: $run.inputs.root}
  - id: consolidate                                 # a join: filed once, when
    to: {asop_id: consolidate, version: 1}          # every run the fan-out filed
    on_outcomes: [FETCHED, NOTHING_TO_FETCH]        # is done
    after: [extract-each-source]
    inputs: {root: $run.inputs.root}
on_escalate:
  to: {asop_id: decide, versions: ">=1"}
  inputs: {question_kind: escalation}
```

- **A closed set is closed.** When an ASOP declares `outcomes`, a run that finishes with
  any other outcome (or none) files nothing, and that is refused (`outcome`). Without
  this, a misspelt outcome would silently suppress every hand-off.
- **It fires** when the run completes (§5.5) with an outcome in `on_outcomes`, or on every
  completion when `on_outcomes` is absent. A hand-off that does not fire is not a refusal:
  it is the run saying "stop here".
- **`one`** files one run, whose subject is the chain. Several predecessors handing off to
  the same successor file it once (see the natural key below). That is **not** a join:
  the run is filed when the first of them finishes. To wait for all of them, declare a
  join (`after`, below).
- **`many`** files one run per subject that `subject_from` yields. The run's `output` must
  be a mapping, and `array` is a dot-separated path of keys within it that leads to a
  list of mappings. An item is kept when two conditions hold:
  - every `where` field is present and has one of the listed values;
  - no `where_not` field equals its value.

  Both comparisons are **type-strict**: `true` is not `1`, and `1` is not `"1"`. A kept
  item's `id_field` must be a string or an integer, and it is the subject. Two kept items
  with the same subject are refused (`output`), because which one was meant is not the
  engine's to guess. Two `many` hand-offs to different targets, with different `where`
  filters over the same output, route subjects by kind.
- **One hand-off per target.** Two hand-offs to the same `asop_id` would share natural
  keys, and which inputs a run received would depend on order.
- **Input references:**
  - `$subject`;
  - `$subject.<field>`: one key, no path, on `many` only;
  - `$run.inputs.<declared input>`;
  - `$run.outputs.<declared output>`.

  Anything else is a scalar literal, and `$$` writes a literal `$`. Everything a reference
  names is declared, so a typo is refused where it is written. Every value handed on must
  be a **scalar**: a structure would get around the literal rule and leave staleness
  hashing undefined, so hand a name or a path instead. A successor is handed **exactly**
  its declared inputs, no more and no fewer, and this is checked at activation and again
  at filing.
- **Names are exact.** That covers ids, input keys, `where` keys, outcomes, outputs and
  artefacts. A name with surrounding whitespace is refused, not stripped, because
  stripping is how `a` and ` a` collide.
- **A target** is a pin `{asop_id, version}` or a range `{asop_id, versions}`, never
  both. `versions` is `">=N"` or `">=N,<M"`: ASCII digits, no spaces, N ≥ 1 and M > N. A
  range resolves **at filing** to the successor's ACTIVE version inside it, and the filed
  run pins that version. Outcomes still attach to versions, never to ranges (§2.1).
- **No cycles, no waiting on yourself.**
  - An ASOP may not hand off to, escalate to, or wait on its own `asop_id`.
  - At activation, the graph reachable through **hand-offs** must be acyclic. A cycle
    through a fan-out has no bound.
  - Escalation edges are not part of that graph, because a person sits in every
    escalation and that is the bound. "The decision procedure re-files the step that
    escalated" is a normal pattern.
  - At activation, a record may not `require` an ASOP it can transitively hand off to,
    because those runs can exist only after it finishes.

**A filing request** (`asop.filing-request/1`, [schema](schema/v1/filing-request.yaml)) is
a finished run's own choice, written by its final step. There can be zero or more.

```yaml
{schema: asop.filing-request/1, handoff: extract-each-source, to: extract,
 subject: s2, reason: "only the second source changed"}
```

A run chooses **which**, never **what**: a request names a declared hand-off and, on a
`many` hand-off, a subject. It carries no inputs. If a `many` hand-off has **accepted**
requests, exactly those subjects are filed. Otherwise the engine expands the whole
declaration, so a model step never has to enumerate subjects by hand. **A refused
request is as if it had not been written.** It never narrows, cancels or alters what the
ASOP declared. Without this rule, one bad request from a misbehaving run would silence a
declared fan-out, which is the hole option B left open. A `one` hand-off with more than
one request, or with a request that names a subject, refuses those requests and still
files.

**Refuse, never repair.** At filing, the engine refuses with `handoff_refused:<rule>`:

| rule | refused when |
|---|---|
| `malformed` | the request is not a valid `asop.filing-request/1` (including one carrying `inputs`), or the requests are not a list |
| `undeclared` | it names a hand-off the filing ASOP does not declare, or a different target |
| `outcome` | the run's outcome is not a declared one, or not one the requested hand-off fires on |
| `cardinality` | a `one` hand-off received more than one request |
| `subject` | a requested subject is not yielded, or a `one` request names one |
| `output` | the output a fan-out reads is missing, lacks the declared shape, or yields an unusable or repeated subject |
| `inputs` | the successor's declared inputs are not exactly satisfied by scalar values |
| `version` | no ACTIVE version of the successor is inside the declared range |
| `conflict` | a run with this natural key was already filed with different inputs |

Every refusal is recorded; none is silently dropped. Each refusal also names `toward`:
the targets it leaves unfiled. A refusal with targets **blocks** a join over that
hand-off (below), so a refused fan-out is never read as an empty one.
- An undeclared outcome leaves every target unfiled.
- A hand-off-level refusal (`output`, `version`, `inputs`, `conflict`) leaves that
  hand-off's target unfiled.
- A refused **request** leaves nothing unfiled, because the declared hand-off still files
  without it.

**The order of decisions is normative**, so that two engines agree line for line:
- an undeclared outcome is a single decision on its own;
- otherwise, each request in the order written;
- then each declared hand-off in declaration order, with its subjects in output order
  (or request order). A join that fires is recorded at its place as **waiting**.

**Idempotency.** Filing is idempotent on the natural key
`{chain}|{asop_id}|{subject}|a{attempt}`, with `%` and `|` percent-encoded in each part.
- Filing the same key again **with the same inputs** is a recorded **duplicate**, not a
  refusal. Several predecessors handing off to one chain run is the normal case, and a
  replayed ledger files nothing twice.
- Filing the same key **with different inputs** is a `conflict`, so which inputs a run
  receives never depends on which predecessor finished first.
- When the engine re-files downstream of a stale input, it files the next **attempt**.

**Joins live on the procedure that fans out.** A hand-off with `after` is a join: "when
every run my listed hand-offs filed is finished, file this". The engine filed those runs
itself, from this one parent run, so it knows exactly which runs to wait for, and the
question never needs the wider graph.
- **Shape.** `after` lists hand-offs declared **earlier** in the same record, never the
  join itself, so joins are acyclic by construction. A join is `one`: it files one run
  whose subject is the chain. To fan out again, the joined procedure declares its own
  hand-off. A join may wait on another join.
- **At completion** a join that fires is recorded as **waiting**, not filed.
- **It waits on** exactly the runs its `after` hand-offs filed for this parent run, or
  found already filed (a `duplicate` the fan-out still relies on), plus any listed join
  not yet filed. A run of the same procedure filed by any other path (another parent,
  an escalation's decision) is **not** part of it. Escalations and new versions
  therefore cannot move what a join waits on.
- **Ready** when every one of those runs is done, **including when there are none**. An
  empty fan-out is a finished one; a join waiting for runs that will never exist is a
  deadlock. Once ready, it is filed like any hand-off: its target resolves **then**, and
  its inputs read the parent run's inputs and outputs.
- **Blocked** when a refusal of one of its `after` hand-offs left a target unfiled, when
  an undeclared outcome did (it leaves every target unfiled), or when one of their runs
  ended without done. A refusal is never an empty result, and a
  failed run is never a finished one. The engine surfaces a blocked join to a person.
  It clears when the failed run is re-run to done under its own key, or when the parent
  is re-attempted, which files the fan-out afresh. A refused **request** blocks nothing.
- **A re-attempt retires the old joins.** When a parent is re-attempted, the waiting joins
  of its earlier attempts are never filed. Otherwise a slow run of the stale fan-out
  would file a join after the fresh one.

What a join gives up: one procedure cannot wait on runs that two unrelated branches
filed unless both pass through a single fan-out with a join. That is deliberate. "Has
every run that could ever be filed finished?" was the question this section first
asked of the waiting procedure, and three review rounds each found a new early-fire or
deadlock case in it (see CHANGELOG v3.5).

**`requires`** names what a run needs from **outside** its own fan-in. It is evaluated
within one chain, and every predicate must hold before a filed run may start:

```yaml
requires:
  - {kind: run_done, asop_id: conventions}
  - {kind: human_approved, artefact: research-review}
```

- **`run_done(X)`**: X's **chain run**, the one whose subject is the chain (as a `one`
  hand-off files it), exists and its highest attempt is done. Per-subject runs of X
  never satisfy it: waiting on a fan-out is a join.
- **`human_approved(A)`**: a declared human (§9) approved artefact A **in this chain**. The
  engine resolves the approver; the record names only what is approved.
- `requires` is a **start** condition. A later attempt of X, filed after this run
  started, does not un-start it; that is staleness, which the engine handles by lineage.
- A run whose `requires` never hold is held, and the engine's **liveness clock**
  surfaces it to a person. Activation refuses the one wait that can never end (a
  record requiring an ASOP it can itself transitively file), but only as seen from that
  record's own activation; a later version of a descendant can still add such a path,
  and the clock is the backstop.
- `all_runs_done` was withdrawn before release; a record that declares it is refused
  with a pointer to joins.

The kinds are a **closed list**. A predicate two engines evaluate differently is a fork of
the contract, so the list grows by spec version, never by author.

**Generated plans** *(v3.6)*. A procedure that writes a plan — a list of tasks with
dependencies, which a person approves in part — is how a fan-out's count gets decided
in practice, and v3.5 could fan it out but not keep its order or its approval. Three
optional fields on `subject_from`:

- `order_by: <field>` names the item field that lists the subjects it depends on. Each
  filed run carries `depends_on`, the natural keys of those runs, and may **start** only
  when they are all done (`fanout_startable`). A dependency the hand-off does not file,
  or a cycle, refuses the hand-off (`order`) and blocks its join. When it is the
  **requests** that narrowed the fan-out into an order it cannot keep, the requests are
  refused and the declaration files: a refused request is still as if it had not been
  written.
- `concurrency: N` bounds how many of the fan-out's runs run at once. Running runs hold
  their slot; a failed dependency never frees its dependents, and the join over the
  fan-out is what reaches a person.
- `approved_subset: true` files only subjects a person approved, in an approval
  (`asop.approval/1`, [schema](schema/v1/approval.yaml)) that binds the output's
  **current digest**. A missing approval, an approval of an earlier digest, or one
  naming a subject the output does not contain refuses the hand-off (`approval`).
  Approving a task without its prerequisite is refused as `order`.

`requires` gains, by the same closed-list rule:

- `human_approved` takes an optional `binds_digest: true`: the approval holds only
  while its digest is the artefact's current one. A name alone fails closed.
- `chain_gate` — `{kind: chain_gate, id, gate}` — a deterministic gate the engine runs
  over the chain's run ledger; it holds only when it `passed`. This is chain-level done
  (§5.8): a terminal procedure requires it, and cannot start on a chain whose ledger
  says it is not done.

A hand-off may declare `preflight: true`: the engine runs the target's entry gates
(§3.8) against this run's would-be filings before the run is recorded done, so a
defect fails the producer that made it instead of surfacing hours later as the
consumer's failure. Activation refuses a preflight against a target with no entry
gates.

**Escalation.** `on_escalate` names the procedure that §5.4's second failure files. If it
is absent, the gate's own `escalate_to` applies.
- **Target.** It must contain a step a human role does, because an escalation reaches a
  person.
- **Inputs.** The mapping reads only the run's inputs and literals, since the run has not
  finished writing its outputs. Declared inputs the mapping leaves open are filled from
  the engine's escalation **context**: what failed, and why. Context keys the target does
  not declare are ignored. A declared input that neither the mapping nor the context
  supplies is refused (`inputs`).
- **Subject.** The engine chooses it, never the run. By convention it is
  `<asop_id>/<run subject>/<step>`.
- **The run continues.** The escalated run is not cancelled. Once the person answers and
  the run completes, its hand-offs fire like any other run's.

**Engine duties.** These stay out of the artefact, because they are the same for every
flow:
- **Starting a chain.** The first procedure is filed by `run` (§5.1), as always.
- **Concurrency and budget:** slots, waves and spend caps.
- **Staleness.** The engine records each run's parent run and input hashes. When an
  input changes, it re-files downstream as the next attempt.
- **Retries.** §5.4 already fixes the policy: one repair, then a person. It is not
  declared per ASOP.
- **Recording.** Every hand-off decision is ledgered with its parent run: filed,
  duplicate, waiting or refused, with each refusal's `toward`. A join's state is
  computed from that record, which is what keeps it from reading a refusal as an empty
  result. A filed join is also ledgered with the natural keys it waited on. Its inputs
  come from the parent, so this is the only lineage that shows it depends on those runs,
  and staleness needs it.
- **Re-evaluating** waiting joins and `requires` whenever a run in the chain finishes.
- **Escalation context** *(v3.6)* SHOULD carry `fault_class` (§5.4) and `audience`: who
  the question is for. Most escalations in a long chain observed in practice were about
  the machine, not the work, and an escalation that does not say so reaches the wrong
  person.
- **Liveness.** A blocked join, or a run whose `requires` have not held within the
  engine's bound, is surfaced to a person. The bound is engine configuration.

**Compatibility.** The record schema is closed (`additionalProperties: false`), so a v3.4
reader **refuses** a record that declares hand-offs. That is deliberate: a reader that
dropped the hand-offs it did not understand would run the procedure as a dead end. Adopt
`asop-spec` 0.5.0 before accepting chained records. A v3.4 record is a valid v3.5 record.
The same holds one version on *(v3.6)*: a record declaring `entry_gates`, `rework`, or
one of §3.7's v3.6 fields is refused by a 0.5.0 reader, which would otherwise run the
procedure without its precondition or its loop bound. Adopt `asop-spec` 0.6.0 before
accepting them. A v3.5 record is a valid v3.6 record, and means the same thing.

The reference implementation is `asop.chain`. Every function is pure:
- `validate_chain_fields` checks one record.
- `validate_handoff_targets` checks across records at activation, given a resolver,
  including acyclicity.
- `plan_handoffs` makes the filing decisions.
- `plan_escalation` makes the escalation filing.
- `join_state` decides whether a waiting join is `wait`, `ready` or `blocked`.
- `plan_join` files a join once it is ready.
- `requires_satisfied` makes the start decision.
- *(v3.6)* `fanout_startable` decides which runs of an ordered fan-out may start;
  `plan_entry` and `preflight_checks` are §3.8's; `plan_rework` is §3.9's;
  `asop.manifest` is §3.10's; `asop.journal` is §5.6–§5.8's.

All of them are pinned by the conformance vectors in `chain-record.yaml`, `handoff.yaml`,
`join.yaml` and `requires.yaml`.

### 3.8 Entry gates — the hand-off gate and the readiness gate *(v3.6)*

A step's gate checks the step's own output. In a chain, the failures that cost most
were the ones where a producer's output had the shape its own gate checked and not the
shape its consumer needed: a plan listed as environment needs the objects its tasks
would create, and the defect surfaced as a build failure hours later, then as an
escalation. v3.5's declared `outputs` named what flows down a chain; nothing checked it
from the consumer's side.

```yaml
entry_gates:
  - id: task-buildable
    over: [task]                         # declared inputs only
    gate: {kind: deterministic, check: "lint-task", cannot_evaluate_exit: 3}
  - id: source-ready                     # a readiness gate: same shape, over a source
    over: [source]
    gate: {kind: deterministic, check: "check-source --final --approver --no-open-decisions",
           cannot_evaluate_exit: 3}
```

- An entry gate is **deterministic**, reads only **declared inputs**, and is run by the
  engine before a filed run may start. It is never a step and never charged to the
  consumer: a run that did not start did not fail.
- The decision (`plan_entry`) is `start` when every entry gate passed, `wait` while one
  has not run, and otherwise `not_ready`:
  - over an input a **predecessor** handed on, the producer is **re-filed** as its next
    attempt. A `cannot_evaluate` charges nobody; a `failed` is charged to the
    **producer** (§5.4), whose output broke the contract;
  - over a **source** — an input supplied at chain start — there is no producer in the
    chain, so the run is **held** for a person (`source_not_ready`, `source_rejected`).
    That is the readiness gate: "the source document is final, names its approver, and
    has no open decisions" is a deterministic check over the document, evaluated
    before the procedure may start.
- Dependents are never released by an entry gate that did not pass.
- **Readiness is recorded and has lineage.** The engine records each entry gate's
  verdict with the digests of the inputs it read, and a change of one makes the verdict
  stale (§5.7). A source that changes after it was found ready is re-evaluated, and
  runs built on the old one are stale.
- **The producer's obligation.** A producer whose hand-off declares `preflight: true`
  runs the consumer's entry gates as part of finishing (§3.7, `preflight_checks`).

### 3.9 Rework — declared, bounded loops *(v3.6)*

v3.5 refuses hand-off cycles and allows a loop only through an escalation, because a
person bounds it. Real chains loop without a person, for good reasons: tests that do
not bite go back to the step that wrote them; a review's findings go back to the
procedure that owns them. Those loops lived in engine code, which is the portability
failure §3.7 was written to fix. A `rework` edge declares one, and bounds it.

```yaml
rework:
  - id: rewrite-tests                    # back to an earlier STEP of this procedure
    to: {step: 1}
    on_step_failure: 3                   # when step 3's gate fails
    max_rounds: 2
    consumes_attempt: false
  - id: replan                           # back to the upstream PROCEDURE that owns a finding
    to: {asop_id: plan, versions: ">=1"}
    on_outcomes: [PLAN_FINDINGS]         # a declared outcome no hand-off fires on
    findings: findings                   # a declared output, carried as prior findings
    max_rounds: 2
    consumes_attempt: false
    tier_bump: true
```

- **A step edge** re-executes steps `to.step` through `on_step_failure` (which must be
  later) instead of repairing the failing step alone. A failing step re-running itself
  is §5.4's repair, not rework.
- **A procedure edge** fires when this run finishes with one of `on_outcomes`, which
  must be declared and must not be an outcome any hand-off fires on: a run that sends
  its work back does not also send it forward. It re-files the **owner** — the upstream
  run of the target in this chain, which the engine finds by lineage — as its next
  attempt with the **same inputs**, plus `findings` as **prior findings**: run context
  outside its declared inputs, a scalar reference like every value handed on. This is
  the one mechanism for "re-file the exhausted owner with the review's findings"; there
  is no second one.
- **Bounded.** `max_rounds` is 1..3, counted per edge per chain. A spent edge
  **escalates** (§5.4); it never runs another round. `consumes_attempt` is required and
  has no default: whether a round spends the repair budget is the author's decision.
- `tier_bump: true` lets the engine bind the re-run's roles to a more capable binding.
  What "more capable" means is a binding, so it is local (§7).
- **One trigger, one edge.** Two edges on one outcome, or on one failing step, are
  refused: which owner is re-filed must not depend on order.
- **Upstream only.** At activation, a procedure edge's target must (transitively) hand
  off to this record. A rework edge to anything else is a hand-off by another name, and
  would escape the cycle ban.
- **Not part of the cycle check.** Rework edges are excluded from §3.7's acyclicity
  check, as escalation edges are: `max_rounds` bounds them, as a person bounds an
  escalation.
- Refusals are `rework_refused:<rule>`: `undeclared`, `version`, `findings`, `owner`.

The reference is `plan_rework`; the vectors are `rework.yaml`.

### 3.10 The chain manifest and adoption *(v3.6)*

v3.5 pins each run, and lets a hand-off name a version **range** resolved at filing. A
chain that runs for days can then file its first procedures under one version and its
last under another — and a gate's meaning moves with more than the step text. A long
chain observed in practice moved under three clocks at once: its procedures, the
checker its deterministic gates call, and the engine's own orchestration rules. A step
pin without a checker pin is not a pin.

**A chain manifest** (`asop.chain-manifest/1`, [schema](schema/v1/chain-manifest.yaml))
is written when a chain starts:

```yaml
{schema: asop.chain-manifest/1, chain: C1,
 start: {asop_id: intake, version: 1},
 members: [{asop_id: extract, version: 2, digest: "sha256:…"}, {asop_id: intake, version: 1, digest: "sha256:…"}],
 components: [{name: checker, version: "29", digest: "sha256:…"}, {name: rules, digest: "sha256:…"}]}
```

- **Resolved once.** At chain start, every procedure the chain can reach — through
  hand-offs, joins, its escalation target and its rework targets — resolves to the
  ACTIVE version inside its reference, and is pinned. One version per procedure. A
  chain that cannot be pinned whole is refused **at its start**, not at a filing hours
  later.
- **Resolved from the pin.** For the rest of the chain, every reference resolves to
  the pinned version, and only if that version satisfies the reference; there is no
  fallback to what is active now. In the reference implementation this is just a
  resolver (`manifest_resolver`): `plan_handoffs`, `plan_join`, `plan_escalation` and
  `plan_rework` are unchanged.
- **Components** are whatever else gives the gates their meaning — a checker, a rules
  file — by digest. Their meaning is local; that they are pinned is not.
- **Adoption.** A chain moves to another manifest only by an **adoption**
  (`asop.adoption/1`, [schema](schema/v1/adoption.yaml)) that a declared human records:
  the two manifests' digests, and the impact report — one row per member and
  component, `unchanged`, `changed`, `added` or `removed` — **computed** from the two
  manifests (`adoption_impact`), never typed. In-flight runs finish under the version
  they started under (§2.1); runs filed after the adoption resolve from the new
  manifest. Using an unpinned version without one is `adoption_required`.
- **Digests** are defined below, and since v3.6.1 they are normative: two
  implementations given the same record MUST produce the same string.

**The digest** *(normative, v3.6.1)*. v3.6 said "a member's digest is the store's",
which let every store invent its own and made a manifest written by one engine
unverifiable by another. The rule is now the one the first store to verify manifests
already applied, byte for byte, so no digest it recorded changes:

1. **Input.** A member digest covers the record's **validated body**: the
   store-assigned fields — `asop_id`, `version`, `status`, `author`, `author_kind`,
   `superseded_by`, `created_at` — are removed, and the rest is normalised by the
   record contract (`validate_asop`, §3.1–§3.7). Normalisation is what makes "absent"
   and "`null`" the same for every optional field, strips surrounding whitespace from
   text, folds tags to lower case, fills `after`, and writes the normalised gate with
   **every** gate field, `null` where unset, plus `schema_version`. Normalisation is
   idempotent, so a validated body digests as the document it came from. A record the
   contract refuses has no digest, and cannot be pinned.
2. **Serialization** (`canonical_json`):
   - object keys sorted by Unicode **code point** — not by UTF-16 code unit, which is
     where this departs from RFC 8785, for keys outside the Basic Multilingual Plane;
   - no whitespace: `,` between items, `:` between key and value;
   - strings encoded as UTF-8, unescaped except `"` → `\"`, `\` → `\\`, and
     U+0000–U+001F: `\b \f \n \r \t` in short form, the rest as `\u00xx` with
     lowercase hex. `/`, U+007F and everything above U+001F are written as themselves;
   - **no Unicode normalization**: code points are hashed as given, so a decomposed
     `é` is a different string, and a different digest, from a precomposed one;
   - integers in decimal with no leading zeros, no `+` and no exponent; `true`,
     `false` and `null` as written;
   - a **float** is refused, and so is a string with a lone surrogate: neither has a
     form two languages agree on.
3. **Hash and encoding.** SHA-256 over those bytes, written `sha256:` followed by 64
   **lowercase** hex digits (`digest_of`; for a record, `body_digest`).

A manifest's own digest is the same serialization and hash over the validated
manifest. The vectors in `digest.yaml` pin a document and its digest string, and the
cases that must and must not change it.

**What this binds in later versions.** Because the normalised gate writes every gate
field, a later version that added a gate field to the normalised output would change
the digest of every record, including the ones that do not use it, and strand every
pinned chain. A later version therefore MUST NOT change the normalised body of a record
that uses none of its new fields. The three gate fields v3.6 added (`default`,
`cannot_evaluate_exit`, `min_cases`) are part of the v3.6.1 digest input as `null`; a
v3.5 record's digest under v3.6.1 includes them.

**Opt-in, deliberately.** An engine that starts chains without a manifest is the v3.5
engine it always was, resolving ranges at filing, and conforms to v3.6; it does not get
to claim it pins chains. Making the manifest mandatory would make every v3.5 engine
non-conforming, which is not an additive change; it is a candidate for v4 (§11.10).

---

## 4. Lifecycle of the artefact

```
create ──▶ DRAFT ──activate──▶ ACTIVE ──(a later version activates)──▶ SUPERSEDED
                                  │
                                  └──retire──▶ RETIRED
```

- **DRAFT** — being written. Cannot be run. Every field may change.
- **ACTIVE** — exactly one version of an `asop_id` is active at a time. Immutable.
  Runs file from it.
- **SUPERSEDED** — replaced by a later active version. Kept forever: runs pinned to it
  must stay resolvable or their outcomes become unreadable.
- **RETIRED** — withdrawn with no successor. Existing runs finish under their pin; no
  new runs file. Human-only: an agent may not withdraw a procedure, in the same spirit
  as the ratchet rule.

`revise` never edits a version. It creates a new DRAFT from an existing version plus a
change set, and records who made it and whether they were human. Activation of a
revision is policed when the reviser is an agent (§6.4).

---

## 5. Lifecycle of a run

A **run** is one execution of one ASOP version against concrete inputs.

### 5.1 Filing

`run` takes `(asop_id, inputs)` and files a tree:

```
parent bead        pins (asop_id, version); carries the run's inputs
  ├─ step 1 bead   pins (asop_id, version, 1); gate from step 1; blocked_by: []
  ├─ step 2 bead   pins (…, 2); blocked_by: [step 1]
  ├─ …
  └─ step 5 bead   pins (…, 5); blocked_by: [step 4]
```

Rules, all enforced at filing:

- The version must be ACTIVE. A draft refuses (`sop_refused`): generating work from an
  unactivated procedure hands somebody a half-written instruction with the authority of
  a published one.
- Every declared input is supplied, or the run refuses (`inputs_missing`).
- Every role resolves to a binding satisfying every constraint, or the run refuses
  (`role_unbound`, `constraint_unsatisfiable`).
- Each step bead carries a **copy** of its step's text and gate, pinned. The
  plan-vs-actual review reads the words the executor was actually handed, even after
  the procedure moves on.
- The child is written into the parent's `blocked_by` in the same lock that files it.
  A parent cannot close while a child is open.
- Decomposition bounds apply to the tree as filed (`decomposition_bound`).

### 5.2 Executing a step

Any harness may claim a step bead its binding is eligible for. Claims are fenced leases:
a claim carries an attempt number, and a report against a stale attempt is refused
(`not_the_holder`). The executor reads the step's text, the run's inputs, and the
outputs of the steps it is `after`.

Completion is **requested, not declared**. The executor reports a result; the store
that owns the bead runs the gate at its own status flip:

| gate outcome | bead status | releases dependents? |
|---|---|---|
| passed | `done` | yes |
| `deterministic` / `judged` failed | `verify_failed` | **no** |
| `human` parked | `awaiting_verify` | **no** |
| could not evaluate *(v3.6, §3.3)* | `cannot_evaluate` — not charged; whatever could not be evaluated is re-filed or held (§3.8) | **no** |
| park clock expired | per `on_timeout`: `done` / `verify_failed` / escalated / `done` on the declared default *(v3.6)* | as above |

Neither `verify_failed` nor `awaiting_verify` releases anything downstream. This kills
the momentarily-done race.

### 5.3 Attestation

Where the store and the plane are different trust domains, the executing domain submits
an **attestation** — `check`, `exit_status`, `environment`, `at`, `submitted_by` — and
the plane verifies and stores the claim. It does not run commands. A `judged` or
`human` gate's attestation names the judge or the person; a `deterministic` gate's
attestation names the executor, and every reader of it knows that is a claim.

**Naming is not authenticating.** A `judged` or `human` attestation's `submitted_by` is
checked against the operator's declared registry for that role (§6.1's adjudicator,
§6.4's human, or the gate's own `verifier`) before it is accepted, never trusted on the
strength of the string alone. An identity the registry cannot resolve is
`unauthenticated` (§10), whether or not it differs from the executor's — three
independent implementations of this contract shipped "differs from the executor" as the
whole check and called it verification; it is a mistake detector, not a credential. An
unset or absent registry authenticates nobody, not everybody: the failure mode is closed,
matching the deterministic gate's own posture (§9) — a check that cannot run is not
silently a pass.

**A name is not a verdict.** A `judged` or `human` attestation carries a `verdict` — which
part of the gate's `check` (or which rung of its `checks`, §2.2) the attesting party found
true or false, in their own words. An attestation with an identity and a timestamp but no
verdict is `attestation_invalid` (§10): it proves a party was named, not that a party
looked. The ladder rule already says evidence that cannot name which rung it climbed is
not evidence about the ladder; this is the same requirement one level up — evidence that
cannot say what it found is not evidence of having judged.

The wire shape is `verdict: {passed: boolean, reason: nonblank string}`.
It applies to the attestation's `check` and, for a ladder, its `stage`; these
must match the gate as usual. `reason` explains the finding about that check
in the attesting party's own words. Missing or malformed verdicts on `judged`
and `human` gates are `attestation_invalid`. Deterministic attestations may
omit a verdict; if supplied, it must have the same shape and semantics.

Valid evidence passes only when `exit_status == 0` and any supplied verdict
has `passed: true`. A negative verdict with exit status zero is stored as
valid evidence of failure; a positive verdict never overrides a nonzero exit
status. Thus contradictory signals fail closed, and every ladder rung must
satisfy both conditions before the gate releases dependents. Validators check
the verdict's structure and its attachment to the declared check; they cannot
prove that the prose is truthful or that judgment was actually exercised.

### 5.4 Failure and repair

A failed step **keeps its failed status** — still blocking everything after it — until
its own gate is re-run and passes. Repair is a sibling **repair bead** (`repairs:
<step bead>`), filed beside, never beneath. It does not count against the parent's
budget: the red original is what holds the parent open. First failure files one repair
bead; second failure escalates to a human; there is no third autonomous attempt.

**What counts** *(v3.6)*. The budget counts the executor's failures, and only those:

- a `failed` journal entry whose `fault_class` is `work`, or that has none, counts —
  an engine that classifies nothing charges every failure, exactly as before;
- `environment`, `checker`, `handoff` and `decision` faults do not: the machine could
  not run the work, the checker judged it wrongly, an input arrived broken, or only a
  person can answer. They go to whoever owns them (the escalation's `audience`), not
  into the executor's budget;
- a crash is not an attempt (§5.6), and `cannot_evaluate` is not a failure (§3.3).

The fault classes are a closed list: `work`, `environment`, `checker`, `handoff`,
`decision` (`asop.journal.FAULT_CLASSES`). A rework round (§3.9) counts when its edge
declares `consumes_attempt: true`.

### 5.5 Completion

The run's parent bead closes when every step bead is `done`. At that moment:

- a **plan-vs-actual** review is written for the run, from the per-step reviews;
- the run's outcome is recorded against `(asop_id, version)` and each step's against
  `(asop_id, version, step)`;
- open adjudications on the run are queued for the lessons pass (§6);
- the ASOP's hand-offs are evaluated against the recorded outcome and outputs, and
  whatever they file is ledgered with this run as its parent (§3.7).

### 5.6 Journal and resume *(v3.6)*

§6.2 already says the plan-vs-actual review is written "at the moment that step
completes". A long chain observed in practice wrote its steps to the ledger in one batch
when the run ended, and three things followed: step timings were artefacts of the flush,
a value one step wrote for the next was missing when the next step looked, and an
executor that died mid-run left no entry, so its crash read as a failure and was
charged as one.

- **Append per step.** The engine appends a journal entry (`asop.step-journal/1`,
  [schema](schema/v1/step-journal.yaml)) when a step starts and when it ends — `passed`,
  `failed`, `cannot_evaluate`, `parked`, `done_on_default` or `escalated` — with the
  digests of the outputs it wrote. Never in a batch at the end of the run.
- **Resume from the journal alone.** After a restart, each step's **last** entry decides
  it (`resume_point`). A step whose last entry is `started` was interrupted: it is
  re-run under a new lease. The probe needs no transcript and no executor memory; if it
  did, a run would be resumable only by the process that died.
- **A crash re-file is not an attempt.** The lease attempt (§5.2) increments, because
  that is fencing; §5.4's budget does not, because an interrupted step reached no gate
  (`charged_failures`).

### 5.7 Staleness — answers, sources and approvals are inputs *(v3.6)*

§3.7 made staleness an engine duty and left its rule implicit. v3.6 states it once, so
two engines agree on it: **a run is stale when it read an artefact that has since
changed, at a different digest** (`stale_runs`). The engine records what each run read,
with digests, and a stale run is re-filed as its next attempt; a re-attempted parent
files its fan-out afresh, which is how staleness reaches the runs below it.

The one rule covers four cases:

- a predecessor's **output** that was rewritten;
- a **source** whose readiness was evaluated (§3.8): the verdict is lineage, and a
  changed source makes it stale;
- an **approval** (§3.7): it binds a digest, and an artefact that changed after it was
  approved was not approved;
- a person's **answer**. An answer to an escalation or a question is an input artefact
  of everything derived from it. A run built while the question was open — on its
  declared default (§3.3) or on nothing — records the question with digest `null`.
  Writing the answer changes it, so every run built on the open question is stale and
  is re-filed. An answer that matches what was built on changes nothing. An answer is
  an input, not a comment.

### 5.8 The chain outcome and chain-level done *(v3.6)*

Outcomes attach to versions (§2.1), and a version is a procedure's. The chain is what an
owner pays for and approves, and nothing recorded its outcome: a long chain observed in
practice ran for most of a day without finishing a single task, and that was visible
only to someone who added the ledger up by hand.

- **Chain-level done** is a `chain_gate` requirement (§3.7) on a terminal procedure: a
  deterministic gate over the run ledger — every requirement traced to evidence, say —
  that holds only when it `passed`.
- **The chain outcome record** (`asop.chain-outcome/1`,
  [schema](schema/v1/chain-outcome.yaml)) is computed from the ledger
  (`chain_outcome`), never typed: wall time, spend, human wait, runs and superseded
  runs, failures and escalations by fault class, escalations by audience, the member
  versions used, and the verdict. `done` when every latest attempt is done and the chain
  gate, if any, passed; `failed` when a latest attempt or the chain gate failed; `open`
  otherwise. A procedure run at more than one version is listed in `mixed_versions` —
  the condition a manifest prevents, and one an outcome row must never average away.
- It is the chain's row in `outcomes_by_version` (§6.3), keyed by its manifest digest.

---

## 6. Self-improvement

### 6.1 Divergence and adjudication

*Divergence* is the phenomenon — execution departed from the step. *Adjudication* is the
record that judges it: `good` or `bad`, with the adjudicator's identity and pointed
evidence. The adjudicator **must not be the executor** (`adjudication_self`), must
adjudicate an executed step (`adjudication_unexecuted`), and adjudicates once
(`adjudication_exists`).

In a single-operator organisation the adjudicator is the operator, or a route the
operator has **declared** as an adjudicator and that is distinct from the executor's.
Declared, never inferred: a registry with no declared adjudicator makes the operator the
only one. A harness where the only possible adjudicator is the executor's own route has
no self-improvement loop, and its ASOPs are documentation.

"Declared" names an invariant, not a mechanism — the mechanism is §5.3's: an adjudication
is `unauthenticated` (§10) unless the adjudicator's claimed identity resolves against the
operator's declared registry, checked at adjudication time, not assumed from the fact
that a name was given. A harness that checks only that the claimed name differs from the
executor's has implemented the invariant's shadow, not the invariant — the distinction
that matters, and the one every early implementation of this contract skipped.

### 6.2 Plan-vs-actual

Written at the moment a step completes, comparing the step text the executor was handed
against what the executor reports having done. It is evidence for adjudication, not an
adjudication.

### 6.3 Lessons and proposals

`propose` is the cadence pass. It reads adjudications nobody has proposed yet and drafts
the next version: good ones into the step's `proposals`, bad ones into the step's
`common_mistakes`. It may also propose **structural** changes — a step split, a step
added, an ordering changed — when the evidence is the same divergence recurring at the
same boundary. A draft, never an activation.

`outcomes_by_version` is the other half: per version and per step, counts of `done`,
`verify_failed`, escalations, repairs, mean park time. Whether a revision helped is a
comparison of two rows. *(v3.6)* It gains a **chain row** per manifest digest — the
chain outcome record of §5.8 — and its counts read fault classes (§5.4), so a version
is not blamed for its environment. A defect class that recurs at a hand-off is evidence
for an entry gate or a preflight (§3.8), which is a structural proposal like a step
split.

### 6.4 Revision policy

When the reviser is an agent, three rules apply, computable because versions are
immutable and each records who authored it:

1. **Protected tags freeze a step against agents.** A step tagged `money` or
   `irreversible` (operator-extensible, never operator-reducible) may not be changed by
   an agent, nor may an agent add or remove such a tag.
2. **Classification ratchets toward human, never away.** An agent may move a role or a
   gate to `human`; only a human may move it back.
3. **No undoing a human.** An agent may not move any field into a state a human moved it
   away from, until a human moves it back.

Who is human is **declared by the operator**, never inferred from a name. An undeclared
registry polices everyone. A human reviser is bound by none of these. Activation is
policed the same way, or the policy has a door beside it.

### 6.5 Promotion — where new ASOPs come from

Not every goal needs an ASOP. A harness's planner decomposes one-off goals into bead
trees ad hoc; that is the ordinary case and it stays. An ASOP is for a **recurring type
of task**.

`promote` takes a completed run tree — a planner's decomposition that worked, with its
gates and its adjudications — and drafts an ASOP from it: the beads become steps, the
executors become roles, the verify bead becomes the validator step. This is the front
door of the loop: the standard grows from evidence that a shape works, not from a blank
page.

In v3 a human opens that door. Promotion is refused when an active ASOP already covers
the run's `task_type`; the variant becomes a new version through `revise`. A later
version of this contract lets agents promote to a draft and lets a harness auto-promote
past a threshold it configures (§11.7) — activation stays human either way.

---

## 7. Sharing across harnesses

What is **portable** — carried in the artefact, identical everywhere:

- the ASOP and its steps, gates, roles, constraints, bounds;
- the version pin and its semantics;
- the refusal vocabulary (§10) and the status vocabulary (§5.2);
- the adjudication record and the attestation record.

What is **local** — decided by each harness, never in the artefact:

- **role bindings** — which agent, model, route, or person fills each role;
- the work-unit store and its transport;
- how a `judged` route is chosen, provided it is distinct from the executor's;
- who the operator declares human;
- schedule, cost, and capacity.

A **plane** stores ASOPs, versions them, records outcomes per version, routes human
gates and adjudications, runs the lessons pass across every harness that reports to it,
and is otherwise advisory: it never executes a check and never blocks a harness.

A **harness** runs standalone against a local ASOP store with the same contract. In
`remote-owned` mode — the mode §11.8 was decided for, and the default when a plane is
configured without saying otherwise (§7.1) — **the plane owns the queue**: runs are filed on the plane with
bindings, and the harness pulls the step beads its bindings name, executes them, reports
and attests to the plane. It never files a run on the plane unprompted and never
publishes without a human-visible prompt. Its local store is the standalone path.

**The standalone store may be an embedded plane.** "A local ASOP store with the same
contract" is a requirement about behaviour, not about writing a second implementation of
it — and writing one is how implementations drift. A harness MAY satisfy its local store
by embedding a plane in-process, and doing so is the recommended shape: one lifecycle,
one set of gate semantics, one answer to a staged ladder.

### 7.1 Filing destination, declared not inferred

A harness is in exactly one **mode**, declared by the operator, never deduced from
whether a remote answered:

| mode | where runs are filed | who owns them |
|---|---|---|
| `local-only` | the local store | the local store |
| `remote-owned` | the configured plane | the plane (§11.8, unchanged) |
| `local-owned` | the local store | the local store; the plane receives a journal |

**`remote-owned` is the default** when a plane is configured and the operator says
nothing else; `local-only` is the default when none is. Defaults exist so that an
operator who has not thought about modes still gets the behaviour §11.8 describes, which
is the one the contract has always had.

Reachability never changes the mode. A `remote-owned` harness that cannot reach its
plane files nothing and says so; a `local-owned` harness files locally whether or not
the plane is up. Inferring the destination from a health check is how one run comes to
exist in two places, which is the failure §11.8 refused.

### 7.2 One run, one owner

Ownership is recorded on the run when it is filed and no verb moves it. The store that
owns a run is the only one that may flip the status of its beads; any other store
attempting it is refused **`not_the_owner`** (§10) — a distinct code from
`not_the_holder`, which is about a lease, not about authority.

A **journal** is what a non-owning plane receives: it may read, count, run the lessons
pass and route human gates, and it may not complete a step. To be useful to the lessons
pass a journal entry carries the pinned `(asop_id, version, step)`, the step text the
executor was handed, the plan-vs-actual review, any divergence and adjudication records,
and the terminal status with its evidence — not a bare outcome.

Journal application is **idempotent on run and bead identity**: an entry already applied
is accepted and changes nothing. Reconnection after a partition is at-least-once
delivery, and `outcomes_by_version` must not count a run twice because it was delivered
twice.

### 7.3 Answering a gate you do not own

A plane may **route** a human or judged gate for a run it does not own — that is what a
coordination layer is for. It may not **answer** it. The verdict returns to the owning
store as an attestation (§5.3), and the owning store re-checks the pinned bead, its
gate, the registry and the terminal status before it flips anything. Enforcement stays
in the domain that owns the bead, exactly as §9 says.

Routing **fails open to the owner**: if the plane that would route a gate is
unreachable, the owning store routes it itself. An embedded plane is a whole plane and
can. Without this, a remote outage silently converts a human gate into an `on_timeout`
resolution — decided by omission, which §9 permits only when a person was actually
asked.

The reachability attempt is **bounded**, inheriting §9's rule that expiry is failure and
never a hang: a router that cannot be reached within its timeout is unreachable, and the
owner proceeds. A partition can therefore produce the same gate routed twice, and that
is accepted rather than prevented — **two prompts, never two completions**. Both answers
arrive at the owner as attestations (§7.3); the first to satisfy the gate resolves it and
the second finds a terminal bead and is refused `not_terminal`. Preventing the duplicate
prompt would need a lock across a partition, which is the thing a partition denies;
making the duplicate harmless costs nothing and holds without coordination.

### 7.4 Version identity across stores

A journalled run pins `(asop_id, version)`, and that pin means the ASOP the OWNING store
activated. Two stores may hold different procedures under one `asop_id`. A plane
receiving a journal entry for a version it cannot resolve stores it and counts it
separately; it does not fold those outcomes into its own version's row, and it does not
silently treat them as the same procedure. Whether an ASOP record itself should
replicate downward to embedded planes, and under whose activation, is **open** — see
§11.9.

A harness is **conforming** when it passes the vectors in
[`conformance/vectors/`](conformance/vectors/) against its own validators.

Each vector is a document and the outcome a conforming implementation must reach:
accepted, or refused with a named code. They are data, not code, so an
implementation in any language becomes conforming by reading the YAML, validating,
and comparing — no dependency on this package and no Python required. The
reference runner in [`conformance/runner/`](conformance/runner/) is about a
hundred lines, which is the intended size of the obligation.

A third file asks the question that actually gates work: given a gate and a set
of attestations, may the step be recorded as done? That is separate from whether
the evidence is well formed, because a FAILING attestation is a valid document —
refusing it would leave failure unrecordable — and an implementation conflating
the two records a red run as a green one.

Conformance is a claim about documents, deliberately. Same verbs, same refusals,
same statuses and same pin semantics remain the goal, but only what a vector can
check is what conformance currently asserts. The gaps are listed in
[`conformance/README.md`](conformance/README.md) rather than left for an adopter
to discover; a suite that hides its own coverage is the thing this section was
guilty of before these vectors existed.

---

## 8. Verbs

The contract, transport-neutral. Every verb names who may call it, its preconditions,
its effect, and the refusals it can return. HTTP and MCP encodings map onto these one
to one.

### 8.1 Authoring

| verb | caller | preconditions | effect | refusals |
|---|---|---|---|---|
| `sop_create(asop)` | human or agent | fields valid; not empty; bounds hold | new `asop_id` at v1, DRAFT | `sop_refused`, `decomposition_bound` |
| `sop_revise(asop_id, from_version, changes)` | human or agent | source version exists | new DRAFT version; records author, author_kind | `sop_refused`, `version_required`, `revision_policy:<rule>` |
| `sop_activate(asop_id, version)` | human, or agent under policy | version is DRAFT | version ACTIVE; previous active SUPERSEDED | `sop_refused`, `revision_policy:<rule>` |
| `sop_retire(asop_id)` | human | an active version exists | active version RETIRED; new runs refuse | `sop_refused`, `revision_policy:human_only` |
| `sop_get(asop_id, version?)` | any | — | the version, or the active one | — |
| `sop_list(filter?)` | any | — | ids with active version and status | — |
| `sop_history(asop_id)` | any | — | every version, oldest first | — |

### 8.2 Running

| verb | caller | preconditions | effect | refusals |
|---|---|---|---|---|
| `run(asop_id, inputs, version?)` | human or agent | version ACTIVE; inputs complete; roles bindable | files the tree (§5.1); returns run id | `sop_refused`, `inputs_missing`, `role_unbound`, `constraint_unsatisfiable`, `decomposition_bound` |
| `run_get(run_id)` | any | — | the tree with statuses and pins | `work_item_unknown` |
| `run_list(asop_id?, status?)` | any | — | runs, newest first | — |
| `drifted(run_id)` | any | — | whether the pinned version is no longer active | — |
| `handoff(run_id, requests?)` *(v3.5)* | the engine that owns the run | run complete | files what §3.7 decides; records each filing, duplicate and refusal with the run as parent | `handoff_refused:<rule>` |
| `chain_start(asop_id, inputs)` *(v3.6)* | human or agent | as `run`; every reachable member pinnable | pins the chain manifest (§3.10), then files the first run as `run` does | as `run`, `sop_refused` |
| `manifest_record(manifest)` *(v3.6.1)* | the engine | every member held, runnable, at the store's digest | stores the manifest; the first one recorded for a chain is the one it starts under | `manifest_invalid`, `manifest_member_unknown`, `manifest_digest_mismatch`, `contract_unsupported` |
| `chain_adopt(chain, manifest)` *(v3.6)* | a declared human | the chain has a manifest | records an adoption with the computed impact; later filings resolve from the new manifest | `revision_policy:human_only` (see §11.11), `chain_unknown`, `manifest_unknown`, `adoption_invalid`, `work_conflict`, `contract_unsupported` |
| `approve(artefact, digest, subset?)` *(v3.6.1)* | a declared human | — | records an `asop.approval/1`; append-only | `approval_invalid`, `contract_unsupported`; a non-human caller: §11.11 |
| `rework(run_id, trigger)` *(v3.6)* | the engine that owns the run | a declared edge matches | re-runs steps, re-files the owner, or escalates when the rounds are spent (§3.9) | `rework_refused:<rule>` |
| `answer(question, answer)` *(v3.6)* | a declared human | the question exists | records the answer as an artefact; everything built on the open question is stale (§5.7) | `unauthenticated` |
| `resume(run_id)` *(v3.6)* | the engine that owns the run | — | re-runs interrupted steps from the journal; not an attempt (§5.6) | `not_the_owner` |

### 8.3 Executing

| verb | caller | preconditions | effect | refusals |
|---|---|---|---|---|
| `work_claim(bead_id, ttl)` | a binding eligible for the step's role | bead ready; no live lease | fenced lease with attempt number | `work_conflict`, `capability_mismatch`, `bad_ttl` |
| `work_report(bead_id, attempt, result)` | the lease holder | attempt is current | store runs the gate at the flip (§5.2) | `not_the_holder`, `attempt_required`, `attestation_required` |
| `work_attest(bead_id, attestation)` | the executing domain | bead reported | plane verifies and stores the claim | `attestation_invalid`, `unauthenticated` |
| `verify_approve(bead_id)` / `verify_reject(bead_id, reason)` | the named `verifier` | bead is `awaiting_verify` | `done` / `verify_failed` | `not_terminal`, `unauthenticated` |
| `repair(bead_id)` | human or agent | bead is `verify_failed` | files a sibling repair bead (§5.4) | `decomposition_bound` |

### 8.4 Improving

| verb | caller | preconditions | effect | refusals |
|---|---|---|---|---|
| `adjudicate(bead_id, good\|bad, evidence)` | anyone but the executor | bead executed; none exists | adjudication recorded | `adjudication_self`, `adjudication_exists`, `adjudication_unexecuted`, `adjudication_invalid`, `unauthenticated` |
| `propose(asop_id?)` | human, or a scheduled pass | unproposed adjudications exist | drafts the next version (§6.3) | `sop_refused` |
| `outcomes(asop_id)` | any | — | per-version and per-step counts | — |
| `promote(run_id)` | human (v3) | run complete; no active ASOP for its `task_type` | drafts an ASOP from the tree (§6.5) | `decomposition_bound`, `sop_refused` |

---

## 9. Enforcement model

Unchanged from v2 Part III; restated per step.

**Trust domains.** Gate enforcement happens inside the domain that owns the bead — the
store that flips its status — never in a shared plane. A plane stays advisory and
verifies attestations.

**Identity.** A claimed adjudicator, verifier, or judge route is authenticated against
the operator's declared registry (§5.3, §6.1) before its attestation is accepted —
resolving a claim is the store's job, at the same choke point that flips a bead's status,
not a convention the caller is trusted to honour. A registry with nothing declared
authenticates nobody; there is no fallback that authenticates everybody.

**The registries have names.** Saying "the operator's declared registry" and stopping
was the same mistake one level up: three implementations read that sentence and invented
three answers. The declarations are, alongside the two §6.4 already names:

| declaration | variable | who it names |
|---|---|---|
| humans | `ASOP_HUMANS` | actors the revision policy treats as human (§6.4) |
| protected tags | `ASOP_PROTECTED_TAGS` | tags that freeze a step against agents (§6.4) |
| **verifiers** | **`ASOP_VERIFIERS`** | routes that may answer a `judged` or `human` gate (§5.3) |
| **adjudicators** | **`ASOP_ADJUDICATORS`** | routes that may adjudicate a divergence (§6.1) |

Comma-separated actor names, matched on exact spelling — the same format and the same
rule as `ASOP_HUMANS`, so an operator declaring who may verify does not have to learn a
second syntax to declare who may judge. `asop.revision` ships `verifiers_from_env`,
`adjudicators_from_env`, `resolves(actor, registry)` and `may_adjudicate(...)` so that
two implementations reading the same declaration cannot disagree about what it says.

**What an empty registry means, exactly** — because the loose reading of this is what
§6.1 and this section were caught disagreeing about. These registries declare **routes**,
not people. An empty registry therefore grants **no route** the authority; it does not
grant everyone the authority, and it does not withdraw it from the operator:

- **Verification.** An empty `ASOP_VERIFIERS` resolves nobody. A `judged` gate with no
  declared route cannot be answered by a route at all; it waits for a person, which is
  what a `human` gate already is.
- **Adjudication.** An empty `ASOP_ADJUDICATORS` leaves the operator as the only
  adjudicator — the reading §6.1 has always had. A declared human adjudicates *by being
  human*, not by also appearing in the adjudicator registry; the registry is how a
  **route** is opted in, and the human-only posture is the default it starts from.

So `resolves()` answers one question — did the operator declare this route — and
`may_adjudicate(actor, adjudicators, humans)` answers the one §6.1 actually asks, which
is that question **or** whether the actor is a declared human. An implementation that
uses the first where the second is meant will refuse its own operator.

**Authentication is the transport's, resolution is the store's.** These are two
questions and conflating them is how the permissive reading gets in. The transport
answers *who is calling* — `submitted_by` is set from the authenticated actor, never
copied from a body that claims otherwise. The store then answers *did the operator
declare them for this role*, at the choke point where it flips the status.

The **reference validator does neither**. It checks that a record is well formed and
that it attests to the gate it claims to; it is handed an already-authenticated
submitter and resolves no registries. Both questions are the integrator's, and a
conforming implementation is not excused from them by the validator accepting a
document — a valid attestation from an undeclared route is a valid document and an
unauthorised answer.

**Execution contract for `deterministic` checks.** Per gate, an implementation pins:
identity (which principal runs the check), environment (working directory and permitted
secrets — no ambient credentials), isolation (the check cannot rewrite the record it
verifies), timeout (mandatory; expiry is failure, never a hang), idempotency (re-running
a passed gate is safe).

**Liveness.** Every `human` gate declares `max_park_seconds` and `escalate_to`. Silence
past the deadline resolves by `on_timeout` — decided by omission is an outcome; an
ignored queue is not. A quarantine pass surfaces stalled gates on a digest.

**The re-verify invariant.** Repair never substitutes for the work it repairs (§5.4).

**Bounds.** §3.1. Recursion is how the bound is honoured, not how it is broken.

---

## 10. Refusal vocabulary

Refusals are the contract's error language: a stable code, a human sentence, and no
stack trace. Codes relevant to ASOPs, from `asop.refusals`, plus the ones v3 adds:

| code | meaning |
|---|---|
| `sop_refused` | the ASOP or version cannot be used as asked (draft run, empty record, bad field) |
| `version_required` | an operation that needs a version got only an id |
| `revision_policy:<rule>` | an agent revision broke `protected`, `ratchet` or `no-undo` |
| `decomposition_bound` | more than 7 children or deeper than 3 |
| `adjudication_self` / `_exists` / `_unexecuted` / `_invalid` | §6.1 |
| `work_conflict` / `not_the_holder` / `attempt_required` | fenced-lease rules |
| `capability_mismatch` | the caller's binding is not eligible for the step's role |
| `attestation_required` / `attestation_invalid` | §5.3 — invalid also covers a `judged`/`human` attestation with no `verdict`, or one not tied to the gate's `check` |
| **`not_the_owner`** *(v3.4)* | a store tried to flip a bead of a run another store owns (§7.2). Distinct from `not_the_holder`, which is about a lease rather than about authority |
| **`unauthenticated`** *(v3.2)* | a claimed adjudicator, verifier, or judge identity does not resolve against the operator's declared registry (§5.3, §6.1, §9) |
| `not_terminal` | a verdict on a bead that is not parked |
| `metadata_reserved` / `natural_key_reserved` | a caller wrote a plane-owned key |
| **`inputs_missing`** *(v3)* | a declared run input was not supplied |
| **`role_unbound`** *(v3)* | no binding for a role |
| **`constraint_unsatisfiable`** *(v3)* | every binding violates a `distinct` constraint |
| **`handoff_refused:<rule>`** *(v3.5)* | a finished run's hand-off or filing request was refused; the rule is one of §3.7's nine, or *(v3.6)* `order` or `approval` |
| **`rework_refused:<rule>`** *(v3.6)* | a declared rework edge could not be applied: `undeclared`, `version`, `findings` or `owner` (§3.9) |
| **`adoption_required`** *(v3.6)* | a chain run would use a version or component its manifest does not pin; moving needs a recorded adoption (§3.10) |
| **`manifest_invalid`** *(v3.6.1)* | the document is not a valid `asop.chain-manifest/1` |
| **`manifest_member_unknown`** *(v3.6.1)* | a member pins a version the store does not hold as a **runnable** version: absent, or only a DRAFT (§5.1) |
| **`manifest_digest_mismatch`** *(v3.6.1)* | a member's digest is not the one the store computes for that version (§3.10) |
| **`manifest_unknown`** *(v3.6.1)* | an impact or adoption names a target manifest not recorded for **that** chain |
| **`chain_unknown`** *(v3.6.1)* | no manifest has been recorded for the chain named |
| **`adoption_invalid`** *(v3.6.1)* | the record is not a valid `asop.adoption/1` of exactly this move: digests or impact other than the computed ones |
| **`approval_invalid`** *(v3.6.1)* | the document is not a valid `asop.approval/1` |
| **`contract_unsupported`** *(v3.6.1)* | the receiver's installed contract does not implement the record kind or version the request needs |

**Storing chain records** *(v3.6.1)*. The eight codes above are what a store returns
when it is asked to record a chain manifest, an adoption or an approval, or to resolve
one. They were first shipped by an implementation; the specification adopts them so
that a second implementation refuses the same request with the same code.

- **Effect.** Every one of them stores **nothing** and changes nothing. A refused
  manifest is not partly recorded, and a refused adoption leaves the chain on the
  manifest it was on.
- **Order.** A store checks in this order, and reports the first failure:
  `contract_unsupported`, then the record's own validity (`manifest_invalid`,
  `adoption_invalid`, `approval_invalid`), then what it refers to (`chain_unknown`
  before `manifest_unknown`; `manifest_member_unknown` before
  `manifest_digest_mismatch`, member by member in manifest order).
- **Retry.** Each is permanent for the same request. It succeeds only once the request,
  or the store's state, changes: a member is activated, a manifest is recorded, or the
  contract is upgraded. `work_conflict` is the one retryable refusal on these verbs,
  meaning the chain moved while the adoption was being recorded.
- **Remediation.** As everywhere in this contract, a refusal carries a `remediation`
  sentence that names the next action, written at the call site for the payload in hand.
  It is non-normative text.
- **Transport.** HTTP statuses are an encoding (§8), so none is normative here. The
  reference plane uses 400 for an invalid record, 404 for an unknown chain or target,
  409 for a member or pin conflict, and 501 for `contract_unsupported`.
- **One legacy use.** An implementation shipped `manifest_member_unknown` for an
  unrecorded **target** manifest as well. That case is `manifest_unknown`. Readers
  should accept both until the implementation migrates (`asop.refusals.LEGACY_USES`).
- **Not here: `unknown_field`.** It is a refusal from a plane's HTTP surface: a key a
  route does not take. It is not a record refusal; every record already refuses its own
  unknown fields under its own code (`sop_refused`, `gate_invalid`,
  `attestation_invalid`, and the codes above). So it is catalogued in `asop.refusals`
  with the plane's other transport codes, and it is not part of this vocabulary.

---

## 11. Open questions for this review

1. **Naming.** ~~Open~~ **DECIDED 2026-09-04: container = `ASOP`, unit = `Step`, check =
   `gate`.** `asop.sop.SOP` is renamed to `Step`. Grounded in doctrine: the US Army's
   Training & Evaluation Outline decomposes a *task* (conditions, standards) into
   *performance steps*, each with *performance measures* rated GO/NO-GO — a one-to-one
   map onto ASOP / Step / gate. "Step" is also the unit in ISO 9001 work instructions,
   fire-service SOPs and police procedures; aviation's "item" is a checklist line, not an
   activity with an owner and a proof.
2. **Ordering.** **DECIDED 2026-09-04: ship `after` in v3.** Linear is the default
   (`after: [n-1]`); a Step may declare `after` explicitly to run in parallel with
   siblings, and the tree carries it as `blocked_by`. Validated at `sop_create`: a
   dangling reference or a cycle is `sop_refused`. Width is bounded by the step count.
3. **`retired`.** **DECIDED 2026-09-04: add the state.** `sop_retire` is human-only,
   moves the active version to `retired`, refuses new runs (`sop_refused`), lets
   in-flight runs finish under their pin, and keeps the record forever like
   `superseded`. The Army rescinds SOPs and ISO obsoletes documents; both keep the
   record.
4. **`next_sop`.** **DECIDED 2026-09-04: dropped.** Composition inside the contract is
   nesting (§3.5) — pinned, gated, bounded. Anything above that — sequencing one ASOP
   after another, choosing which procedure a situation calls for, a process of
   processes — is **orchestration, and orchestration belongs to the harness**, not to
   the artefact. The contract describes a procedure; the runtime decides when to run
   one. (mabidoli: "we may need a higher orchestration layer, but that is delegated to
   the harness that is using the ASOPs.")

   **REVERSED 2026-10-10 (v3.5), by the same owner, on evidence.** The first real
   chained execution put the orchestration layer exactly where this decision said: in
   the harness. Its flow ended up in one engine's rules file and driver, outside every
   ASOP. A plane could not share a procedure's place in the flow, and a second engine
   could only copy it. Of three options, the owner chose C over the alternatives:
   - **A, fixed successors only.** It cannot fan out a count known only at run time.
   - **B, run-time requests only.** A misbehaving run could file anything, and nothing
     would check it.
   - **C, chosen.** The ASOP declares where it **may** hand off; the run chooses which
     subjects; the engine refuses anything undeclared (§3.7).

   What survives of the original decision: the ASOP still executes nothing, and choosing
   which procedure *starts* a chain is still the harness's. `next_sop` stays dropped.
   A hand-off is declared once, on the ASOP, not as a link on a step.
5. **Run inputs.** **DECIDED 2026-09-04: names plus free descriptions in v3.** The
   contract checks presence (`inputs_missing`); what an input *is* is between the
   author and the harness. `kind` is reserved on the input record — optional and
   unvalidated — so that when a second harness disagrees with the first about the
   shape of an input, the disagreement can be named without a schema change. Kinds
   get standardised from that evidence, not guessed now.
6. **Who adjudicates in a one-person organisation.** **DECIDED 2026-09-04: a declared
   route may adjudicate.** The contract forbids exactly one thing — self-adjudication —
   and keeps it. A route may adjudicate an agent's step when it is distinct from the
   executor's route **and the operator declared it as an adjudicator**; declared, never
   inferred, the same way verifiers are. A registry with no declared adjudicator makes
   the operator the only one — the human-only posture is the default, the route is the
   opt-in. Activation stays human regardless: an agent-adjudicated divergence becomes
   a proposal, and a person activates the version it produces.
8. **Queue ownership when connected.** **DECIDED 2026-09-04: the plane owns the
   queue.** A run is filed on the plane with bindings; a harness pulls the step beads
   its bindings name, executes, reports and attests. The alternative — the harness
   files locally and reports outcomes upward — needs the plane to accept outcomes for
   runs it did not file and leaves two queues to agree about one run. The wire keeps
   the verbs it has (`agentco pull` / `agentco report`) as a protocol name, whatever
   the binary is called.

   **Qualified 2026-09-10 (v3.4).** The decision stands for every run the plane files,
   which is what it was about. It was reasoned in a world with two topologies —
   standalone, or connected-and-the-plane-owns-everything — and its objection to the
   alternative was precise: accepting outcomes for runs it did not file "leaves two
   queues to agree about one run". §7 now answers that objection rather than reopening
   it. Ownership is fixed at filing and never moves, so two queues never hold one run;
   a locally-filed run is owned locally and journalled upward, and the plane may not
   complete a step of a run it does not own. What changes is only that "connected" no
   longer implies "everything was filed there".

7. **Promotion authority.** **DECIDED 2026-09-04: humans only in v3.** `promote` is a
   human verb. It drafts an ASOP from a run tree, and is refused when an active ASOP
   already covers the same `task_type` — the path for a variant is a new *version* via
   `revise`, not a second draft.

   **Reserved for a later version:** agents may `promote` to a draft, and a harness may
   set a threshold — *N* clean completions with no bad adjudication — that auto-promotes
   a new ASOP, or a new version of an existing one, for a human to validate. The
   threshold is a harness number, not a contract number. The line that does not move:
   agents may draft, only humans activate.

---

9. **Does an ASOP record replicate downward?** **OPEN (v3.4).** §7.4 says a plane
   receiving a journal entry for a version it cannot resolve counts it separately rather
   than folding it in. That is safe but partial: it leaves an operator running many
   embedded planes with no way to distribute one procedure to all of them except by
   copying it, and copies drift, which is what this contract exists to stop. The
   candidates are replication with the remote as the activation authority, or pull-on-pin
   where an embedded plane fetches the version a journalled run names. Both need the
   revision policy (§6.4) to hold across the hop, and neither is decided.

10. **Candidates for v4.** **OPEN (v3.6).** v3.6 is additive, so each of these was
    left out because it would change what an existing record, gate or engine means. The
    list, with reasons, is in [`MIGRATION.md`](MIGRATION.md#candidates-for-v4): making the
    chain manifest mandatory (so a v3.5 engine resolving at filing stops conforming);
    `cannot_evaluate` for every deterministic gate's exit 3 without a declaration;
    judged entry gates; normative `inputs[].kind` / `outputs[].kind`; declared
    per-step capabilities and environments; and run-instance identity across machines.

11. **Which code refuses a non-human on a human-only chain verb?** **OPEN (v3.6.1).** §8.2
    gives `chain_adopt` `revision_policy:human_only`, the code the human-only *revision*
    verbs use. The first plane to implement adoption and approval refuses a non-human
    caller with `capability_mismatch` instead, and §10 defines that code as "the caller's
    binding is not eligible for the step's role". Neither reading is plainly right:
    adoption is not a revision, and being a declared human is not a step's role. Until
    this is decided, readers should accept either code for these two verbs, and nothing
    here freezes one.

---

## 12. What an ASOP is not

- **Not a prompt file.** Prose without a gate is advice.
- **Not a workflow engine.** The ASOP executes nothing; harnesses do.
- **Not an orchestrator.** An ASOP does not decide which procedure a situation calls
  for, and it runs nothing. Since v3.5 it *declares* where a finished run may hand off
  (§3.7), and since v3.6 where it may send work back (§3.9) and what it checks before
  it starts (§3.8); the engine decides, refusing anything undeclared.
  Composition *inside* one procedure is nesting. Starting a chain, concurrency,
  staleness and retries stay in the harness.
- **Not a straitjacket.** Divergence is expected and metabolised, not forbidden.
- **Not every goal.** One-off work stays with the planner; ASOPs are for recurring
  types of task.
- **Not a place to name agents.** Roles live in the artefact; bindings live in the
  harness.

---

## 13. Glossary

| term | meaning |
|---|---|
| **ASOP** | the versioned sequence of steps for one type of task |
| **step** | one unit of the sequence: text, gate, role |
| **gate** | how a step proves it is done: `deterministic`, `judged`, `human` |
| **role** | a named responsibility in the ASOP, bound to an executor by the harness |
| **binding** | what fills a role in a run: an agent, a route, a person |
| **run** | one execution of one ASOP version against concrete inputs |
| **bead** | the work unit; a run is a tree of them, one per step |
| **pin** | `(asop_id, version)` on a run, `(asop_id, version, step)` on a bead |
| **attestation** | a proof-of-execution record the executing domain submits to a plane |
| **divergence** | execution departed from the step |
| **adjudication** | the record judging a divergence `good` or `bad`, by a non-executor |
| **repair bead** | a sibling filed to fix a `verify_failed` step; never substitutes |
| **promotion** | drafting an ASOP from a run tree that worked |
| **hand-off** | a declared, outcome-conditioned filing of another ASOP when a run finishes (§3.7) |
| **chain** | the runs one starting run leads to through hand-offs; the scope of `requires` and of the natural key |
| **join** | a hand-off with `after`: filed once, when every run the listed hand-offs of the same parent run filed is done (§3.7) |
| **filing request** | a finished run's own choice of which declared hand-offs and subjects to file; it carries no inputs |
| **entry gate** | a consumer's deterministic precondition over declared inputs, run before a filed run may start; over a source it is a **readiness gate** (§3.8) |
| **cannot_evaluate** | the third gate outcome: the check could not judge the work. Never releases dependents, never charged (§3.3) |
| **rework edge** | a declared, bounded loop back to an earlier step or to the upstream owner of a finding (§3.9) |
| **chain manifest** | the one pin a chain resolves from: its members' versions and the components that give its gates meaning (§3.10) |
| **adoption** | a person's recorded move of a chain to another manifest, with a computed impact report (§3.10) |
| **chain outcome** | the chain-level record computed from the ledger, and the chain's row in `outcomes_by_version` (§5.8) |
| **fault class** | why something failed: `work`, `environment`, `checker`, `handoff`, `decision`. Only `work` is charged (§5.4) |
| **plane** | a coordination layer storing ASOPs and outcomes across harnesses |
| **harness** | a runtime that executes beads; runs alone or reports to a plane |

---

*Provenance: v1 authored 2026-08; v2 adversarially reviewed and cross-validated by three
frontier-model vendors 2026-08-31 (unanimous "adapt"; must-fixes became the enforcement
model). v3 drafted 2026-09-04 from a review that found the shipped grain and gate
placement did not match v2's text. Prior-art review 2026-08-31 (AWS Agentic SOPs/Strands,
Decagon AOPs, Skan, Agent-S): instruction documents all — none carry per-version
outcomes, embedded gates, or divergence-driven revision. v3.2 drafted 2026-09-09 from a
cross-validation of the CONCEPT (not any one implementation) against ~30 prior design
notes spanning three independent builds of this contract in one week — every one of the
three had separately shipped "declared, never inferred" with no authentication behind it,
and one had separately documented, in its own words, that its "distinct verifier" check
proved a name looked, not that judgment was exercised. v3.6 drafted 2026-10-16 from a real run
of a sixteen-procedure chain — hours per work item, tens to hundreds of agent steps, and
a defect registry of about a hundred classes — whose costliest failures were not in any
step's prose: they were gates that could not evaluate and passed or were charged, loops
and pins that lived only in engine code, defaults nobody saw, and a chain whose outcome
nobody recorded.*
