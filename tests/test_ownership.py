"""Administrative ownership transfer (ASOP.md §7.2, v3.6.1): no run verb moves
a run's owner; a recorded transfer by a declared human administrator may, and
it fences the former owner by epoch without touching what is done."""

from __future__ import annotations

import pytest

from asop.errors import Refusal
from asop.manifest import body_digest
from asop.ownership import (
    TRANSFER_RULES,
    administrators_from_env,
    apply_transfer,
    check_flip,
    ownership_of,
    validate_transfer,
)
from asop.refusals import CODES
from asop.revision import may_flip
from asop.sop import SopContractError

ADMINS, HUMANS = ["ines"], ["ines", "dana"]
RUN = {"run_id": "R1", "owner": "store-a", "steps": [{"step": 1, "status": "done", "evidence": ["att-1"]},
                                                       {"step": 2, "status": "claimed"}]}
T = {"schema": "asop.ownership-transfer/1", "run": "R1", "from": "store-a", "to": "store-b", "epoch": 1,
     "by": "ines", "at": "2026-10-18T12:00:00Z", "reason": "store-a was decommissioned"}


def rule(doc, run=RUN, admins=ADMINS, humans=HUMANS):
    with pytest.raises(Refusal) as e:
        validate_transfer(doc, run=run, administrators=admins, humans=humans)
    return e.value.code


def test_a_v36_run_is_at_epoch_zero_with_no_history():
    assert ownership_of({"owner": "store-a"}) == {"owner": "store-a", "epoch": 0, "history": []}


def test_an_administrator_transfers_and_the_history_records_it():
    after = apply_transfer(RUN, T, administrators=ADMINS, humans=HUMANS)
    assert after["owner"] == "store-b" and after["epoch"] == 1
    assert after["ownership_history"] == [T]
    assert set(after) == {"owner", "epoch", "ownership_history"}, "a transfer touches nothing else"
    second = {**T, "from": "store-b", "to": "store-c", "epoch": 2, "reason": "again"}
    again = apply_transfer({**RUN, **after}, second, administrators=ADMINS, humans=HUMANS)
    assert [h["to"] for h in again["ownership_history"]] == ["store-b", "store-c"]


def test_completed_steps_are_not_rewritten():
    before = [dict(s) for s in RUN["steps"]]
    run = {**RUN, **apply_transfer(RUN, T, administrators=ADMINS, humans=HUMANS)}
    assert run["steps"] == before


def test_refusal_rules():
    assert rule({**T, "by": "dana"}) == "transfer_refused:administrator"
    assert rule(T, humans=["dana"]) == "transfer_refused:administrator", "an administrator must also be human"
    assert rule(T, admins=[]) == "transfer_refused:administrator", "an empty registry transfers nothing"
    assert rule({**T, "reason": ""}) == "transfer_refused:reason"
    assert rule({k: v for k, v in T.items() if k != "reason"}) == "transfer_refused:reason"
    assert rule({**T, "from": "store-x"}) == "transfer_refused:stale"
    assert rule({**T, "epoch": 2}) == "transfer_refused:stale"
    assert rule({**T, "run": "R2"}) == "transfer_refused:stale"
    assert rule({**T, "to": "store-a"}) == "transfer_refused:same_owner"
    assert rule({**T, "epoch": True}) == "transfer_refused:malformed"
    assert rule({**T, "note": "x"}) == "transfer_refused:malformed"
    assert rule({**T, "schema": "asop.ownership-transfer/2"}) == "transfer_refused:malformed"


def test_every_rule_is_catalogued():
    assert "transfer_refused:<rule>" in CODES
    assert set(TRANSFER_RULES) == {"malformed", "administrator", "reason", "stale", "same_owner"}


def test_the_former_owner_is_not_consulted():
    """An owner in no registry, unreachable, revoked: it signs nothing, so it blocks nothing."""
    gone = {"run_id": "R1", "owner": "store-gone"}
    assert apply_transfer(gone, {**T, "from": "store-gone"}, administrators=ADMINS, humans=HUMANS)["owner"] == "store-b"


def test_the_former_owner_is_fenced_by_epoch():
    run = {**RUN, **apply_transfer(RUN, T, administrators=ADMINS, humans=HUMANS)}
    for store, epoch in (("store-a", None), ("store-a", 0), ("store-a", 1), ("store-b", 0), ("store-b", None)):
        with pytest.raises(Refusal) as e:
            check_flip(store, run, actor_epoch=epoch)
        assert e.value.code == "not_the_owner" and "epoch 1" in e.value.message
    check_flip("store-b", run, actor_epoch=1)


def test_back_to_the_same_store_still_fences_stale_work():
    run = {"run_id": "R1", "owner": "store-a", "epoch": 2}
    with pytest.raises(Refusal):
        check_flip("store-a", run, actor_epoch=0)
    check_flip("store-a", run, actor_epoch=2)


def test_a_v36_caller_is_unchanged_until_a_transfer():
    assert may_flip("store-a", "store-a") is True
    check_flip("store-a", {"run_id": "R1", "owner": "store-a"})
    assert may_flip("store-a", "store-a", run_epoch=1) is False


def test_administrators_from_env(monkeypatch):
    monkeypatch.setenv("ASOP_ADMINISTRATORS", " ines , ops ")
    assert administrators_from_env() == {"ines", "ops"}
    monkeypatch.delenv("ASOP_ADMINISTRATORS")
    assert administrators_from_env() == frozenset()


def test_ownership_never_enters_the_member_digest():
    """Ownership is run state. A record carrying it is refused, not digested."""
    rec = {"title": "Solo", "roles": {"w": {"kind": "agent"}},
           "steps": [{"name": "do", "role": "w", "purpose": "p", "gate": {"kind": "deterministic", "check": "c"}}]}
    body_digest(rec)
    for field in ("owner", "epoch", "ownership_history"):
        with pytest.raises(SopContractError):
            body_digest({**rec, field: "x"})
