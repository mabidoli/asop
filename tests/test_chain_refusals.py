"""Storing chain records (ASOP.md §10, v3.6.1): the refusal codes a store
returns for manifests, adoptions and approvals, the reference checks that
raise them, and `validate_approval`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from asop import validate_asop
from asop.chain import ChainContractError, validate_approval
from asop.errors import Refusal
from asop.manifest import (
    adoption_target,
    body_digest,
    check_pinned,
    manifest_digest,
    require_schema,
    verify_manifest,
)
from asop.refusals import CHAIN_RECORD_CODES, CODES, LEGACY_USES

DET = {"kind": "deterministic", "check": "make check"}
REC = {"title": "Solo", "roles": {"w": {"kind": "agent"}},
       "steps": [{"name": "do", "role": "w", "purpose": "do it", "gate": DET}]}
GOOD = {"schema": "asop.chain-manifest/1", "chain": "C1", "start": {"asop_id": "solo", "version": 1},
        "members": [{"asop_id": "solo", "version": 1, "digest": body_digest(REC, asop_id="solo")}]}
APPR = {"schema": "asop.approval/1", "artefact": "tasks", "digest": "sha256:aa", "subset": ["T1", 2],
        "approved_by": "dana", "approved_at": "2026-10-18T12:00:00Z"}


def refused(code, fn, *a, **kw):
    with pytest.raises(Refusal) as e:
        fn(*a, **kw)
    assert e.value.code == code and e.value.remediation.strip()
    return e.value


# --- the vocabulary -----------------------------------------------------------

def test_every_chain_record_code_is_catalogued_and_specified():
    spec = (Path(__file__).resolve().parents[1] / "ASOP.md").read_text()
    for code in CHAIN_RECORD_CODES:
        assert code in CODES, code
        assert re.search(rf"\*\*`{code}`\*\*", spec), f"{code} has no row in ASOP.md §10"


def test_unknown_field_is_catalogued_but_not_specified():
    """A transport refusal: catalogued with the plane's HTTP codes, not part of §10."""
    spec = (Path(__file__).resolve().parents[1] / "ASOP.md").read_text()
    assert "unknown_field" in CODES and "unknown_field" not in CHAIN_RECORD_CODES
    assert not re.search(r"\*\*`unknown_field`\*\*", spec)


def test_legacy_use_points_at_a_specified_code():
    for (old, _situation), new in LEGACY_USES.items():
        assert new in CHAIN_RECORD_CODES or new.startswith("transfer_refused:")


# --- manifests ------------------------------------------------------------------

def fetch_from(store):
    return lambda a, v: store.get((a, v))


def test_verify_manifest_accepts_the_stores_own_digests():
    assert verify_manifest(GOOD, fetch_from({("solo", 1): ("active", REC)}))["chain"] == "C1"


def test_verify_manifest_refusals_in_order():
    held = fetch_from({("solo", 1): ("active", REC)})
    refused("manifest_invalid", verify_manifest, {**GOOD, "members": []}, held)
    refused("manifest_member_unknown", verify_manifest, GOOD, fetch_from({}))
    refused("manifest_member_unknown", verify_manifest, GOOD, fetch_from({("solo", 1): ("draft", REC)}))
    forged = {**GOOD, "members": [{**GOOD["members"][0], "digest": "sha256:" + "0" * 64}]}
    refused("manifest_digest_mismatch", verify_manifest, forged, held)


def test_a_record_the_contract_refuses_has_no_digest_to_match():
    broken = {"title": "No steps"}
    refused("manifest_digest_mismatch", verify_manifest, GOOD, fetch_from({("solo", 1): ("active", broken)}))


def test_a_superseded_member_is_still_pinnable():
    """Runs pinned to it must stay resolvable (§4); only a DRAFT cannot be run."""
    verify_manifest(GOOD, fetch_from({("solo", 1): ("superseded", REC)}))


def test_a_stored_record_with_store_fields_verifies():
    stored = {"asop_id": "solo", "version": 1, "status": "active", **REC}
    verify_manifest(GOOD, fetch_from({("solo", 1): ("active", stored)}))


# --- adoption targets and pins ------------------------------------------------

def test_adoption_target():
    other = {**GOOD, "components": [{"name": "checker", "digest": "sha256:" + "1" * 64}]}
    recorded = {manifest_digest(GOOD): GOOD, manifest_digest(other): other}
    old, new = adoption_target("C1", manifest_digest(other), current={"C1": manifest_digest(GOOD)}, manifests=recorded)
    assert new["components"] and not old["components"]
    refused("chain_unknown", adoption_target, "C9", manifest_digest(other),
            current={"C1": manifest_digest(GOOD)}, manifests=recorded)
    refused("manifest_unknown", adoption_target, "C1", "sha256:" + "f" * 64,
            current={"C1": manifest_digest(GOOD)}, manifests=recorded)
    elsewhere = {**other, "chain": "C2"}
    refused("manifest_unknown", adoption_target, "C1", manifest_digest(elsewhere),
            current={"C1": manifest_digest(GOOD)}, manifests={**recorded, manifest_digest(elsewhere): elsewhere})


def test_check_pinned():
    assert check_pinned(GOOD, "solo") == 1 == check_pinned(GOOD, "solo", 1)
    refused("adoption_required", check_pinned, GOOD, "solo", 2)
    refused("adoption_required", check_pinned, GOOD, "stranger")


def test_require_schema():
    assert require_schema({"schema": "asop.approval/1"}) == "asop.approval/1"
    assert require_schema({"title": "a record has no schema field"}) is None
    refused("contract_unsupported", require_schema, {"schema": "asop.chain-manifest/2"})
    refused("contract_unsupported", require_schema, {"schema": "asop.approval/1"}, supported=())


# --- approvals ------------------------------------------------------------------

def test_validate_approval_accepts_and_normalises():
    assert validate_approval(APPR) == APPR
    assert "subset" not in validate_approval({k: v for k, v in APPR.items() if k != "subset"})
    assert "subset" not in validate_approval({**APPR, "subset": None}), "null is absent"


@pytest.mark.parametrize("change", [
    {"schema": "asop.approval/2"}, {"digest": ""}, {"digest": None}, {"artefact": " tasks"},
    {"approved_by": ""}, {"approved_at": None}, {"subset": "T1"}, {"subset": ["T1", "T1"]},
    {"subset": [True]}, {"subset": [1.5]}, {"subset": [" T1"]}, {"note": "lgtm"},
])
def test_validate_approval_refusals(change):
    with pytest.raises(ChainContractError):
        validate_approval({**APPR, **change})


def test_string_and_integer_ids_of_the_same_spelling_are_two_subjects():
    """Matching the schema's `uniqueItems`, which compares by type."""
    assert validate_approval({**APPR, "subset": ["1", 1]})["subset"] == ["1", 1]


def test_validate_asop_is_untouched():
    validate_asop(REC)


# --- human_required (v3.6.1) ----------------------------------------------------

def test_human_required_for_the_chain_and_ownership_verbs():
    from asop.revision import HUMAN_REQUIRED_VERBS, require_declared_human
    assert HUMAN_REQUIRED_VERBS == ("chain_adopt", "approve", "run_transfer")
    for verb in HUMAN_REQUIRED_VERBS:
        assert require_declared_human("dana", ["dana"], verb=verb) == "dana"
        for actor in ("ops-bot", None):
            refused("human_required", require_declared_human, actor, ["dana"], verb=verb)


def test_capability_mismatch_keeps_its_meaning_and_is_only_a_legacy_alias_here():
    from asop.refusals import LEGACY_UNTIL
    assert LEGACY_USES[("capability_mismatch", "a non-human attempting chain_adopt or approve")] == "human_required"
    assert "capability" in CODES["capability_mismatch"] and "human" not in CODES["capability_mismatch"]
    assert LEGACY_UNTIL == "v3.7"


def test_the_revision_policy_require_human_is_untouched():
    """retire and promote keep `revision_policy:human_only`; the new helper has its own name."""
    from asop.revision import RevisionPolicyError, require_human
    with pytest.raises(RevisionPolicyError):
        require_human("agent", "retire", because="test")
