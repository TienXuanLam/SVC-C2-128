# SVC-C2-128 — Unit Tests: MainNode (Step 2 verify + Step 3 classify)

import inspect
import json
from datetime import date, timedelta

from framework.schemas.agent_status import AgentStatus
from src.nodes.main_node import MainNode

_FUTURE_WINDOW = (date.today() + timedelta(days=90)).isoformat()


def _normalized_record(**overrides):
    base = {
        "contract_id": "C-1001",
        "renewal_window_end": _FUTURE_WINDOW,
        "entitlement": {"present": True},
        "asset": {"present": True},
        "pricing_reference": {"present": True},
        "contact_consent": {"present": True},
        "rule_version": "v1",
        "escalation_directory": {},
    }
    base.update(overrides)
    return base


class TestMainNode:
    def setup_method(self):
        self.node = MainNode()

    def test_rejected_upstream_routes_manual_exception_review(self):
        state = {
            "rejection_reason": "missing authorization",
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["primary_route"] == "manual_exception_review"
        assert result["route_explanation"]

    def test_all_present_routes_renewal_ready(self):
        state = {
            "rejection_reason": None,
            "normalized_record": json.dumps(_normalized_record()),
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["primary_route"] == "renewal_ready"
        findings = json.loads(result["evidence_findings"])
        assert len(findings) == 5  # renewal_window + 4 evidence categories

    def test_expired_renewal_window_never_routes_renewal_ready(self):
        # Blocker fix (T1-1): an expired renewal_window_end must never
        # reach renewal_ready even when all 4 evidence categories are
        # otherwise clean.
        past_window = (date.today() - timedelta(days=1)).isoformat()
        state = {
            "rejection_reason": None,
            "normalized_record": json.dumps(_normalized_record(renewal_window_end=past_window)),
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert result["primary_route"] != "renewal_ready"
        assert result["primary_route"] == "manual_exception_review"
        findings = json.loads(result["evidence_findings"])
        statuses = {f["category"]: f["status"] for f in findings}
        assert statuses["renewal_window"] == "expired"

    def test_unparseable_renewal_window_never_routes_renewal_ready(self):
        state = {
            "rejection_reason": None,
            "normalized_record": json.dumps(_normalized_record(renewal_window_end="not-a-date")),
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert result["primary_route"] != "renewal_ready"

    def test_asset_missing_routes_asset_data_correction(self):
        state = {
            "rejection_reason": None,
            "normalized_record": json.dumps(_normalized_record(asset={"present": False})),
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert result["primary_route"] == "asset_data_correction"

    def test_secondary_flags_present_with_multiple_gaps(self):
        state = {
            "rejection_reason": None,
            "normalized_record": json.dumps(
                _normalized_record(asset={"present": False}, pricing_reference={"present": False})
            ),
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        secondary = json.loads(result["secondary_flags"])
        assert secondary == ["pricing_reference:missing"]

    def test_route_explanation_present_on_every_path(self):
        state = {
            "rejection_reason": None,
            "normalized_record": json.dumps(_normalized_record()),
            "node_history": [],
            "error_log": [],
        }
        result = self.node.execute(state)
        assert result["route_explanation"]
        assert "approve" not in result["route_explanation"].lower()

    def test_execute_method_signature(self):
        """Node contract: Node must implement execute(state) not _invoke_impl."""
        assert hasattr(MainNode, "execute"), "MainNode must implement execute()"

        sig = inspect.signature(MainNode.execute)
        params = list(sig.parameters.keys())
        assert len(params) >= 2, f"execute() must accept (self, state), got params: {params}"
        assert params[1] == "state", f"Second parameter must be 'state', got '{params[1]}'"

        assert "_invoke_impl" not in MainNode.__dict__, (
            "_invoke_impl() must not be defined in MainNode — use execute() instead"
        )
