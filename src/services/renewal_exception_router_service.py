"""AgentCore Platform v1.0"""

# Service layer: deterministic domain logic for the service-contract
# renewal exception router.
#
# Deterministic-vs-LLM boundary (playbook A4): every routing/verification
# decision function here is pure and deterministic (evidence verification,
# exception classification). None accepts an `llm` parameter. The one
# exception is `explain_route()`, explicitly bounded per proposal §4 Step 3
# ("use bounded LLM explanation only for human-readable rationale without
# inventing evidence") — it narrates a decision already made elsewhere and
# can never return a route or status. Must NOT contain agenticstar/framework
# imports (service layer is framework-agnostic).

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

# Primary route values (proposal §4 Step 3).
ROUTE_ENTITLEMENT_VERIFICATION = "entitlement_verification"
ROUTE_ASSET_DATA_CORRECTION = "asset_data_correction"
ROUTE_PRICING_MANUAL_EXCEPTION_REVIEW = "pricing_manual_exception_review"
ROUTE_CONSENT_CONTACT_UPDATE = "consent_contact_update"
ROUTE_RENEWAL_READY = "renewal_ready"
ROUTE_MANUAL_EXCEPTION_REVIEW = "manual_exception_review"

# Per-field evidence finding status values (proposal §4 Step 2).
FINDING_PRESENT = "present"
FINDING_MISSING = "missing"
FINDING_EXPIRED = "expired"
FINDING_CONFLICTING = "conflicting"

# Field categories mapped to their accountable route when a finding is not
# "present" (proposal §4 Step 3 / §5 route list). Order here also defines
# the primary-route priority (first category with a non-present finding
# wins the primary route; the rest become secondary flags — proposal §4
# note: "identify one primary accountable queue and preserve secondary
# flags"). renewal_window is highest priority: an expired/unparseable
# renewal window means the record should never reach renewal_ready
# regardless of whether the other 4 evidence categories are clean — proposal
# §4 note: this is a pre-renewal readiness gate, and a record whose renewal
# window has already lapsed is definitionally not renewal-ready (Idea/§8
# "Core Bottleneck").
_FIELD_CATEGORY_TO_ROUTE: dict[str, str] = {
    "renewal_window": ROUTE_MANUAL_EXCEPTION_REVIEW,
    "entitlement": ROUTE_ENTITLEMENT_VERIFICATION,
    "asset": ROUTE_ASSET_DATA_CORRECTION,
    "pricing_reference": ROUTE_PRICING_MANUAL_EXCEPTION_REVIEW,
    "contact_consent": ROUTE_CONSENT_CONTACT_UPDATE,
}
_FIELD_CATEGORY_PRIORITY = ("renewal_window", "entitlement", "asset", "pricing_reference", "contact_consent")

# Personal/commercial-sensitive field names (proposal Risk #3) that must
# never appear verbatim in an S-4 audit payload — mirrors the
# credential-handling pattern: log the fact, never the value.
SENSITIVE_FIELD_NAMES = frozenset(
    {
        "contact_name",
        "contact_email",
        "contact_phone",
        "negotiated_discount_pct",
        "commercial_notes",
    }
)


def minimize_sensitive_fields(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """S-2 data minimization (proposal §10 Angle 5: "minimization and
    redaction of contact/commercial data").

    Strips any top-level field matching SENSITIVE_FIELD_NAMES from a
    record-shaped dict before it is normalized/threaded through state.
    Returns (minimized_payload, stripped_field_names) — the caller emits
    stripped_field_names in an S-4 audit event (names only, never values).

    Retained for its own unit test coverage and any simple flat-dict
    caller; `normalize_renewal_record()` uses the recursive version below
    instead (T2-1 fix — top-level-only stripping left nested sensitive
    fields, e.g. a secondary contact block inside contact_consent, in
    normalized_record unchanged).
    """
    stripped = [k for k in payload if k in SENSITIVE_FIELD_NAMES]
    if not stripped:
        return payload, []
    minimized = {k: v for k, v in payload.items() if k not in SENSITIVE_FIELD_NAMES}
    return minimized, stripped


def minimize_sensitive_fields_recursive(payload: Any) -> tuple[Any, list[str]]:
    """Recursive counterpart to minimize_sensitive_fields() (nested-field fix) —
    walks nested dicts/lists and strips SENSITIVE_FIELD_NAMES at every
    level, not just the top level of a single category dict. Applied to
    the entire renewal record (contract, entitlement, asset,
    pricing_reference, contact_consent) in normalize_renewal_record(), since
    a sensitive field can appear inside any nested structure any of those
    five categories may carry (e.g. a secondary/billing contact block
    nested inside contact_consent, or a discount breakdown nested inside
    pricing_reference).

    Recursion boundary: this function scans and strips by field NAME at
    every dict level, in dicts nested inside lists, and lists nested
    inside dicts — the full JSON-serializable structure produced by
    normalize_renewal_record() (ADR-005: no non-JSON types reach this
    boundary). It intentionally does not attempt free-text value scanning
    (e.g. detecting an email address embedded in a string under an
    unlisted key) — that is a fundamentally different, pattern-based
    detection problem, not a schema-minimization problem, and is out of
    scope for this fix (see docs/02_design.md "Fix — recursive sensitive-
    field minimization" for the explicit boundary statement).

    Returns (minimized_payload, stripped_field_names) — stripped_field_names
    is deduplicated and sorted for stable, name-only S-4 audit logging.
    """
    stripped: set[str] = set()

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            result = {}
            for key, value in node.items():
                if key in SENSITIVE_FIELD_NAMES:
                    stripped.add(key)
                    continue
                result[key] = _walk(value)
            return result
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    minimized = _walk(payload)
    return minimized, sorted(stripped)


def normalize_renewal_record(
    contract: dict[str, Any],
    entitlement: dict[str, Any],
    asset: dict[str, Any],
    pricing_reference: dict[str, Any],
    contact_consent: dict[str, Any],
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    """Step 1 — normalize and validate the renewal record (S-1
    provenance/schema check).

    Returns None (caller treats as rejection) when the packet is missing a
    contract id, an authorized source, or a rule version — required for a
    manual-review-safe default rather than inferring missing authority
    (proposal §4 note: "If an approved source is unavailable or rule
    version is unknown, return manual exception review; do not infer
    eligibility").
    """
    if not contract.get("contract_id") or not contract.get("renewal_window_end"):
        return None
    if not rules.get("source_authorized"):
        return None
    if not rules.get("version"):
        return None

    # Recursive minimization (T2-1 fix) applied across all 4 source
    # categories — not just contact_consent/pricing_reference — since a
    # sensitive field can appear nested inside entitlement or asset data
    # just as easily (e.g. an asset record carrying an embedded contact
    # block for the site technician).
    entitlement, stripped_entitlement_fields = minimize_sensitive_fields_recursive(entitlement)
    asset, stripped_asset_fields = minimize_sensitive_fields_recursive(asset)
    pricing_reference, stripped_pricing_fields = minimize_sensitive_fields_recursive(pricing_reference)
    contact_consent, stripped_contact_fields = minimize_sensitive_fields_recursive(contact_consent)
    stripped_fields = sorted(
        set(stripped_entitlement_fields)
        | set(stripped_asset_fields)
        | set(stripped_pricing_fields)
        | set(stripped_contact_fields)
    )

    return {
        "contract_id": contract["contract_id"],
        "renewal_window_end": contract["renewal_window_end"],
        "entitlement": entitlement,
        "asset": asset,
        "pricing_reference": pricing_reference,
        "contact_consent": contact_consent,
        "rule_version": rules["version"],
        "escalation_directory": rules.get("escalation_directory", {}),
        "stripped_sensitive_fields": stripped_fields,
    }


def verify_renewal_window(renewal_window_end: str | None, as_of: date | None = None) -> dict[str, Any]:
    """Validate the contract's renewal_window_end as an ISO date and compare
    it against today. Unlike the other 4 evidence categories, this field is
    not optional evidence to reconcile against a source — it is the very
    thing that makes this a *pre-renewal* readiness check (proposal Idea
    §8 "Core Bottleneck"). A missing, unparseable, or already-passed
    renewal window must never resolve to FINDING_PRESENT.

    Returns a finding dict {"category": "renewal_window", "status": ...,
    "source": "contract"}:
      - FINDING_MISSING   — renewal_window_end absent (should not normally
        happen; normalize_renewal_record() already requires it non-empty,
        this is a second, independent check)
      - FINDING_EXPIRED   — unparseable (ambiguous — proposal §4 note: "do
        not infer eligibility") OR the date has already passed
      - FINDING_PRESENT   — a valid ISO date strictly in the future (or
        today)
    """
    if not renewal_window_end:
        return {"category": "renewal_window", "status": FINDING_MISSING, "source": "contract"}
    try:
        window_end = date.fromisoformat(renewal_window_end)
    except ValueError:
        # Unparseable renewal window is ambiguous, not evidence of
        # readiness — fail safe to EXPIRED (routes to manual review) rather
        # than silently treating bad data as a valid future date.
        return {"category": "renewal_window", "status": FINDING_EXPIRED, "source": "contract"}
    today = as_of or date.today()
    if today > window_end:
        return {"category": "renewal_window", "status": FINDING_EXPIRED, "source": "contract"}
    return {"category": "renewal_window", "status": FINDING_PRESENT, "source": "contract"}


def verify_evidence(
    renewal_window_end: str | None,
    entitlement: dict[str, Any],
    asset: dict[str, Any],
    pricing_reference: dict[str, Any],
    contact_consent: dict[str, Any],
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Step 2 — compare each required source category (including the
    contract's renewal window itself) against the approved reference and
    produce a findings list.

    Each of the 4 evidence-source category dicts is expected to optionally
    carry:
      - present: bool (defaults to True if the dict is non-empty)
      - expires_on: ISO date string, optional
      - conflict: bool, optional (explicit conflict flag from the source
        reconciliation step upstream of this agent)

    Returns a list of 5 findings (renewal_window plus the 4 evidence
    categories), each shaped as {"category": str, "status": str, "source": str}.
    """
    today = as_of or date.today()
    categories = {
        "entitlement": entitlement,
        "asset": asset,
        "pricing_reference": pricing_reference,
        "contact_consent": contact_consent,
    }

    findings: list[dict[str, Any]] = [verify_renewal_window(renewal_window_end, as_of=today)]
    for category, data in categories.items():
        source = data.get("source", category)

        if not data or data.get("present") is False:
            findings.append({"category": category, "status": FINDING_MISSING, "source": source})
            continue

        if data.get("conflict"):
            findings.append({"category": category, "status": FINDING_CONFLICTING, "source": source})
            continue

        expires_on_str = data.get("expires_on")
        if expires_on_str:
            try:
                expires_on = date.fromisoformat(expires_on_str)
                if today > expires_on:
                    findings.append({"category": category, "status": FINDING_EXPIRED, "source": source})
                    continue
            except ValueError:
                # Unparseable expiry: fail-safe to present-so-far rather
                # than inferring an expired state from bad data (proposal
                # §4 note — "do not infer eligibility").
                pass

        findings.append({"category": category, "status": FINDING_PRESENT, "source": source})

    return findings


def classify_exception(
    findings: list[dict[str, Any]],
    rejected: bool,
) -> tuple[str, str, list[str]]:
    """Step 3 — deterministic bounded routing rule. Never decides a
    commercial or renewal outcome, only where the record should be routed
    for human review.

    Returns (primary_route, reason, secondary_flags).
    """
    if rejected:
        return (
            ROUTE_MANUAL_EXCEPTION_REVIEW,
            "renewal record failed normalization/authorization checks",
            [],
        )

    non_present = {f["category"]: f["status"] for f in findings if f["status"] != FINDING_PRESENT}

    if not non_present:
        return ROUTE_RENEWAL_READY, "all required evidence present and within validity window", []

    # Primary route: first category (by fixed priority) with a non-present
    # finding. All other non-present categories become secondary flags.
    primary_category = next((c for c in _FIELD_CATEGORY_PRIORITY if c in non_present), None)
    if primary_category is None:
        # Defensive: a finding referenced an unknown category — route to
        # manual review rather than silently dropping it.
        return ROUTE_MANUAL_EXCEPTION_REVIEW, "unrecognized evidence category in findings", []

    primary_route = _FIELD_CATEGORY_TO_ROUTE[primary_category]
    primary_status = non_present[primary_category]
    reason = f"{primary_category} evidence is {primary_status}"

    secondary_flags = [
        f"{category}:{status}" for category, status in non_present.items() if category != primary_category
    ]

    return primary_route, reason, secondary_flags


def select_human_queue(route: str, escalation_directory: dict[str, Any]) -> str:
    """Map a classified primary route to the authorized human queue name."""
    defaults = {
        ROUTE_ENTITLEMENT_VERIFICATION: "entitlement_data_steward_queue",
        ROUTE_ASSET_DATA_CORRECTION: "asset_data_steward_queue",
        ROUTE_PRICING_MANUAL_EXCEPTION_REVIEW: "pricing_exception_queue",
        ROUTE_CONSENT_CONTACT_UPDATE: "contact_consent_update_queue",
        ROUTE_MANUAL_EXCEPTION_REVIEW: "manual_exception_review_queue",
        ROUTE_RENEWAL_READY: "renewal_operations_queue",
    }
    fallback = defaults.get(route, "manual_exception_review_queue")
    return str(escalation_directory.get(route, fallback))


class ExplanationLLM(Protocol):
    """Minimal LLM boundary for explain_route() — a single narrow method so
    the service layer stays framework-agnostic (no concrete LLM client
    import) while still being explicit about the contract."""

    def generate(self, prompt: str) -> str: ...


def explain_route(
    route: str,
    reason: str,
    findings: list[dict[str, Any]],
    llm: ExplanationLLM | None = None,
) -> str | None:
    """Bounded narrative explanation of an ALREADY-DECIDED route (proposal
    §4 Step 3: "use bounded LLM explanation only for human-readable
    rationale without inventing evidence").

    This function never influences `route` or `reason` — both are passed in
    already final. When `llm` is None (the default — no LLM configured),
    returns a deterministic template string instead of None, so the field
    is populated without requiring an LLM dependency.
    """
    non_present = [f["category"] for f in findings if f["status"] != FINDING_PRESENT]

    if llm is None:
        if route == ROUTE_RENEWAL_READY:
            return "All required entitlement, asset, pricing-reference, and consent evidence is present and current."
        if non_present:
            return f"Route '{route}': {reason}. Affected evidence categories: {', '.join(sorted(non_present))}."
        return f"Route '{route}': {reason}."

    prompt = (
        "Narrate the following already-decided renewal-evidence routing outcome in one or "
        "two plain-language sentences for a service-contract operations analyst. Do NOT "
        "invent evidence, approve the renewal, or imply a commercial decision — only "
        f"describe the evidence gap that was found.\nRoute: {route}\nReason: {reason}\n"
        f"Affected categories: {sorted(non_present)}"
    )
    return llm.generate(prompt)


def build_handoff_packet(
    contract_id: str,
    rule_version: str,
    findings: list[dict[str, Any]],
    primary_route: str,
    reason: str,
    secondary_flags: list[str],
    human_queue: str,
    explanation: str | None = None,
) -> dict[str, Any]:
    """Step 4 — render the sanitized, source-cited handoff packet."""
    packet: dict[str, Any] = {
        "contract_id": contract_id,
        "rule_version": rule_version,
        "evidence_findings": findings,
        "primary_route": primary_route,
        "reason": reason,
        "secondary_flags": secondary_flags,
        "human_queue": human_queue,
        "advisory_only": True,
        "notice": (
            "This packet is a data-readiness routing aid. It is not a renewal decision, "
            "pricing approval, offer, or contract action. Only the assigned human reviewer "
            "may approve an exception or execute a renewal."
        ),
    }
    if explanation is not None:
        packet["explanation"] = explanation
    return packet
