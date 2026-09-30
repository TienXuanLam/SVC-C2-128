# SVC-C2-128 — Unit Tests: PostProcessNode (Step 4 route and hand off)

import json

import pytest
from framework.schemas.agent_status import AgentStatus
from src.nodes.post_process_node import PostProcessNode


class TestPostProcessNode:
    def setup_method(self):
        self.node = PostProcessNode()

    def test_builds_advisory_only_packet(self):
        state = {
            "normalized_record": json.dumps({"contract_id": "C-1001", "escalation_directory": {}}),
            "evidence_findings": json.dumps(
                [{"category": c, "status": "present", "source": c}
                 for c in ("entitlement", "asset", "pricing_reference", "contact_consent")]
            ),
            "secondary_flags": json.dumps([]),
            "primary_route": "renewal_ready",
            "route_reason": "all present",
            "rule_version": "v1",
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        packet = json.loads(result["handoff_packet"])
        assert packet["advisory_only"] is True
        assert result["human_queue"]

    def test_routes_to_asset_queue_for_asset_gap(self):
        state = {
            "normalized_record": json.dumps({"contract_id": "C-1001", "escalation_directory": {}}),
            "evidence_findings": json.dumps([{"category": "asset", "status": "missing", "source": "x"}]),
            "secondary_flags": json.dumps([]),
            "primary_route": "asset_data_correction",
            "route_reason": "gap",
            "rule_version": "v1",
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert result["human_queue"] == "asset_data_steward_queue"

    def test_explanation_included_when_present(self):
        state = {
            "normalized_record": json.dumps({"contract_id": "C-1001", "escalation_directory": {}}),
            "evidence_findings": json.dumps([]),
            "secondary_flags": json.dumps([]),
            "primary_route": "renewal_ready",
            "route_reason": "all present",
            "route_explanation": "All evidence present.",
            "rule_version": "v1",
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        packet = json.loads(result["handoff_packet"])
        assert packet["explanation"] == "All evidence present."

    def test_explanation_omitted_when_absent(self):
        state = {
            "normalized_record": json.dumps({"contract_id": "C-1001", "escalation_directory": {}}),
            "evidence_findings": json.dumps([]),
            "secondary_flags": json.dumps([]),
            "primary_route": "manual_exception_review",
            "route_reason": "rejected",
            "rule_version": "v1",
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        packet = json.loads(result["handoff_packet"])
        assert "explanation" not in packet


class TestPostProcessNodeOutputGate:
    def setup_method(self):
        self.node = PostProcessNode()

    def test_sensitive_field_leak_in_output_blocks(self):
        result = {"handoff_packet": json.dumps({"contact_email": "leaked@example.com"})}
        with pytest.raises(RuntimeError):
            self.node._extra_security_gate_output(result)

    def test_clean_output_passes_through(self):
        result = {"handoff_packet": json.dumps({"notice": "Routine renewal routing"})}
        assert self.node._extra_security_gate_output(result) == result
