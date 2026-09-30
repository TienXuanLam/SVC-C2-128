# SVC-C2-128 — Unit Tests: PreProcessNode (Step 1 normalize and protect)

import json

from framework.schemas.agent_status import AgentStatus
from src.nodes.pre_process_node import PreProcessNode


def _valid_input_context():
    return {
        "contract": {"contract_id": "C-1001", "renewal_window_end": "2026-12-31"},
        "entitlement": {"present": True},
        "asset": {"present": True},
        "pricing_reference": {"present": True},
        "contact_consent": {"present": True},
        "rules": {"version": "v1", "source_authorized": True, "escalation_directory": {}},
    }


class TestPreProcessNode:
    def setup_method(self):
        self.node = PreProcessNode()

    def test_valid_record_normalizes(self):
        state = {
            "user_input": "renewal record",
            "input_context": _valid_input_context(),
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["rejection_reason"] is None
        normalized = json.loads(result["normalized_record"])
        assert normalized["contract_id"] == "C-1001"

    def test_unauthorized_source_rejected(self):
        input_context = _valid_input_context()
        input_context["rules"]["source_authorized"] = False
        state = {
            "user_input": "renewal record",
            "input_context": input_context,
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["rejection_reason"] is not None

    def test_missing_contract_id_rejected(self):
        input_context = _valid_input_context()
        input_context["contract"] = {}
        state = {
            "user_input": "renewal record",
            "input_context": input_context,
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert result["rejection_reason"] is not None

    def test_no_sensitive_fields_yields_empty_stripped_list(self):
        state = {
            "user_input": "renewal record",
            "input_context": _valid_input_context(),
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert json.loads(result["stripped_sensitive_fields"]) == []

    def test_sensitive_fields_stripped_and_audited(self, monkeypatch):
        emitted = []
        monkeypatch.setattr(
            "src.nodes.pre_process_node.emit_trace_event",
            lambda event_type, payload, state: emitted.append((event_type, payload)),
        )
        input_context = _valid_input_context()
        input_context["contact_consent"]["contact_email"] = "a@example.com"
        state = {
            "user_input": "renewal record",
            "input_context": input_context,
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        stripped = json.loads(result["stripped_sensitive_fields"])
        assert stripped == ["contact_email"]
        normalized = json.loads(result["normalized_record"])
        assert "contact_email" not in normalized["contact_consent"]

        minimize_events = [p for t, p in emitted if t == "sensitive_fields_minimized"]
        assert len(minimize_events) == 1
        assert "a@example.com" not in json.dumps(minimize_events[0])
        assert minimize_events[0]["field_names"] == ["contact_email"]


class TestPreProcessNodeInputGate:
    def setup_method(self):
        self.node = PreProcessNode()

    def test_oversized_input_blocks(self):
        state = {
            "user_input": "x" * 4001,
            "input_context": {},
            "node_history": [],
            "error_log": [],
        }
        result = self.node._extra_security_gate_input(state)
        assert result["status"] == AgentStatus.ERROR.value

    def test_normal_input_passes_through(self):
        state = {
            "user_input": "renewal record",
            "input_context": {},
            "node_history": [],
            "error_log": [],
        }
        result = self.node._extra_security_gate_input(state)
        assert result.get("status") != AgentStatus.ERROR.value
