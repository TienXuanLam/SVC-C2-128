# SVC-C2-128 — Unit Tests: renewal_exception_router_service (deterministic domain logic)

import inspect
from datetime import date, timedelta

from src.services.renewal_exception_router_service import (
    ROUTE_ASSET_DATA_CORRECTION,
    ROUTE_CONSENT_CONTACT_UPDATE,
    ROUTE_ENTITLEMENT_VERIFICATION,
    ROUTE_MANUAL_EXCEPTION_REVIEW,
    ROUTE_PRICING_MANUAL_EXCEPTION_REVIEW,
    ROUTE_RENEWAL_READY,
    SENSITIVE_FIELD_NAMES,
    build_handoff_packet,
    classify_exception,
    explain_route,
    minimize_sensitive_fields,
    minimize_sensitive_fields_recursive,
    normalize_renewal_record,
    select_human_queue,
    verify_evidence,
    verify_renewal_window,
)


class TestDeterministicSignatures:
    """Playbook A4: routing/verification decisions must be deterministic —
    no function here may accept an `llm` parameter. explain_route() is the
    sole, explicit exception (proposal §4 Step 3: bounded narration of an
    already-decided route) — verified separately below."""

    def test_no_llm_parameter_in_any_decision_function(self):
        for fn in (
            normalize_renewal_record,
            minimize_sensitive_fields,
            verify_renewal_window,
            verify_evidence,
            classify_exception,
            select_human_queue,
            build_handoff_packet,
        ):
            assert "llm" not in inspect.signature(fn).parameters, (
                f"{fn.__name__} must remain deterministic (no llm param)"
            )

    def test_explain_route_llm_param_is_the_sole_documented_exception(self):
        assert "llm" in inspect.signature(explain_route).parameters
        assert inspect.signature(explain_route).parameters["llm"].default is None


class TestNormalizeRenewalRecord:
    def _valid_args(self, **overrides):
        args = dict(
            contract={"contract_id": "C-1", "renewal_window_end": "2026-12-31"},
            entitlement={},
            asset={},
            pricing_reference={},
            contact_consent={},
            rules={"version": "v1", "source_authorized": True, "escalation_directory": {}},
        )
        args.update(overrides)
        return args

    def test_rejects_missing_contract_id(self):
        args = self._valid_args(contract={"renewal_window_end": "2026-12-31"})
        assert normalize_renewal_record(**args) is None

    def test_rejects_missing_renewal_window_end(self):
        args = self._valid_args(contract={"contract_id": "C-1"})
        assert normalize_renewal_record(**args) is None

    def test_rejects_unauthorized_source(self):
        args = self._valid_args(rules={"version": "v1", "source_authorized": False})
        assert normalize_renewal_record(**args) is None

    def test_rejects_missing_rule_version(self):
        args = self._valid_args(rules={"source_authorized": True})
        assert normalize_renewal_record(**args) is None

    def test_accepts_valid_record(self):
        result = normalize_renewal_record(**self._valid_args())
        assert result is not None
        assert result["contract_id"] == "C-1"
        assert result["rule_version"] == "v1"
        assert result["stripped_sensitive_fields"] == []

    def test_strips_sensitive_contact_and_pricing_fields(self):
        args = self._valid_args(
            contact_consent={"contact_email": "a@example.com", "consent_given": True},
            pricing_reference={"negotiated_discount_pct": 15, "list_price": 1000},
        )
        result = normalize_renewal_record(**args)
        assert "contact_email" not in result["contact_consent"]
        assert "negotiated_discount_pct" not in result["pricing_reference"]
        assert result["contact_consent"]["consent_given"] is True
        assert result["pricing_reference"]["list_price"] == 1000
        assert set(result["stripped_sensitive_fields"]) == {"contact_email", "negotiated_discount_pct"}

    def test_strips_nested_sensitive_fields_in_contact_consent(self):
        # T2-1 fix: a sensitive field nested inside contact_consent (not at
        # its top level) must also be stripped.
        args = self._valid_args(
            contact_consent={
                "consent_given": True,
                "secondary_contact": {"contact_phone": "555-0100", "role": "billing"},
            },
        )
        result = normalize_renewal_record(**args)
        assert "contact_phone" not in result["contact_consent"]["secondary_contact"]
        assert result["contact_consent"]["secondary_contact"]["role"] == "billing"
        assert "contact_phone" in result["stripped_sensitive_fields"]

    def test_strips_nested_sensitive_fields_in_entitlement_and_asset(self):
        # T2-1 fix: minimization previously only applied to contact_consent
        # and pricing_reference — entitlement and asset were never scanned
        # at all, top-level or nested.
        args = self._valid_args(
            entitlement={"present": True, "commercial_notes": "internal discount note"},
            asset={"present": True, "site_contact": {"contact_name": "Jane Doe"}},
        )
        result = normalize_renewal_record(**args)
        assert "commercial_notes" not in result["entitlement"]
        assert "contact_name" not in result["asset"]["site_contact"]
        assert result["entitlement"]["present"] is True
        assert set(result["stripped_sensitive_fields"]) == {"commercial_notes", "contact_name"}


class TestMinimizeSensitiveFields:
    def test_strips_known_sensitive_fields(self):
        payload = {"contact_name": "Jane Doe", "commercial_notes": "discount approved", "contract_id": "C-1"}
        minimized, stripped = minimize_sensitive_fields(payload)
        assert "contact_name" not in minimized
        assert "commercial_notes" not in minimized
        assert minimized["contract_id"] == "C-1"
        assert set(stripped) == {"contact_name", "commercial_notes"}

    def test_no_sensitive_fields_returns_unchanged(self):
        payload = {"contract_id": "C-1"}
        minimized, stripped = minimize_sensitive_fields(payload)
        assert minimized == payload
        assert stripped == []

    def test_stripped_names_never_include_values(self):
        payload = {"contact_email": "secret@example.com"}
        _, stripped = minimize_sensitive_fields(payload)
        assert stripped == ["contact_email"]
        assert "secret@example.com" not in str(stripped)

    def test_all_declared_sensitive_field_names_are_stripped(self):
        payload = {name: "x" for name in SENSITIVE_FIELD_NAMES}
        minimized, stripped = minimize_sensitive_fields(payload)
        assert minimized == {}
        assert set(stripped) == SENSITIVE_FIELD_NAMES


class TestMinimizeSensitiveFieldsRecursive:
    """T2-1 fix: minimize_sensitive_fields() only stripped the top level of
    a single category dict — a sensitive field nested one or more levels
    deep (e.g. contact_consent.secondary_contact.contact_email) persisted
    into normalized_record unchanged. minimize_sensitive_fields_recursive()
    must catch sensitive fields at every nesting level."""

    def test_strips_at_top_level(self):
        payload = {"commercial_notes": "leak", "contract_id": "C-1"}
        minimized, stripped = minimize_sensitive_fields_recursive(payload)
        assert "commercial_notes" not in minimized
        assert minimized["contract_id"] == "C-1"
        assert stripped == ["commercial_notes"]

    def test_strips_inside_nested_dict(self):
        payload = {
            "contact_consent": {
                "consent_given": True,
                "secondary_contact": {"contact_email": "leak@example.com", "role": "billing"},
            }
        }
        minimized, stripped = minimize_sensitive_fields_recursive(payload)
        assert "contact_email" not in minimized["contact_consent"]["secondary_contact"]
        assert minimized["contact_consent"]["secondary_contact"]["role"] == "billing"
        assert minimized["contact_consent"]["consent_given"] is True
        assert stripped == ["contact_email"]

    def test_strips_inside_list_of_dicts(self):
        # Mirrors a pricing_reference tier breakdown or similar list shape.
        payload = {
            "pricing_reference": {
                "tiers": [
                    {"tier": "gold", "negotiated_discount_pct": 15},
                    {"tier": "standard", "list_price": 1000},
                ]
            }
        }
        minimized, stripped = minimize_sensitive_fields_recursive(payload)
        assert "negotiated_discount_pct" not in minimized["pricing_reference"]["tiers"][0]
        assert minimized["pricing_reference"]["tiers"][0]["tier"] == "gold"
        assert minimized["pricing_reference"]["tiers"][1] == {"tier": "standard", "list_price": 1000}
        assert stripped == ["negotiated_discount_pct"]

    def test_no_sensitive_fields_unchanged(self):
        payload = {"a": {"b": [{"c": 1}]}}
        minimized, stripped = minimize_sensitive_fields_recursive(payload)
        assert minimized == payload
        assert stripped == []

    def test_stripped_names_deduplicated_and_sorted(self):
        payload = {
            "entitlement": {"contact_name": "leak1"},
            "asset": {"contact_name": "leak2", "commercial_notes": "leak3"},
        }
        minimized, stripped = minimize_sensitive_fields_recursive(payload)
        assert stripped == sorted(set(stripped))
        assert stripped == ["commercial_notes", "contact_name"]


_FUTURE_WINDOW = (date.today() + timedelta(days=90)).isoformat()


class TestVerifyEvidence:
    def test_all_present(self):
        findings = verify_evidence(
            renewal_window_end=_FUTURE_WINDOW,
            entitlement={"present": True},
            asset={"present": True},
            pricing_reference={"present": True},
            contact_consent={"present": True},
        )
        statuses = {f["category"]: f["status"] for f in findings}
        assert all(status == "present" for status in statuses.values())
        assert len(findings) == 5  # renewal_window + 4 evidence categories

    def test_empty_dict_is_missing(self):
        findings = verify_evidence(
            renewal_window_end=_FUTURE_WINDOW,
            entitlement={},
            asset={"present": True},
            pricing_reference={"present": True},
            contact_consent={"present": True},
        )
        statuses = {f["category"]: f["status"] for f in findings}
        assert statuses["entitlement"] == "missing"

    def test_explicit_present_false_is_missing(self):
        findings = verify_evidence(
            renewal_window_end=_FUTURE_WINDOW,
            entitlement={"present": False},
            asset={"present": True},
            pricing_reference={"present": True},
            contact_consent={"present": True},
        )
        statuses = {f["category"]: f["status"] for f in findings}
        assert statuses["entitlement"] == "missing"

    def test_conflict_flag_is_conflicting(self):
        findings = verify_evidence(
            renewal_window_end=_FUTURE_WINDOW,
            entitlement={"present": True, "conflict": True},
            asset={"present": True},
            pricing_reference={"present": True},
            contact_consent={"present": True},
        )
        statuses = {f["category"]: f["status"] for f in findings}
        assert statuses["entitlement"] == "conflicting"

    def test_past_expires_on_is_expired(self):
        past = (date.today() - timedelta(days=1)).isoformat()
        findings = verify_evidence(
            renewal_window_end=_FUTURE_WINDOW,
            entitlement={"present": True, "expires_on": past},
            asset={"present": True},
            pricing_reference={"present": True},
            contact_consent={"present": True},
        )
        statuses = {f["category"]: f["status"] for f in findings}
        assert statuses["entitlement"] == "expired"

    def test_future_expires_on_is_present(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        findings = verify_evidence(
            renewal_window_end=_FUTURE_WINDOW,
            entitlement={"present": True, "expires_on": future},
            asset={"present": True},
            pricing_reference={"present": True},
            contact_consent={"present": True},
        )
        statuses = {f["category"]: f["status"] for f in findings}
        assert statuses["entitlement"] == "present"

    def test_unparseable_expires_on_not_inferred_expired(self):
        findings = verify_evidence(
            renewal_window_end=_FUTURE_WINDOW,
            entitlement={"present": True, "expires_on": "not-a-date"},
            asset={"present": True},
            pricing_reference={"present": True},
            contact_consent={"present": True},
        )
        statuses = {f["category"]: f["status"] for f in findings}
        assert statuses["entitlement"] == "present"

    def test_source_field_preserved(self):
        findings = verify_evidence(
            renewal_window_end=_FUTURE_WINDOW,
            entitlement={"present": True, "source": "entitlement_master_v3"},
            asset={"present": True},
            pricing_reference={"present": True},
            contact_consent={"present": True},
        )
        entitlement_finding = next(f for f in findings if f["category"] == "entitlement")
        assert entitlement_finding["source"] == "entitlement_master_v3"

    def test_expired_renewal_window_is_included_in_findings(self):
        # Blocker fix (T1-1): a lapsed renewal window must appear as its
        # own finding even when all 4 evidence categories are otherwise
        # clean — it must never be silently absent from evidence_findings.
        past_window = (date.today() - timedelta(days=1)).isoformat()
        findings = verify_evidence(
            renewal_window_end=past_window,
            entitlement={"present": True},
            asset={"present": True},
            pricing_reference={"present": True},
            contact_consent={"present": True},
        )
        statuses = {f["category"]: f["status"] for f in findings}
        assert statuses["renewal_window"] == "expired"


class TestVerifyRenewalWindow:
    """Blocker fix (T1-1): normalize_renewal_record() previously only
    checked renewal_window_end was non-empty — it never parsed or compared
    it against today, so an expired window like "2000-01-01" reached
    FINDING_PRESENT/renewal_ready. verify_renewal_window() closes that gap."""

    def test_missing_is_missing_finding(self):
        finding = verify_renewal_window(None)
        assert finding["status"] == "missing"

    def test_empty_string_is_missing_finding(self):
        finding = verify_renewal_window("")
        assert finding["status"] == "missing"

    def test_unparseable_is_expired_not_present(self):
        # Ambiguous data must never resolve to "present" (proposal §4 note
        # — "do not infer eligibility").
        finding = verify_renewal_window("not-a-date")
        assert finding["status"] == "expired"

    def test_past_date_is_expired(self):
        past = (date.today() - timedelta(days=1)).isoformat()
        finding = verify_renewal_window(past)
        assert finding["status"] == "expired"

    def test_far_past_date_is_expired(self):
        # The exact scenario from the review finding: a renewal window of
        # 2000-01-01 must not reach "present".
        finding = verify_renewal_window("2000-01-01", as_of=date(2026, 1, 1))
        assert finding["status"] == "expired"

    def test_future_date_is_present(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        finding = verify_renewal_window(future)
        assert finding["status"] == "present"

    def test_today_is_present(self):
        today = date.today()
        finding = verify_renewal_window(today.isoformat(), as_of=today)
        assert finding["status"] == "present"

    def test_category_and_source_fields(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        finding = verify_renewal_window(future)
        assert finding["category"] == "renewal_window"
        assert finding["source"] == "contract"


class TestClassifyException:
    def test_rejected_routes_manual_exception_review(self):
        route, reason, secondary = classify_exception([], rejected=True)
        assert route == ROUTE_MANUAL_EXCEPTION_REVIEW
        assert secondary == []

    def test_all_present_routes_renewal_ready(self):
        findings = [{"category": c, "status": "present", "source": c} for c in
                    ("entitlement", "asset", "pricing_reference", "contact_consent")]
        route, reason, secondary = classify_exception(findings, rejected=False)
        assert route == ROUTE_RENEWAL_READY
        assert secondary == []

    def test_entitlement_gap_is_primary_over_others(self):
        findings = [
            {"category": "entitlement", "status": "missing", "source": "x"},
            {"category": "asset", "status": "missing", "source": "x"},
        ]
        route, reason, secondary = classify_exception(findings, rejected=False)
        assert route == ROUTE_ENTITLEMENT_VERIFICATION
        assert secondary == ["asset:missing"]

    def test_asset_gap_when_entitlement_present(self):
        findings = [
            {"category": "entitlement", "status": "present", "source": "x"},
            {"category": "asset", "status": "expired", "source": "x"},
        ]
        route, reason, secondary = classify_exception(findings, rejected=False)
        assert route == ROUTE_ASSET_DATA_CORRECTION

    def test_pricing_gap_when_entitlement_and_asset_present(self):
        findings = [
            {"category": "entitlement", "status": "present", "source": "x"},
            {"category": "asset", "status": "present", "source": "x"},
            {"category": "pricing_reference", "status": "conflicting", "source": "x"},
        ]
        route, reason, secondary = classify_exception(findings, rejected=False)
        assert route == ROUTE_PRICING_MANUAL_EXCEPTION_REVIEW

    def test_consent_gap_is_lowest_priority(self):
        findings = [
            {"category": "entitlement", "status": "present", "source": "x"},
            {"category": "asset", "status": "present", "source": "x"},
            {"category": "pricing_reference", "status": "present", "source": "x"},
            {"category": "contact_consent", "status": "missing", "source": "x"},
        ]
        route, reason, secondary = classify_exception(findings, rejected=False)
        assert route == ROUTE_CONSENT_CONTACT_UPDATE
        assert secondary == []

    def test_unrecognized_category_routes_manual_review(self):
        findings = [{"category": "unknown_category", "status": "missing", "source": "x"}]
        route, reason, secondary = classify_exception(findings, rejected=False)
        assert route == ROUTE_MANUAL_EXCEPTION_REVIEW


class TestSelectHumanQueue:
    def test_uses_escalation_directory_override(self):
        queue = select_human_queue(ROUTE_ENTITLEMENT_VERIFICATION, {ROUTE_ENTITLEMENT_VERIFICATION: "custom_queue"})
        assert queue == "custom_queue"

    def test_falls_back_to_default(self):
        queue = select_human_queue(ROUTE_RENEWAL_READY, {})
        assert queue == "renewal_operations_queue"

    def test_unknown_route_falls_back_to_manual_review_queue(self):
        queue = select_human_queue("some_unknown_route", {})
        assert queue == "manual_exception_review_queue"


class TestExplainRoute:
    def test_no_llm_returns_deterministic_template_for_ready(self):
        explanation = explain_route(ROUTE_RENEWAL_READY, "all present", [])
        assert explanation is not None
        assert "approve" not in explanation.lower()
        assert "decision" not in explanation.lower()

    def test_no_llm_returns_deterministic_template_for_gap(self):
        findings = [{"category": "asset", "status": "missing", "source": "x"}]
        explanation = explain_route(ROUTE_ASSET_DATA_CORRECTION, "asset evidence is missing", findings)
        assert "asset" in explanation

    def test_llm_provided_delegates_to_llm_generate(self):
        calls = []

        class FakeLLM:
            def generate(self, prompt: str) -> str:
                calls.append(prompt)
                return "narrated text"

        findings = [{"category": "asset", "status": "missing", "source": "x"}]
        result = explain_route(ROUTE_ASSET_DATA_CORRECTION, "asset evidence is missing", findings, llm=FakeLLM())
        assert result == "narrated text"
        assert len(calls) == 1
        assert "asset_data_correction" in calls[0]


class TestBuildHandoffPacket:
    def test_packet_is_always_advisory_only(self):
        packet = build_handoff_packet(
            contract_id="C-1",
            rule_version="v1",
            findings=[],
            primary_route=ROUTE_RENEWAL_READY,
            reason="all present",
            secondary_flags=[],
            human_queue="renewal_operations_queue",
        )
        assert packet["advisory_only"] is True
        assert "notice" in packet and packet["notice"]

    def test_packet_never_uses_decision_language_in_route(self):
        for route in (
            ROUTE_ENTITLEMENT_VERIFICATION,
            ROUTE_ASSET_DATA_CORRECTION,
            ROUTE_PRICING_MANUAL_EXCEPTION_REVIEW,
            ROUTE_CONSENT_CONTACT_UPDATE,
            ROUTE_RENEWAL_READY,
            ROUTE_MANUAL_EXCEPTION_REVIEW,
        ):
            for banned in ("approved", "renewed", "charged", "offer_sent"):
                assert banned not in route.lower()

    def test_explanation_included_when_present(self):
        packet = build_handoff_packet(
            contract_id="C-1",
            rule_version="v1",
            findings=[],
            primary_route=ROUTE_RENEWAL_READY,
            reason="all present",
            secondary_flags=[],
            human_queue="renewal_operations_queue",
            explanation="all good",
        )
        assert packet["explanation"] == "all good"

    def test_explanation_omitted_when_absent(self):
        packet = build_handoff_packet(
            contract_id="C-1",
            rule_version="v1",
            findings=[],
            primary_route=ROUTE_RENEWAL_READY,
            reason="all present",
            secondary_flags=[],
            human_queue="renewal_operations_queue",
        )
        assert "explanation" not in packet
