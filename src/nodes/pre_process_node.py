"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants (.value) — never plain strings
#  - Read input_context via state.get("input_context", {}) — read-only

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.renewal_exception_router_service import normalize_renewal_record


_MAX_USER_INPUT_LENGTH = 4000


class PreProcessNode(FunctionNode):
    """Step 1 — normalize and protect: validate shape, provenance, and
    permitted fields before evidence verification."""

    # Explicit by design. Renewal records carry commercial/contact-sensitive
    # data — require an authenticated external caller at minimum.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        # SVC is not a regulated-data vertical (that fixed detector-pattern
        # list applies to a different class of templates only), so there is
        # no fixed detector pattern list to apply here. The structured
        # sensitive-data handling for this template is
        # minimize_sensitive_fields() inside normalize_renewal_record()
        # (docs/02_design.md scope note). This hook instead guards against
        # an oversized/malformed free-text user_input reaching execute() —
        # a raw-length sanity check, a pattern also used by other
        # non-regulated-data verticals.
        user_input = state.get("user_input", "")
        if len(user_input) > _MAX_USER_INPUT_LENGTH:
            state = dict(state)
            state["status"] = AgentStatus.ERROR.value
            state["error_log"] = state.get("error_log", []) + [
                f"user_input exceeds {_MAX_USER_INPUT_LENGTH} characters; processing refused."
            ]
            return state
        return state

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        input_context = state.get("input_context", {})  # read-only

        contract = input_context.get("contract", {})
        entitlement = input_context.get("entitlement", {})
        asset = input_context.get("asset", {})
        pricing_reference = input_context.get("pricing_reference", {})
        contact_consent = input_context.get("contact_consent", {})
        rules = input_context.get("rules", {})

        normalized = normalize_renewal_record(contract, entitlement, asset, pricing_reference, contact_consent, rules)

        if normalized is None:
            emit_trace_event(
                "renewal_record_rejected",
                {"reason": "missing authorization, contract id, or rule version"},
                state,
            )
            return {
                "rejection_reason": "missing authorization, contract id, or rule version",
                "status": AgentStatus.SUCCESS.value,  # route to manual exception review, not a hard error
            }

        emit_trace_event(
            "renewal_record_normalized",
            {"contract_id": normalized["contract_id"], "rule_version": normalized["rule_version"]},
            state,
        )

        stripped_fields = normalized.get("stripped_sensitive_fields", [])
        if stripped_fields:
            # S-2 minimization audit event — field NAMES only, never values.
            emit_trace_event(
                "sensitive_fields_minimized",
                {"contract_id": normalized["contract_id"], "field_names": stripped_fields},
                state,
            )

        return {
            # ensure_ascii=False: keep any downstream regulated-data scan
            # able to match literal non-ASCII content instead of \uXXXX escapes.
            "normalized_record": json.dumps(normalized, ensure_ascii=False),
            "rejection_reason": None,
            "stripped_sensitive_fields": json.dumps(stripped_fields, ensure_ascii=False),
            "status": AgentStatus.SUCCESS.value,
        }
