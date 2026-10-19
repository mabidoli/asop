"""The member digest, normative since v3.6.1 (ASOP.md §3.10): SHA-256 over the
canonical JSON of the validated body, store-assigned fields removed.

`digest.yaml` pins the strings; these tests pin the RULES behind them, with a
second serializer written from the spec text alone, so the prose and the
reference implementation cannot drift apart silently."""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from asop import validate_asop
from asop.chain import ChainContractError
from asop.manifest import STORE_FIELDS, body_digest, canonical_json, digest_of, pin_manifest
from asop.sop import SopContractError

DET = {"kind": "deterministic", "check": "make check"}
BODY = {"title": "One step", "roles": {"w": {"kind": "agent"}},
        "steps": [{"name": "do", "role": "w", "purpose": "do it", "gate": DET}]}


def spec_serialize(v) -> str:
    """ASOP.md §3.10, read literally — not the reference implementation."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        short = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
        return '"' + "".join(short.get(c, f"\\u{ord(c):04x}" if ord(c) < 0x20 else c) for c in v) + '"'
    if isinstance(v, list):
        return "[" + ",".join(spec_serialize(x) for x in v) + "]"
    return "{" + ",".join(f"{spec_serialize(k)}:{spec_serialize(v[k])}" for k in sorted(v)) + "}"


def test_the_prose_and_the_reference_serialize_identically():
    doc = {"z": [True, False, None, -7, 0], "é": "tab\there \"q\" \\ \x01 \x7f /", "A": {"\U0001F600": 1, "￿": 2}}
    assert canonical_json(doc) == spec_serialize(doc).encode("utf-8")


def test_keys_sort_by_code_point_not_utf16():
    """U+FFFF sorts before U+1F600 by code point; RFC 8785's UTF-16 order is the reverse."""
    assert canonical_json({"\U0001F600": 1, "￿": 2}) == '{"￿":2,"\U0001F600":1}'.encode()


def test_digest_string_shape():
    d = digest_of({"a": 1})
    assert d == "sha256:" + hashlib.sha256(b'{"a":1}').hexdigest()
    assert len(d) == 71 and d == d.lower()


def test_member_digest_is_the_digest_of_the_validated_body():
    assert body_digest(BODY) == digest_of(validate_asop(BODY))
    assert body_digest(validate_asop(BODY)) == body_digest(BODY), "normalisation is idempotent"


def test_store_fields_are_excluded():
    stored = {"asop_id": "x", "version": 3, "status": "active", "author": "dana", "author_kind": "human",
              "superseded_by": None, "created_at": "2026-10-18T00:00:00Z", **BODY}
    assert set(STORE_FIELDS) <= set(stored)
    assert body_digest(stored) == body_digest(BODY)


def test_null_optional_field_is_absent():
    assert body_digest({**BODY, "trigger": None, "handoffs": None}) == body_digest(BODY)


def test_no_unicode_normalisation():
    assert body_digest({**BODY, "title": "Café"}) != body_digest({**BODY, "title": "Café"})


def test_undigestable_records_are_refused():
    with pytest.raises(ChainContractError, match="float"):
        body_digest({**BODY, "inputs": [{"name": "a", "kind": 0.5}]})
    with pytest.raises(ChainContractError, match="lone surrogates"):
        canonical_json({"a": "\ud800"})
    with pytest.raises(SopContractError):
        body_digest({"title": "no steps"})


def test_pin_manifest_uses_the_member_digest():
    body = validate_asop(BODY)
    m = pin_manifest("solo", 1, body, lambda to: None, chain="C1")
    assert m["members"] == [{"asop_id": "solo", "version": 1, "digest": body_digest(BODY)}]


def test_the_pinned_vector_string_is_what_the_reference_computes():
    """digest.yaml's fixed string, recomputed from the spec serializer."""
    yaml = pytest.importorskip("yaml")
    path = pathlib.Path(__file__).resolve().parents[1] / "conformance" / "vectors" / "digest.yaml"
    fixed = next(v for v in yaml.safe_load(path.read_text())["vectors"] if v["id"] == "dg-fixed-document")
    body = validate_asop(fixed["document"], asop_id=fixed["asop_id"])
    by_spec = "sha256:" + hashlib.sha256(spec_serialize(json.loads(json.dumps(body))).encode()).hexdigest()
    assert by_spec == fixed["digest"] == body_digest(fixed["document"], asop_id=fixed["asop_id"])
