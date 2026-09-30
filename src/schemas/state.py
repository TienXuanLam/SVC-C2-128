"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict (see ADR-005 for the prohibited
# alternatives). LangGraph checkpoints use msgpack serialization, so only
# plain serializable fields are allowed. Do NOT add credentials or secrets.
#
# Dict/list-shaped domain payloads (renewal record, evidence findings) are
# stored as JSON strings (Optional[str]) per ADR-005 — never as nested
# dict/list of arbitrary objects — and (de)serialized with json.dumps/loads
# at the service boundary.

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """State for ServiceContractRenewalExceptionRouterAgent.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.
    """

    # -- Step 1 (pre_process): normalize and protect ------------------------
    # JSON-encoded normalized renewal record: contract id, renewal window,
    # entitlement/asset references, pricing-reference, consent reference,
    # approved rule version — only permitted/authorized fields survive.
    normalized_record: str | None
    # None on success; a human-readable reason when normalization rejects
    # the record (missing authorization, unknown source, schema violation).
    rejection_reason: str | None
    # Names (never values) of contact/commercial fields stripped by S-2
    # minimization. JSON-encoded list[str]; empty list when nothing stripped.
    stripped_sensitive_fields: str | None

    # -- Step 2 (main): verify renewal evidence ------------------------------
    # JSON-encoded findings list: [{field, status, source, rule_version}]
    # status in {present, missing, expired, conflicting}.
    evidence_findings: str | None
    # Approved rule version preserved for citation and audit.
    rule_version: str | None

    # -- Step 3 (main): classify exception -----------------------------------
    # Primary route: entitlement_verification, asset_data_correction,
    # pricing_manual_exception_review, consent_contact_update,
    # renewal_ready, or manual_exception_review.
    primary_route: str | None
    # Short deterministic reason code backing primary_route.
    route_reason: str | None
    # JSON-encoded list[str] of secondary route flags (a record may have
    # multiple findings; only one route is primary/accountable — proposal
    # §4 note).
    secondary_flags: str | None
    # Bounded narrative explanation of the already-decided route; never
    # used to decide the route itself (deterministic template when no LLM
    # is configured).
    route_explanation: str | None

    # -- Step 4 (post_process): route and hand off -----------------------------
    # JSON-encoded sanitized, source-cited handoff packet.
    handoff_packet: str | None
    # Authorized human queue the packet is routed to.
    human_queue: str | None
