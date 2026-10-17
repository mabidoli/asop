"""The chain manifest and adoption (ASOP.md §3.10, v3.6): one pin for a whole
chain, resolved once at its start; a move to another manifest only by a
recorded adoption whose impact report is computed, not typed."""

from __future__ import annotations

import pytest

from asop import validate_asop
from asop.chain import ChainContractError, plan_handoffs
from asop.manifest import (
    ADOPTION_SCHEMA,
    MANIFEST_SCHEMA,
    adoption_impact,
    canonical_json,
    digest_of,
    is_pinned,
    manifest_digest,
    manifest_resolver,
    pin_manifest,
    validate_adoption,
    validate_manifest,
)

DET = {"kind": "deterministic", "check": "make check"}
CHECKER = {"name": "checker", "version": "29", "digest": "sha256:" + "1" * 64}


def body(title, **chain):
    b = {"title": title, "inputs": [{"name": "root"}], "roles": {"worker": {"kind": "agent"}},
         "steps": [{"name": "do", "role": "worker", "purpose": "do it", "gate": DET}]}
    b.update(chain)
    return validate_asop(b)


STORE = {
    ("intake", 1): body("Intake", handoffs=[{"id": "x", "to": {"asop_id": "extract", "versions": ">=2"},
                                              "inputs": {"root": "$run.inputs.root"}}]),
    ("extract", 2): body("Extract v2"),
    ("extract", 3): body("Extract v3"),
}
ACTIVE = {"intake": 1, "extract": 2}


def store_resolve(to, active=None):
    active = active or ACTIVE
    v = active.get(to["asop_id"])
    if v is None:
        return None
    from asop.chain import version_in
    return (v, STORE[(to["asop_id"], v)]) if version_in(to, v) else None


def fetch(asop_id, version):
    return STORE.get((asop_id, version))


def pinned(active=None, components=(CHECKER,)):
    return pin_manifest("intake", 1, STORE[("intake", 1)], lambda to: store_resolve(to, active),
                        chain="C1", components=components)


def test_pin_resolves_every_reachable_member_once():
    m = pinned()
    assert m["schema"] == MANIFEST_SCHEMA
    assert [(x["asop_id"], x["version"]) for x in m["members"]] == [("extract", 2), ("intake", 1)]
    assert m["components"] == [CHECKER]
    assert manifest_digest(m) == digest_of(m)


def test_the_chain_keeps_its_pin_after_a_new_version_activates():
    """THE MIXED-VERSION CASE. v3.5 resolves `>=2` at each filing, so the
    first filings of a chain get extract v2 and the last ones v3. Under a
    manifest, every filing of this chain gets v2."""
    m = pinned()
    after = {"intake": 1, "extract": 3}
    v35 = plan_handoffs(STORE[("intake", 1)], outcome=None, outputs={}, run_inputs={"root": "r"}, requests=[],
                        resolve=lambda to: store_resolve(to, after), chain="C1")
    v36 = plan_handoffs(STORE[("intake", 1)], outcome=None, outputs={}, run_inputs={"root": "r"}, requests=[],
                        resolve=manifest_resolver(m, fetch), chain="C1")
    assert v35[0]["to"] == {"asop_id": "extract", "version": 3}
    assert v36[0]["to"] == {"asop_id": "extract", "version": 2}


def test_an_unpinned_procedure_resolves_to_nothing():
    r = manifest_resolver(pinned(), fetch)
    assert r({"asop_id": "elsewhere", "version": 1}) is None
    assert r({"asop_id": "extract", "versions": ">=3"}) is None, "the pin is outside this reference's range"
    assert is_pinned(pinned(), "extract", 2) and not is_pinned(pinned(), "extract", 3)


def test_pin_refuses_a_chain_it_cannot_pin_whole():
    with pytest.raises(ChainContractError, match="cannot pin 'extract'"):
        pinned(active={"intake": 1})


def test_one_version_per_procedure():
    two = body("Two refs", handoffs=[{"id": "a", "to": {"asop_id": "extract", "version": 2},
                                      "inputs": {"root": "$run.inputs.root"}}],
               on_escalate={"to": {"asop_id": "extract", "versions": ">=3"}})
    with pytest.raises(ChainContractError, match="one version of each procedure"):
        pin_manifest("two", 1, two, lambda to: store_resolve(to, {"extract": 2}) if "version" in to
                     else store_resolve(to, {"extract": 3}), chain="C1")


def test_manifest_shape():
    m = pinned()
    for bad in ({**m, "schema": "asop.chain-manifest/2"}, {**m, "extra": 1}, {**m, "members": []},
                {**m, "members": m["members"] + [m["members"][0]]},
                {**m, "members": [dict(m["members"][0], digest="sha1:abc")] + m["members"][1:]},
                {**m, "start": {"asop_id": "elsewhere", "version": 1}},
                {**m, "components": [dict(CHECKER, version=29)]}):
        with pytest.raises(ChainContractError):
            validate_manifest(bad)


def test_canonical_json_refuses_floats():
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()
    with pytest.raises(ChainContractError, match="float"):
        canonical_json({"x": 0.1})


def test_member_order_does_not_change_the_digest():
    m = pinned()
    assert manifest_digest({**m, "members": list(reversed(m["members"]))}) == manifest_digest(m)


# --- adoption ---------------------------------------------------------------

def test_impact_names_what_changed_including_the_checker():
    old = pinned()
    new = pinned(active={"intake": 1, "extract": 3},
                 components=(dict(CHECKER, version="31", digest="sha256:" + "2" * 64),))
    impact = adoption_impact(old, new)
    by = {(r["kind"], r["name"]): r for r in impact}
    assert by[("member", "extract")]["effect"] == "changed"
    assert by[("member", "intake")]["effect"] == "unchanged"
    assert by[("component", "checker")]["effect"] == "changed"
    assert [r for r in adoption_impact(old, pinned(components=())) if r["kind"] == "component"] == [{
        "kind": "component", "name": "checker", "from": {"version": "29", "digest": CHECKER["digest"]},
        "to": None, "effect": "removed"}]


def adoption(old, new, **kw):
    doc = {"schema": ADOPTION_SCHEMA, "chain": "C1", "from": manifest_digest(old), "to": manifest_digest(new),
           "adopted_by": "dana", "adopted_at": "2026-10-16T12:00:00Z", "impact": adoption_impact(old, new)}
    doc.update(kw)
    return doc


def test_a_recorded_adoption_of_exactly_this_move_is_accepted():
    old, new = pinned(), pinned(active={"intake": 1, "extract": 3})
    assert validate_adoption(adoption(old, new), old=old, new=new)["adopted_by"] == "dana"


@pytest.mark.parametrize("change, msg", [
    ({"impact": []}, "computed impact"),
    ({"from": "sha256:" + "0" * 64}, "'from'"),
    ({"adopted_by": "  "}, "adopted_by"),
    ({"chain": "C2"}, "ONE chain"),
    ({"schema": "asop.adoption/2"}, "schema"),
])
def test_adoption_refusals(change, msg):
    old, new = pinned(), pinned(active={"intake": 1, "extract": 3})
    with pytest.raises(ChainContractError, match=msg):
        validate_adoption(adoption(old, new, **change), old=old, new=new)


def test_adopting_the_same_manifest_is_not_an_adoption():
    m = pinned()
    with pytest.raises(ChainContractError, match="changes nothing"):
        validate_adoption(adoption(m, m), old=m, new=m)
