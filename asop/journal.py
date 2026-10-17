"""Per-step journaling, resume, staleness and the chain outcome record
(ASOP.md §5.6–§5.8, v3.6).

A run that is written down only when it finishes is a run whose middle cannot
be read. A long chain observed in practice wrote each run's steps to its ledger
in one batch at the end, and three things followed from that one choice: step
timings were artefacts of the flush (a two-minute step read as hours), a value
one step wrote for the next was not there when the next step looked, and an
executor that died mid-run left no entry at all, so its crash read as a
failure and was charged as one. §6.2 already required the plan-vs-actual
review "at the moment that step completes"; this module makes the record it
reads normative.

Every function is pure:

- `validate_journal_entry`  is this an `asop.step-journal/1` entry?
- `resume_point`            after a restart, where does a run stand?
- `charged_failures`        how many failures §5.4's budget counts
- `stale_runs`              which runs read an artefact that has since changed
- `chain_outcome`           the chain-level outcome record, from the ledger
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping, Optional

from asop.chain import ChainContractError, _ident

JOURNAL_SCHEMA = "asop.step-journal/1"
CHAIN_OUTCOME_SCHEMA = "asop.chain-outcome/1"

#: What a journal entry records. `started` is appended when a step is claimed;
#: every other event when it ends. A step whose last entry is `started` was
#: interrupted — a crash, not a failure.
STEP_EVENTS = ("started", "passed", "failed", "cannot_evaluate", "parked", "done_on_default", "escalated")
DONE_EVENTS = ("passed", "done_on_default")

#: Why something failed or escalated, as the engine classifies it (§5.4).
#: Only `work` — the executor's own work was wrong — is charged to §5.4's
#: budget. An environment that could not run the work, a checker that judged
#: it wrongly, an input a predecessor handed on broken (`handoff`), and a
#: question only a person can answer (`decision`) are not the executor's
#: failures, and charging them is how an escalation queue fills with the
#: machine's problems wearing the work's name.
FAULT_CLASSES = ("work", "environment", "checker", "handoff", "decision")

CHAIN_VERDICTS = ("done", "open", "failed")


def validate_journal_entry(doc: Any) -> dict:
    """`asop.step-journal/1`: `{schema, natural_key, step, attempt, event, at,
    fault_class?, outputs?, reason?}`. `attempt` is the lease attempt (§5.2),
    the fencing number — not §5.4's budget, which `charged_failures` counts.
    `outputs` maps a declared output to the digest of what the step wrote, so
    the next step, a resume and the staleness rule read the same value."""
    if not isinstance(doc, Mapping):
        raise ChainContractError("a journal entry must be a mapping.")
    if doc.get("schema") != JOURNAL_SCHEMA:
        raise ChainContractError(f"a journal entry declares schema '{JOURNAL_SCHEMA}', got {doc.get('schema')!r}.")
    extra = set(doc) - {"schema", "natural_key", "step", "attempt", "event", "at", "fault_class", "outputs", "reason"}
    if extra:
        raise ChainContractError(f"journal entry: unknown field(s) {', '.join(sorted(map(str, extra)))}.")
    out: dict = {"schema": JOURNAL_SCHEMA, "natural_key": _ident(doc.get("natural_key"), "journal 'natural_key'")}
    for key in ("step", "attempt"):
        v = doc.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise ChainContractError(f"journal '{key}' must be an integer >= 1, got {v!r}.")
        out[key] = v
    if doc.get("event") not in STEP_EVENTS:
        raise ChainContractError(f"journal 'event' must be one of {', '.join(STEP_EVENTS)}, got {doc.get('event')!r}.")
    out["event"] = doc["event"]
    out["at"] = _ident(doc.get("at"), "journal 'at'")
    if doc.get("fault_class") is not None:
        if doc["fault_class"] not in FAULT_CLASSES:
            raise ChainContractError(
                f"journal 'fault_class' must be one of {', '.join(FAULT_CLASSES)}, got {doc['fault_class']!r}."
            )
        if out["event"] not in ("failed", "cannot_evaluate", "escalated"):
            raise ChainContractError(f"a '{out['event']}' entry has no fault to classify.")
        out["fault_class"] = doc["fault_class"]
    if doc.get("outputs") is not None:
        if not isinstance(doc["outputs"], Mapping) or not all(
                isinstance(v, str) and v.strip() for v in doc["outputs"].values()):
            raise ChainContractError("journal 'outputs' maps a declared output to the digest of what was written.")
        out["outputs"] = {_ident(k, "journal output name"): v for k, v in doc["outputs"].items()}
    if doc.get("reason") is not None:
        out["reason"] = _ident(doc["reason"].strip() if isinstance(doc["reason"], str) else doc["reason"],
                               "journal 'reason'")
    return out


def resume_point(body: Mapping, entries: Iterable[Mapping]) -> dict:
    """Where a run stands after a restart, from its own journal alone (§5.6).

    `body` is the run's pinned, validated ASOP body; `entries` are this run's
    journal entries in the order they were appended. Each step's LAST entry
    decides it:

    - `passed` / `done_on_default` → `done`;
    - `started` with nothing after → `crashed`: re-run it under a new lease.
      A crash re-file is NOT an attempt: §5.4's budget counts gate failures,
      and an interrupted step reached no gate;
    - `failed`, `cannot_evaluate`, `parked`, `escalated` → as named; these
      are the ordinary paths of §5.2 and §5.4, not resume's to decide.

    `next` lists the steps that may run now: not done, not waiting on anything,
    and every step they are `after` done — crashed steps first among them, in
    step order. The probe needs no transcript and no executor memory: if it did,
    a run would be resumable only by the process that died."""
    last: dict[int, str] = {}
    for e in entries:
        last[int(e["step"])] = e["event"]
    states = {"done": [], "crashed": [], "failed": [], "cannot_evaluate": [], "parked": [], "escalated": []}
    for n, ev in sorted(last.items()):
        key = ("done" if ev in DONE_EVENTS else "crashed" if ev == "started" else ev)
        states[key].append(n)
    done = set(states["done"])
    blocked = set(states["failed"]) | set(states["cannot_evaluate"]) | set(states["parked"]) | set(states["escalated"])
    nxt = []
    for i, st in enumerate(body.get("steps", []), start=1):
        if i in done or i in blocked:
            continue
        after = st.get("after", [i - 1] if i > 1 else [])
        if all(a in done for a in after):
            nxt.append(i)
    return {**states, "next": nxt}


def charged_failures(entries: Iterable[Mapping], *, step: int) -> int:
    """How many failures of `step` §5.4's budget counts (feed the result to
    `asop.gates.retry_decision`). A `failed` entry whose `fault_class` is
    `work`, or that carries none, counts — an engine that classifies nothing
    charges every failure, exactly as v3.5 did. A crash, a `cannot_evaluate`,
    and a failure classified as anything but `work` do not."""
    return sum(1 for e in entries
               if int(e["step"]) == step and e["event"] == "failed"
               and e.get("fault_class") in (None, "work"))


def stale_runs(lineage: Mapping, changed: Mapping) -> list[str]:
    """The runs that read an artefact which has since changed (§5.7).

    `lineage` maps a natural key to `{"read": {artefact: digest}}` — every
    artefact the run was built from, with the digest it read. `changed` maps an
    artefact to its new digest. A run is stale when it read a changed artefact
    at a different digest.

    The same rule covers four things v3.5 left to the engine, so two engines
    reach the same answer:
    - a predecessor's output that was re-written;
    - a SOURCE whose readiness was evaluated (§3.8): the readiness verdict is
      recorded as lineage, and a changed source makes it stale;
    - an APPROVAL (§3.7): it binds a digest; a changed artefact makes every
      run built on the approval stale;
    - a person's ANSWER (§5.7): a run built while a question was open — on its
      declared default, or on nothing — records the question with digest
      `None`. Writing the answer changes it, so every run built on the
      unanswered question is stale, and is re-filed by the staleness rules.
      An answer is an input, not a comment.

    Re-filing is the engine's (§3.7): a stale run is re-filed as its next
    attempt, and a re-attempted parent files its fan-out afresh, which is how
    staleness reaches the runs below it."""
    out = []
    for key, rec in lineage.items():
        read = (rec or {}).get("read") or {}
        if any(a in changed and read[a] != changed[a] for a in read):
            out.append(key)
    return sorted(out)


def _seconds(start: Optional[str], end: Optional[str]) -> Optional[int]:
    if not start or not end:
        return None
    a = datetime.fromisoformat(start.replace("Z", "+00:00"))
    b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return int((b - a).total_seconds())


def _num(v: Any) -> float:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def chain_outcome(runs: Iterable[Mapping], *, chain: str, manifest: Optional[str] = None,
                  started_at: Optional[str] = None, ended_at: Optional[str] = None,
                  escalations: Iterable[Mapping] = (), chain_gate: Optional[str] = None,
                  spend_unit: Optional[str] = None) -> dict:
    """The chain outcome record, `asop.chain-outcome/1` (§5.8).

    `runs` are the chain's runs from its ledger: `{asop_id, version, subject,
    attempt, status, fault_class?, spend?, human_wait_seconds?}`, status one of
    `done`, `failed`, `open`, `superseded`. `escalations` are `{fault_class?,
    audience?}`. `manifest` is the digest of the chain's manifest (§3.10), when
    it has one. `chain_gate` is the outcome of the chain-level gate a terminal
    procedure requires, when there is one.

    The verdict reads each subject's LATEST attempt of each procedure,
    superseded runs aside: `failed` when one ended failed or the chain gate
    did; `done` when every one is done and the chain gate (if any) passed;
    `open` otherwise. `mixed_versions` names any procedure the chain ran at
    more than one version — the condition a manifest exists to prevent, and
    the one an outcome row must not hide."""
    runs = list(runs)
    latest: dict[tuple, Mapping] = {}
    members: dict[tuple, dict] = {}
    failures: dict[str, int] = {}
    versions: dict[str, set] = {}
    for r in runs:
        mk = (r["asop_id"], r["version"])
        m = members.setdefault(mk, {"asop_id": r["asop_id"], "version": r["version"], "runs": 0, "done": 0, "failed": 0})
        m["runs"] += 1
        if r["status"] in ("done", "failed"):
            m[r["status"]] += 1
        versions.setdefault(r["asop_id"], set()).add(r["version"])
        if r["status"] == "failed":
            fc = r.get("fault_class") or "unclassified"
            failures[fc] = failures.get(fc, 0) + 1
        if r["status"] == "superseded":
            continue
        key = (r["asop_id"], str(r["subject"]))
        if key not in latest or r.get("attempt", 1) > latest[key].get("attempt", 1):
            latest[key] = r
    esc: dict[str, int] = {}
    audiences: dict[str, int] = {}
    for e in escalations:
        fc = e.get("fault_class") or "unclassified"
        esc[fc] = esc.get(fc, 0) + 1
        if e.get("audience"):
            audiences[e["audience"]] = audiences.get(e["audience"], 0) + 1
    statuses = [r["status"] for r in latest.values()]
    if "failed" in statuses or chain_gate == "failed":
        verdict = "failed"
    elif statuses and all(s == "done" for s in statuses) and chain_gate in (None, "passed"):
        verdict = "done"
    else:
        verdict = "open"
    record = {
        "schema": CHAIN_OUTCOME_SCHEMA,
        "chain": _ident(chain, "chain_outcome chain"),
        "verdict": verdict,
        "wall_seconds": _seconds(started_at, ended_at),
        "spend": sum(_num(r.get("spend")) for r in runs),
        "human_wait_seconds": sum(_num(r.get("human_wait_seconds")) for r in runs),
        "runs": len(runs),
        "superseded": sum(1 for r in runs if r["status"] == "superseded"),
        "failures_by_class": dict(sorted(failures.items())),
        "escalations_by_class": dict(sorted(esc.items())),
        "escalations_by_audience": dict(sorted(audiences.items())),
        "members": [members[k] for k in sorted(members)],
        "mixed_versions": sorted(a for a, vs in versions.items() if len(vs) > 1),
    }
    if manifest is not None:
        record["manifest"] = manifest
    if spend_unit is not None:
        record["spend_unit"] = spend_unit
    if chain_gate is not None:
        record["chain_gate"] = chain_gate
    if started_at is not None:
        record["started_at"] = started_at
    if ended_at is not None:
        record["ended_at"] = ended_at
    return record
