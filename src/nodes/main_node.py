"""AgentCore Platform v1.0"""

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.renewal_exception_router_service import (
    classify_exception,
    explain_route,
    verify_evidence,
)


class MainNode(FunctionNode):
    """Step 2 (verify renewal evidence) + Step 3 (classify exception) —
    bounded, deterministic; never decides a commercial or renewal outcome,
    only routes for human review.

    `explain_route()` (called with no `llm`, i.e. deterministic template
    mode — no LLM is configured for this template) narrates the
    already-decided route; it never influences `primary_route` or
    `route_reason`.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        rejection_reason = state.get("rejection_reason")

        if rejection_reason:
            route, reason, secondary = classify_exception([], rejected=True)
            explanation = explain_route(route, reason, [])
            emit_trace_event("exception_classified", {"route": route}, state)
            return {
                "evidence_findings": json.dumps([]),
                "rule_version": None,
                "primary_route": route,
                "route_reason": reason,
                "secondary_flags": json.dumps(secondary),
                "route_explanation": explanation,
                "status": AgentStatus.SUCCESS.value,
            }

        normalized_raw = state.get("normalized_record")
        normalized = json.loads(normalized_raw) if normalized_raw else {}

        renewal_window_end = normalized.get("renewal_window_end")
        entitlement = normalized.get("entitlement", {})
        asset = normalized.get("asset", {})
        pricing_reference = normalized.get("pricing_reference", {})
        contact_consent = normalized.get("contact_consent", {})
        rule_version = normalized.get("rule_version")

        findings = verify_evidence(renewal_window_end, entitlement, asset, pricing_reference, contact_consent)
        route, reason, secondary = classify_exception(findings, rejected=False)
        explanation = explain_route(route, reason, findings)

        emit_trace_event(
            "evidence_verified",
            {"finding_count": len(findings), "rule_version": rule_version},
            state,
        )
        emit_trace_event("exception_classified", {"route": route}, state)

        return {
            # ensure_ascii=False: keep downstream scans able to match
            # literal non-ASCII content instead of \uXXXX escapes.
            "evidence_findings": json.dumps(findings, ensure_ascii=False),
            "rule_version": rule_version,
            "primary_route": route,
            "route_reason": reason,
            "secondary_flags": json.dumps(secondary, ensure_ascii=False),
            "route_explanation": explanation,
            "status": AgentStatus.SUCCESS.value,
        }
