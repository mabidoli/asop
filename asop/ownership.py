"""Run ownership and its one exception: a recorded administrative transfer
(ASOP.md §7.2, v3.6.1).

§7.2 fixes a run's owner at filing, and no RUN verb moves it. That stays. What
v3.6.1 adds is the case an operator meets in the first year: the owning store
is gone — its machine retired, its operator left, its credential revoked — and
its runs would otherwise be stranded for ever, because the only party allowed
to complete them can no longer speak.

A transfer is therefore not a run verb. It is an administrative record:

- made by a person the operator declared an ADMINISTRATOR (`ASOP_ADMINISTRATORS`)
  who is also a declared human (`ASOP_HUMANS`), with a reason;
- appended to the run's ownership history (who, when, from, to, reason);
- bumping the run's ownership EPOCH, so anything the former owner sends after
  it — a completion, an attestation — is refused `not_the_owner`, with the
  epoch, however good its lease;
- needing nothing from the former owner: no signature, no reachability;
- rewriting nothing already done. Completed steps keep their status and their
  evidence; only who may flip the REMAINING beads changes.

Every function is pure:

- `administrators_from_env`  the declaration
- `validate_transfer`        is this a transfer this run's state admits?
- `apply_transfer`           the run's ownership after it
- `check_flip`               may this store, at this epoch, flip a bead?
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Mapping, Optional

from asop.errors import Refusal

TRANSFER_SCHEMA = "asop.ownership-transfer/1"

ADMINISTRATORS_ENV_VAR = "ASOP_ADMINISTRATORS"
#: The name the first implementation shipped, read when the standard's is
#: unset — the same courtesy every other registry extends (§9).
LEGACY_ADMINISTRATORS_ENV_VAR = "AGENTCO_ADMINS"

TRANSFER_REFUSED = "transfer_refused"
TRANSFER_RULES: dict[str, str] = {
    "malformed": "the transfer is not a valid asop.ownership-transfer/1 document",
    "administrator": "the actor is a declared human but not a declared administrator (a non-human is human_required)",
    "reason": "the transfer gives no reason",
    "stale": "the transfer's `from` or `epoch` is not the run's current owner and epoch + 1",
    "same_owner": "the transfer moves the run to the store that already owns it",
}


def administrators_from_env(value: Optional[str] = None) -> frozenset[str]:
    """The declared administrators. Comma-separated actor names, exact
    spelling — the format of every other registry (§9). An empty declaration
    makes nobody an administrator: no run can be transferred, which is the
    v3.6 behaviour, and the closed default."""
    if value is not None:
        raw = value
    else:
        raw = os.environ.get(ADMINISTRATORS_ENV_VAR)
        if raw is None:
            raw = os.environ.get(LEGACY_ADMINISTRATORS_ENV_VAR)
    return frozenset(p.strip() for p in (raw or "").split(",") if p.strip())


def _name(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and v.strip() and v == v.strip() else None


def _refuse(rule: str, message: str, remediation: str) -> Refusal:
    return Refusal(code=f"{TRANSFER_REFUSED}:{rule}", message=message, remediation=remediation)


def ownership_of(run: Mapping) -> dict:
    """A run's ownership, with the v3.6.1 fields defaulted: a run filed before
    them is at epoch 0 with no history, which is exactly what it always was."""
    return {"owner": run.get("owner"), "epoch": run.get("epoch", 0) or 0,
            "history": list(run.get("ownership_history") or [])}


def validate_transfer(doc: Any, *, run: Mapping, administrators: Iterable[str],
                      humans: Iterable[str]) -> dict:
    """`asop.ownership-transfer/1`: `{schema, run, from, to, epoch, by, at, reason}`.

    Accepted only as the next transfer of THIS run: `from` is its current
    owner, `epoch` is its current epoch + 1, `to` is a different store. `by` is
    the authenticated actor (§9, set by the transport, never the body) and must
    be a declared human (else `human_required`) AND a declared administrator
    (else `transfer_refused:administrator`). Nothing here consults the
    former owner: an unreachable owner, or one whose credential was revoked,
    cannot block its own replacement. Raises `Refusal`
    (`transfer_refused:<rule>`)."""
    if not isinstance(doc, Mapping) or doc.get("schema") != TRANSFER_SCHEMA:
        raise _refuse("malformed", f"a transfer declares schema '{TRANSFER_SCHEMA}'",
                      "Send an asop.ownership-transfer/1 document.")
    extra = set(doc) - {"schema", "run", "from", "to", "epoch", "by", "at", "reason"}
    if extra:
        raise _refuse("malformed", f"transfer: unknown field(s) {', '.join(sorted(map(str, extra)))}",
                      "Remove them; the record is closed.")
    # The caller first (§10's order): who is asking is settled before what they sent.
    if _name(doc.get("by")) is not None:
        from asop.revision import require_declared_human
        require_declared_human(doc["by"], humans, verb="run_transfer")
    for key in ("run", "from", "to", "by", "at"):
        if _name(doc.get(key)) is None:
            raise _refuse("malformed", f"transfer '{key}' must be an exact, non-empty name",
                          f"Set '{key}'.")
    epoch = doc.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise _refuse("malformed", f"transfer 'epoch' must be an integer >= 1, got {epoch!r}",
                      "Set it to the run's current epoch + 1.")
    by = doc["by"]
    if by not in set(administrators):
        raise _refuse("administrator",
                      f"{by!r} is a declared human but not a declared administrator",
                      "Have a person the operator declared in ASOP_ADMINISTRATORS (and ASOP_HUMANS) "
                      "record it. No agent and no store transfers a run.")
    reason = doc.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise _refuse("reason", "a transfer must say why",
                      "Give the reason in a sentence: the record outlives everybody who remembers it.")
    current = ownership_of(run)
    if doc["run"] != run.get("run_id"):
        raise _refuse("stale", f"the transfer is for run {doc['run']!r}, not {run.get('run_id')!r}",
                      "Transfer the run you read.")
    if doc["from"] != current["owner"] or epoch != current["epoch"] + 1:
        raise _refuse("stale",
                      f"the run is owned by {current['owner']!r} at epoch {current['epoch']}; the transfer "
                      f"says {doc['from']!r} and epoch {epoch}",
                      "Read the run again and transfer from the owner it has now, at its epoch + 1.")
    if doc["to"] == current["owner"]:
        raise _refuse("same_owner", f"{doc['to']!r} already owns this run", "Nothing to transfer.")
    return {"schema": TRANSFER_SCHEMA, "run": doc["run"], "from": doc["from"], "to": doc["to"],
            "epoch": epoch, "by": by, "at": doc["at"], "reason": reason.strip()}


def apply_transfer(run: Mapping, doc: Any, *, administrators: Iterable[str],
                   humans: Iterable[str]) -> dict:
    """The run's ownership fields after a valid transfer: `owner` is `to`,
    `epoch` is the transfer's, and the transfer is appended to
    `ownership_history`. Nothing else on the run changes — completed steps,
    their statuses and their evidence are not touched."""
    t = validate_transfer(doc, run=run, administrators=administrators, humans=humans)
    current = ownership_of(run)
    return {"owner": t["to"], "epoch": t["epoch"], "ownership_history": current["history"] + [t]}


def check_flip(actor_store: Optional[str], run: Mapping, *, actor_epoch: Optional[int] = None) -> None:
    """Refuse `not_the_owner` unless `actor_store` owns `run` at the epoch it
    claims. `actor_epoch` None is a store that predates epochs; it is accepted
    only while the run has never been transferred (epoch 0), so a v3.6 store
    behaves as before until an administrator moves one of its runs, and is
    refused after."""
    current = ownership_of(run)
    owner, epoch = current["owner"], current["epoch"]
    claimed = 0 if actor_epoch is None else actor_epoch
    if actor_store is None or owner is None or actor_store != owner or claimed != epoch:
        raise Refusal(
            code="not_the_owner",
            message=(f"run {run.get('run_id')!r} is owned by {owner!r} at ownership epoch {epoch}; "
                     f"{actor_store!r} claimed epoch {claimed}"),
            remediation=("Only the owning store at the current epoch flips this run's beads. If ownership "
                         "was transferred, the new owner re-claims the remaining work; evidence already "
                         "recorded stands."),
        )
