# PB-08 (domain-specific): Sensitive-field minimization and advisory-only
# guarantee boundary verification for SVC-C2-128.
#
# Two boundaries under test:
#  1. S-2 minimization in PreProcessNode must strip SENSITIVE_FIELD_NAMES
#     before normalization, and must never log the stripped value.
#  2. Every handoff packet produced by PostProcessNode carries advisory_only
#     True and never represents a route as a renewal/commercial decision
#     (proposal Risk #5).

import json

from framework.schemas.agent_status import AgentStatus
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode


class TestSensitiveFieldMinimizationBoundary:
    def test_sensitive_value_never_appears_in_audit_payload(self, monkeypatch):
        captured_payloads = []

        def fake_emit(event_type, payload, state):
            captured_payloads.append((event_type, payload))

        monkeypatch.setattr("src.nodes.pre_process_node.emit_trace_event", fake_emit)

        secret_email = "leaked-secret@example.com"
        node = PreProcessNode()
        state = {
            "user_input": "renewal record",
            "input_context": {
                "contract": {"contract_id": "C-1", "renewal_window_end": "2026-12-31"},
                "entitlement": {"present": True},
                "asset": {"present": True},
                "pricing_reference": {"present": True},
                "contact_consent": {"present": True, "contact_email": secret_email},
                "rules": {"version": "v1", "source_authorized": True, "escalation_directory": {}},
            },
            "node_history": [],
            "error_log": [],
        }
        result = node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value

        normalized = json.loads(result["normalized_record"])
        assert "contact_email" not in normalized["contact_consent"]

        for _event_type, payload in captured_payloads:
            assert secret_email not in json.dumps(payload), (
                "stripped sensitive value must never appear in audit payload"
            )

    def test_output_gate_blocks_sensitive_field_leak(self):
        node = PostProcessNode()
        result = {"handoff_packet": json.dumps({"negotiated_discount_pct": 20})}
        raised = False
        try:
            node._extra_security_gate_output(result)
        except RuntimeError:
            raised = True
        assert raised, "S-3 output gate must block a sensitive field leaking into the handoff packet"

    def test_output_gate_blocks_sensitive_field_nested_anywhere_in_packet(self):
        # T2-1 fix: the S-3 gate scans the entire serialized handoff_packet
        # string for a quoted sensitive key, so it catches a leak
        # regardless of nesting depth — verify this holds for a field
        # buried several levels deep, not just at the top level.
        node = PostProcessNode()
        result = {
            "handoff_packet": json.dumps(
                {
                    "evidence_findings": [
                        {"category": "contact_consent", "detail": {"secondary": {"contact_email": "x@example.com"}}}
                    ]
                }
            )
        }
        raised = False
        try:
            node._extra_security_gate_output(result)
        except RuntimeError:
            raised = True
        assert raised, "S-3 output gate must block a sensitive field nested deep inside the handoff packet"

    def test_nested_sensitive_field_stripped_before_reaching_pre_process_output(self, monkeypatch):
        # T2-1 fix: a sensitive field nested inside a category dict (not
        # just top-level) must be stripped by PreProcessNode before it
        # ever reaches normalized_record/state, not merely caught later by
        # the S-3 output gate as a last resort.
        monkeypatch.setattr("src.nodes.pre_process_node.emit_trace_event", lambda *a, **k: None)
        secret_email = "nested-leak@example.com"
        node = PreProcessNode()
        state = {
            "user_input": "renewal record",
            "input_context": {
                "contract": {"contract_id": "C-1", "renewal_window_end": "2099-12-31"},
                "entitlement": {"present": True},
                "asset": {"present": True},
                "pricing_reference": {"present": True},
                "contact_consent": {
                    "present": True,
                    "secondary_contact": {"contact_email": secret_email, "role": "billing"},
                },
                "rules": {"version": "v1", "source_authorized": True, "escalation_directory": {}},
            },
            "node_history": [],
            "error_log": [],
        }
        result = node.execute(state)
        normalized = json.loads(result["normalized_record"])
        assert "contact_email" not in normalized["contact_consent"]["secondary_contact"]
        assert normalized["contact_consent"]["secondary_contact"]["role"] == "billing"
        assert secret_email not in result["normalized_record"]


class TestAdvisoryOnlyBoundary:
    def test_every_route_packet_is_advisory_only(self, monkeypatch):
        monkeypatch.setattr(
            "src.nodes.post_process_node.emit_trace_event",
            lambda *a, **k: None,
        )
        node = PostProcessNode()

        routes = [
            "entitlement_verification",
            "asset_data_correction",
            "pricing_manual_exception_review",
            "consent_contact_update",
            "renewal_ready",
            "manual_exception_review",
        ]
        for route in routes:
            state = {
                "normalized_record": json.dumps({"contract_id": "C-1", "escalation_directory": {}}),
                "evidence_findings": json.dumps([]),
                "secondary_flags": json.dumps([]),
                "primary_route": route,
                "route_reason": "test",
                "rule_version": "v1",
                "node_history": [],
                "error_log": [],
            }
            result = node.execute(state)
            packet = json.loads(result["handoff_packet"])
            assert packet["advisory_only"] is True, f"route={route} must always be advisory_only"
            assert "notice" in packet and packet["notice"]
