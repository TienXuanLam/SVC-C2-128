"""AgentCore Platform v1.0"""

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.renewal_exception_router_service import (
    SENSITIVE_FIELD_NAMES,
    build_handoff_packet,
    select_human_queue,
)


class PostProcessNode(FunctionNode):
    """Step 4 — render secure handoff: build structured cited packet,
    apply S-3 sensitive-field leak check, route only through the approved
    escalation directory."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        # Defense-in-depth: SENSITIVE_FIELD_NAMES are already stripped
        # upstream by minimize_sensitive_fields() in PreProcessNode, but
        # this re-checks the final serialized packet before it leaves the
        # node boundary — block if a sensitive field name ever leaked
        # through (S-3 convention: raise).
        packet_raw = result.get("handoff_packet")
        if packet_raw:
            for field_name in SENSITIVE_FIELD_NAMES:
                if f'"{field_name}"' in packet_raw:
                    raise RuntimeError(f"S-3: sensitive field '{field_name}' leaked into handoff_packet")
        return result

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized_raw = state.get("normalized_record")
        normalized = json.loads(normalized_raw) if normalized_raw else {}

        findings_raw = state.get("evidence_findings")
        findings = json.loads(findings_raw) if findings_raw else []

        secondary_flags_raw = state.get("secondary_flags")
        secondary_flags = json.loads(secondary_flags_raw) if secondary_flags_raw else []

        route = state.get("primary_route", "manual_exception_review")
        reason = state.get("route_reason", "")
        explanation = state.get("route_explanation")
        contract_id = normalized.get("contract_id", "unknown")
        rule_version = state.get("rule_version") or "unknown"
        escalation_directory = normalized.get("escalation_directory", {})

        human_queue = select_human_queue(route, escalation_directory)

        packet = build_handoff_packet(
            contract_id=contract_id,
            rule_version=rule_version,
            findings=findings,
            primary_route=route,
            reason=reason,
            secondary_flags=secondary_flags,
            human_queue=human_queue,
            explanation=explanation,
        )

        emit_trace_event(
            "handoff_packet_routed",
            {"route": route, "human_queue": human_queue},
            state,
        )

        return {
            # ensure_ascii=False: keep any literal non-ASCII content
            # matchable by the S-3 leak-check scan above.
            "handoff_packet": json.dumps(packet, ensure_ascii=False),
            "human_queue": human_queue,
            "formatted_output": packet,
            "status": AgentStatus.SUCCESS.value,
        }
