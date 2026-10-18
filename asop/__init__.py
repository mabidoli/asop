"""asop — the Agentic Standard Operating Procedure contract.

The shared shape that any coordination plane and any harness executing its
work can both validate against, without either depending on the other. See
`README.md` for what belongs here versus what stays plane- or harness-side.

`SCHEMA_VERSION` covers the gate schema (`asop.gates.validate_gate`'s
normalised output carries it as `schema_version`). It changes only for a
breaking change to that normalised shape — a new required field, a renamed
one, a changed type — never for an additive optional field or a new refusal
code, both of which existing readers already tolerate. Bump it in the same
change that breaks compatibility, never after the fact.
"""

from __future__ import annotations

from asop.errors import Refusal
from asop.gates import (
    ATTESTATION_FIELDS,
    ATTESTATION_INVALID,
    ATTESTATION_REQUIRED,
    GATE_FIELDS,
    GATE_INVALID,
    GATE_KINDS,
    GATE_OUTCOMES,
    ON_TIMEOUT,
    VERIFY_CAPABILITY,
    attestation_passes,
    gate_outcome,
    gate_satisfied,
    retry_decision,
    timeout_resolution,
    validate_attestation,
    validate_gate,
)
from asop.chain import (
    CARDINALITIES,
    FILING_REQUEST_SCHEMA,
    HANDOFF_RULES,
    JOIN_STATES,
    MAX_REWORK_ROUNDS,
    REQUIRE_KINDS,
    REWORK_RULES,
    ChainContractError,
    fanout_startable,
    plan_entry,
    plan_escalation,
    plan_handoffs,
    plan_join,
    plan_rework,
    preflight_checks,
    validate_approval,
    join_state,
    requires_satisfied,
    validate_chain_fields,
    validate_filing_request,
    validate_handoff_targets,
)
from asop.journal import (
    FAULT_CLASSES,
    charged_failures,
    chain_outcome,
    resume_point,
    stale_runs,
    validate_journal_entry,
)
from asop.manifest import (
    ADOPTION_REQUIRED,
    STORE_FIELDS,
    adoption_impact,
    adoption_target,
    check_pinned,
    require_schema,
    verify_manifest,
    body_digest,
    canonical_json,
    digest_of,
    is_pinned,
    manifest_digest,
    manifest_resolver,
    pin_manifest,
    validate_adoption,
    validate_manifest,
)
from asop.ownership import (
    ADMINISTRATORS_ENV_VAR,
    TRANSFER_RULES,
    TRANSFER_SCHEMA,
    administrators_from_env,
    apply_transfer,
    check_flip,
    ownership_of,
    validate_transfer,
)
from asop.sop import (
    ASOP,
    ASOP_VERSION,
    DEFAULT_PROTECTED_TAGS,
    MAX_DEPTH,
    MAX_STEPS,
    ROLE_KINDS,
    SOP,
    SopContractError,
    SopError,
    SopStatus,
    Step,
    validate_asop,
    validate_fields,
    validate_step,
)

SCHEMA_VERSION = 1

__all__ = [
    "SCHEMA_VERSION",
    "Refusal",
    "GATE_KINDS",
    "GATE_FIELDS",
    "ATTESTATION_FIELDS",
    "ON_TIMEOUT",
    "VERIFY_CAPABILITY",
    "GATE_INVALID",
    "ATTESTATION_INVALID",
    "ATTESTATION_REQUIRED",
    "validate_gate",
    "validate_attestation",
    "attestation_passes",
    "retry_decision",
    "ASOP",
    "ASOP_VERSION",
    "DEFAULT_PROTECTED_TAGS",
    "Step",
    "MAX_STEPS",
    "MAX_DEPTH",
    "ROLE_KINDS",
    "validate_asop",
    "validate_step",
    "SOP",
    "SopStatus",
    "SopError",
    "SopContractError",
    "validate_fields",
    # hand-offs (ASOP.md §3.7, v3.5)
    "CARDINALITIES",
    "FILING_REQUEST_SCHEMA",
    "HANDOFF_RULES",
    "JOIN_STATES",
    "REQUIRE_KINDS",
    "ChainContractError",
    "validate_chain_fields",
    "validate_handoff_targets",
    "validate_filing_request",
    "plan_handoffs",
    "plan_escalation",
    "plan_join",
    "join_state",
    "requires_satisfied",
    # v3.6 — gates (ASOP.md §5.2)
    "GATE_OUTCOMES",
    "gate_outcome",
    "gate_satisfied",
    "timeout_resolution",
    # v3.6 — entry gates, rework, ordered fan-outs (§3.7–§3.9)
    "MAX_REWORK_ROUNDS",
    "REWORK_RULES",
    "fanout_startable",
    "plan_entry",
    "plan_rework",
    "preflight_checks",
    # v3.6 — the chain manifest and adoption (§3.10)
    "ADOPTION_REQUIRED",
    "adoption_impact",
    # v3.6.1 — the member digest, normative (§3.10)
    "STORE_FIELDS",
    "body_digest",
    "canonical_json",
    "digest_of",
    # v3.6.1 — storing chain records (§10)
    "adoption_target",
    "check_pinned",
    "require_schema",
    "validate_approval",
    "verify_manifest",
    "is_pinned",
    "manifest_digest",
    "manifest_resolver",
    "pin_manifest",
    "validate_adoption",
    "validate_manifest",
    # v3.6.1 — administrative ownership transfer (§7.2)
    "ADMINISTRATORS_ENV_VAR",
    "TRANSFER_RULES",
    "TRANSFER_SCHEMA",
    "administrators_from_env",
    "apply_transfer",
    "check_flip",
    "ownership_of",
    "validate_transfer",
    # v3.6 — journal, resume, staleness, chain outcome (§5.6–§5.8)
    "FAULT_CLASSES",
    "charged_failures",
    "chain_outcome",
    "resume_point",
    "stale_runs",
    "validate_journal_entry",
]
