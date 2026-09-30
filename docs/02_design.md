# Template Design Specification

## Position in AgentCore Architecture

- **Agent Class**: `ServiceContractRenewalExceptionRouterAgent`
- **L1 Base**: `AgentBaseGraph`
- **Pattern**: Cat 2 — flat backbone (outer `AgentBaseGraph`, no inner subgraph); DocGenerationAgent
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)

## Architecture Overview

> **Flat-backbone vs. `GraphNode` + inner workflow (design review,
> clarified):** the template family ships two valid Cat 2 shapes, not one
> mandatory shape. `src/examples/graph_cat2_sample.py` (marked *"read-only
> reference, delete before release"*) demonstrates one option — an outer
> `AgentBaseGraph` with a `GraphNode` + inner domain-workflow graph in the
> `main` slot — for templates whose domain logic genuinely needs its own
> LangGraph topology (branching, loops, multiple nodes with independent
> routing). The authoritative framework rule ("Cat is a business
> category... it describes what kind of value the template delivers, not
> its internal architecture or number of nodes") states explicitly that
> *"a Cat 2 template can inherit the L1 framework directly and implement
> its own multi-step workflow"* — making the flat-backbone step-helper
> shape (this template's choice) an equally valid Cat 2 architecture, not
> an exception requiring separate sign-off. This template's 4-step
> workflow is a strictly sequential pipeline with no branching, no loop,
> and no step that needs independent LangGraph-level composition (retry,
> parallel fan-out, HITL isolation) — the domain complexity is in the
> *rules*, not the *topology* — so a `GraphNode` + inner graph would add a
> second composition layer with no corresponding control-flow need, while
> still being fully valid if a future revision's domain complexity changed
> that calculus. The same flat-backbone step-helper pattern is already in
> production use across several other templates in the same fleet — this
> is the established fleet convention for sequential multi-step Cat 2
> workflows, not a one-off deviation.

`AgentBaseGraph` exposes exactly three domain slots — `pre_process`, `main`,
`post_process` (`compile()` raises `MissingNodeError` if any is absent;
there is no mechanism to register a 4th domain slot). The source proposal's
4-step spec (source proposal §4: Normalize/protect, Verify
evidence, Classify exception, Render handoff) is implemented as **6
pure-Python step helper functions** in
`src/services/renewal_exception_router_service.py` (plain functions, not
`BaseNode` subclasses — 2 sub-steps folded into Step 1 and Step 3, the
same pattern used by other templates in the fleet), invoked from inside
the 3 real `FunctionNode` slots. This keeps S-1/S-2/S-3 as the *real*
framework gates on `__call__()`, never a second parallel gate system.

```
AgentBaseGraph (src/graph/graph.py):
  InitializeNode      [framework default]
  PreProcessNode      [VERIFIED_EXTERNAL — real FunctionNode / real S-1 gate]
    ├─ normalize_renewal_record(...)   step 1a — source/authority/schema validation
    └─ minimize_sensitive_fields(...)  step 1b — S-2 contact/commercial minimization
  MainNode            [VERIFIED_EXTERNAL — real FunctionNode]
    ├─ verify_evidence(...)            step 2  — 4-category evidence comparison
    ├─ classify_exception(...)         step 3a — bounded deterministic routing
    └─ explain_route(...)              step 3b — bounded narration (llm=None)
  PostProcessNode     [VERIFIED_EXTERNAL — real FunctionNode / real S-3 gate]
    └─ build_handoff_packet(...)       step 4  — sanitized, source-cited packet + human queue
  FinalizeNode        [framework default]
```

### Node Configuration

| Slot (real `FunctionNode`) | Location | required_trust_level | Step helpers invoked (in order) |
|---|---|---|---|
| `pre_process` | src/nodes/pre_process_node.py | VERIFIED_EXTERNAL | 1a. `normalize_renewal_record()` (calls 1b. `minimize_sensitive_fields()` internally) |
| `main` | src/nodes/main_node.py | VERIFIED_EXTERNAL | 2. `verify_evidence()` 3a. `classify_exception()` 3b. `explain_route()` |
| `post_process` | src/nodes/post_process_node.py | VERIFIED_EXTERNAL | 4. `build_handoff_packet()` |

Step helper functions live in
`src/services/renewal_exception_router_service.py` (pure domain logic, no
`agenticstar`/framework security-gate imports; no function accepts an
`llm` parameter except `explain_route()`, explicitly bounded — see
Design Decision Record).

### Step Helper Detail

| # | Step helper | Responsibility |
|---|---|---|
| 1a | `normalize_renewal_record()` | Validate the record has a contract id + renewal window end, the caller is an authorized source, and the rule set declares a version. Returns `None` on any failure — caller (`PreProcessNode`) treats this as a rejection routed to `manual_exception_review`, never inferred as valid (proposal §4 note) |
| 1b | `minimize_sensitive_fields()` | S-2 data minimization (proposal §10 Angle 5) — strips `SENSITIVE_FIELD_NAMES` (e.g. `contact_email`, `negotiated_discount_pct`) from the `contact_consent` and `pricing_reference` payloads before normalization; returns the minimized payload plus the list of stripped field *names* (never values) for the S-4 audit event |
| 2 | `verify_evidence()` | Compare each of the 4 required source categories (entitlement, asset, pricing_reference, contact_consent) against the approved reference; classify each as `present`, `missing` (absent/`present: false`), `expired` (`expires_on` has passed), or `conflicting` (explicit `conflict` flag from upstream reconciliation) |
| 3a | `classify_exception()` | Deterministic bounded routing: the first non-`present` category (by fixed priority `entitlement > asset > pricing_reference > contact_consent`) becomes the primary route; remaining non-`present` categories become `secondary_flags`; all-`present` → `renewal_ready`. Never evaluates a commercial or renewal decision — only evidence presence (proposal §4 Step 3) |
| 3b | `explain_route()` | Bounded narration of the **already-decided** route/reason (proposal §4 Step 3: "use bounded LLM explanation only for human-readable rationale without inventing evidence"). `llm=None` (this template's current wiring) returns a deterministic template string; sole function in the module with an `llm` parameter, explicitly verified in `TestDeterministicSignatures` |
| 4 | `build_handoff_packet()` | Assembles the structured packet (contract id, rule version, findings, primary route, reason, secondary flags, human queue, optional explanation) with a mandatory `advisory_only: true` flag and non-decision notice text (proposal Risk #5 mitigation) |

### Data Flow

```
START
  → InitializeNode
  → PreProcessNode (VERIFIED_EXTERNAL, real S-1 gate)
       1a. normalize_renewal_record() → normalized_record (JSON-str) | rejection_reason
       1b. minimize_sensitive_fields() (called inside 1a) → stripped_sensitive_fields (JSON-str list)
       S-4: sensitive_fields_minimized (only when non-empty)
  → MainNode (VERIFIED_EXTERNAL)
       2.  verify_evidence()       → evidence_findings (JSON-str, 4 categories)
       3a. classify_exception()    → primary_route, route_reason, secondary_flags
       3b. explain_route()         → route_explanation (deterministic template, llm=None)
  → PostProcessNode (VERIFIED_EXTERNAL, real S-3 gate)
       S-3 sensitive-field leak re-check (_extra_security_gate_output)
       4. build_handoff_packet() → handoff_packet (JSON-str), human_queue
       S-4 audit log: renewal_record_normalized/rejected, evidence_verified,
         exception_classified, handoff_packet_routed
       → formatted_output (dict, ready for caller)
  → FinalizeNode
END
```

> **Scope boundary** (source proposal §4): changing source systems, pricing
> decisions, contract/legal interpretation, exception approval, customer
> contact, offer generation, renewal execution, and billing are explicitly
> out of scope. Each invocation processes one renewal record.

> **Advisory-only guarantee** (source proposal Risk #5): `build_handoff_packet()`
> always sets `advisory_only: true` and attaches a non-decision notice. This
> is a documented runtime contract flag, not a framework-enforced mechanism
> — the guarantee is that no code path ever produces a packet with
> `primary_route == renewal_ready` and represents it as a contractual
> approval; route names avoid decision language and `renewal_ready` is
> explicitly documented as data-readiness only (proposal Risk #5).

## HITL — not used (mandatory-disclaimer pattern instead)

The source proposal describes a routing recommendation to human queues
(operations analyst, account team lead, data steward), not a suspend/resume
checkpoint requiring a specific person's sign-off before the agent can
complete. Per the playbook guidance on advisory-only workflows (and consistent with
other templates that route to human queues rather than gate on sign-off),
this template uses the **mandatory disclaimer** pattern instead of real
`interrupt()`-based HITL: every handoff packet carries `advisory_only:
true` plus explicit notice text, and `config/agent.yaml`/`config/config.yaml`
do not set `hitl.enabled`.
`tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` is
retained unmodified from the template baseline and auto-`SKIPPED` (PB-7
gate rule — auto-waived for non-HITL templates).

## State Definition

File: `src/schemas/state.py` — extends `AgentState` (flat TypedDict, ADR-005).

| Field | Type | Producer (step) | Consumer | ADR-005 note |
|---|---|---|---|---|
| `normalized_record` | `Optional[str]` | 1a. normalize_renewal_record | 2, 3a, 3b, 4 | **JSON-str** — `{contract_id, renewal_window_end, entitlement, asset, pricing_reference, contact_consent, rule_version, escalation_directory, stripped_sensitive_fields}` |
| `rejection_reason` | `Optional[str]` | 1a. normalize_renewal_record | MainNode | `None` on success; human-readable reason routed to `manual_exception_review` on failure |
| `stripped_sensitive_fields` | `Optional[str]` | 1b. minimize_sensitive_fields | PreProcessNode's own S-4 audit event (`sensitive_fields_minimized`) | **JSON-str list[str]** — field *names* only, e.g. `["contact_email"]`; never the stripped values |
| `evidence_findings` | `Optional[str]` | 2. verify_evidence | 3a, 3b, 4, PostProcessNode output | **JSON-str list** — `[{category, status, source}]`, `status` one of `present\|missing\|expired\|conflicting` |
| `rule_version` | `Optional[str]` | 2 (passthrough from normalized_record) | 4, PostProcessNode output | preserved for citation/audit |
| `primary_route` | `Optional[str]` | 3a. classify_exception | 3b, 4, PostProcessNode output | one of the 6 bounded route values (proposal §4 Step 3) |
| `route_reason` | `Optional[str]` | 3a. classify_exception | 3b, 4, PostProcessNode output | short deterministic reason code backing the route |
| `secondary_flags` | `Optional[str]` | 3a. classify_exception | 4, PostProcessNode output | **JSON-str list[str]** — remaining non-present evidence categories, e.g. `["asset:missing"]`; proposal §4 note: "identify one primary accountable queue and preserve secondary flags" |
| `route_explanation` | `Optional[str]` | 3b. explain_route (llm=None — deterministic template; no LLM wired in this build) | 4, PostProcessNode output (`handoff_packet.explanation`, only when non-`None`) | narrative of an already-decided route/reason; structurally cannot influence the route |
| `handoff_packet` | `Optional[str]` | 4. build_handoff_packet | caller | **JSON-str** — final sanitized, source-cited routing packet |
| `human_queue` | `Optional[str]` | 4. build_handoff_packet | caller | authorized queue name the packet is routed to |

> **State Constraints (mandatory):**
> - Flat TypedDict only — no Pydantic, dataclass, or arbitrary objects (msgpack incompatible)
> - No credentials/JWT/API keys in state (checkpoint DB leakage risk)
> - `InvocationContext` accessed via `InvocationContext.from_state(state)` inside
>   `execute()` only — never stored in state, never constructed directly in a node
> - Dict/list fields: `Optional[str]` + `json.dumps` (producer) / `json.loads`
>   (consumer) [ADR-005]
> - Renewal records carry contact and commercial-pricing data (source
>   proposal Risk #3) — never logged in plaintext in S-4 audit events; only
>   ids, counts, categories, and route/queue names

## Security Design

| Gate | Location | Level | Implementation |
|---|---|---|---|
| S-1 | PreProcessNode, MainNode, PostProcessNode (real gates) + step 1a `normalize_renewal_record` (domain check) | VERIFIED_EXTERNAL | Framework S-1 trust gate on `__call__()` for all 3 domain nodes (record data requires an authenticated caller — proposal §10 Angle 5); domain-level check inside `execute()`: contract id, renewal window end, rule version, and `source_authorized` flag all required, else routed to manual review |
| S-2 | PreProcessNode | VERIFIED_EXTERNAL | `minimize_sensitive_fields_recursive()` inside `normalize_renewal_record()` — recursively strips `SENSITIVE_FIELD_NAMES` (`contact_name`, `contact_email`, `contact_phone`, `negotiated_discount_pct`, `commercial_notes`) at every nesting level of all 4 source categories (`entitlement`, `asset`, `pricing_reference`, `contact_consent`), not just their top-level keys, so those values never enter any state field regardless of how deep they are nested |
| S-3 | PostProcessNode (real gate) | VERIFIED_EXTERNAL | `_extra_security_gate_output()` — defense-in-depth re-scan of the entire serialized `handoff_packet` string for any `SENSITIVE_FIELD_NAMES` key leaking through despite upstream minimization; the scan is over the full string so it catches a leak at any nesting depth, not just top-level; raises to block (S-3 convention) |
| S-4 | All domain nodes | — | `emit_trace_event()` in every `execute()`: `renewal_record_normalized`/`renewal_record_rejected`/`sensitive_fields_minimized` (PreProcessNode), `evidence_verified`/`exception_classified` (MainNode), `handoff_packet_routed` (PostProcessNode). Payloads carry only ids, counts, field *names*, and route/queue names — never raw evidence content or stripped field values |

> **S-2/S-3 hook rule (ADR-017):** All three real slot nodes are `FunctionNode`
> subclasses. Framework `@final` gates run automatically. Extend via
> `_extra_security_gate_input()` / `_extra_security_gate_output()` only.
> **MUST NOT override `_security_gate_input()` or `_security_gate_output()`
> directly** — raises `TypeError` at class definition.

> **S-4 emit rule:** Do NOT emit `node_start` / `node_complete` / `node_error`
> — `BaseNode.__call__()` emits these automatically. Custom events only.

> **Regulated-data detection scope note:** the fixed regulated-data detector
> rules (APPI sensitive categories, My Number identifiers, government
> classification markers) apply to HCR/INS/GOV verticals specifically.
> SVC is not in that list — this template's sensitive-data handling is the
> broader "minimize personal, business-confidential, and case-sensitive
> data" obligation from the proposal itself (§2-3, §10 Angle 5), implemented
> via `SENSITIVE_FIELD_NAMES` stripping rather than the regulated-data
> detector patterns.

### Fix — recursive sensitive-field minimization (review T2-1)

A review flagged that `minimize_sensitive_fields()` only stripped
`SENSITIVE_FIELD_NAMES` at the top level of the `contact_consent` and
`pricing_reference` dicts, and only those two categories — a sensitive
field nested one or more levels deep (e.g. `contact_consent.
secondary_contact.contact_email`), or present anywhere inside
`entitlement`/`asset`, persisted into `normalized_record` unchanged.

**Fixed** by `minimize_sensitive_fields_recursive()` — a recursive dict/
list walker, now applied to **all 4** source categories
(`entitlement`, `asset`, `pricing_reference`, `contact_consent`) inside
`normalize_renewal_record()`, not just 2. `minimize_sensitive_fields()`
(non-recursive) is retained only for its own unit test coverage and any
future simple flat-dict caller.

**Explicit recursion/detection boundary** (per the review's request to
define the allowed input schema/recursion boundary): the recursive walker
strips by exact field **name** at every level of nested dicts and lists —
the full JSON-serializable structure produced by `normalize_renewal_record()`
(ADR-005: no non-JSON types reach this boundary, so the walker never needs
to handle non-dict/list/primitive types). It does **not** attempt
free-text pattern/value scanning (e.g. detecting an email address embedded
in a string under an unlisted key like `notes` or `description`) — that is
a fundamentally different, content-detection problem (the kind the fixed
regulated-data detector rules solve for HCR/INS/GOV
verticals via fixed regex patterns), not a schema-minimization problem,
and is explicitly out of scope for SVC per the "Regulated-data detection
scope note" above. If a future proposal revision requires
detecting sensitive *values* embedded in permitted free-text fields (not
just sensitive *field names*), that is a new capability requiring its own
design and review sign-off, not an extension of this fix.

The S-3 output gate in `PostProcessNode._extra_security_gate_output()` was
already a full-string scan (`f'"{field_name}"' in packet_raw`), so it
already caught a leak at any nesting depth once the field name appeared
literally in the serialized JSON — that gate did not need to change, only
the upstream minimization did. Both are covered by new tests: 5 unit
tests for `minimize_sensitive_fields_recursive()`
(`TestMinimizeSensitiveFieldsRecursive`), 2 unit tests confirming nested
fields inside each of the 4 categories are stripped by
`normalize_renewal_record()`, and 2 new PB-08 tests — one confirming a
deeply-nested sensitive field is blocked at the S-3 output boundary, one
confirming a nested sensitive field is stripped by `PreProcessNode`
*before* it ever reaches `normalized_record`/state (not merely caught
later as a last resort by S-3).

### Risk mitigations — design decisions (source proposal §11, Risks 1-5)

1. **Risk 1 (High) — incorrect routing from stale/incomplete/conflicting
   source records.** `verify_evidence()` preserves the `source` field per
   category and never infers `expired` from an unparseable `expires_on`
   (fail-safe, same "do not infer eligibility" principle used by other
   templates' due-date handling); `normalize_renewal_record()` fails safe (returns
   `None` → manual review) on any missing authorization or rule version.
2. **Risk 2 (Medium) — LLM-generated explanation could overstate evidence
   or imply approval.** `explain_route()` is called only after
   `classify_exception()` has already decided the route; its return type
   is a bare string with no route/status channel, and its default
   (`llm=None`) is a deterministic template — no LLM is wired in this
   build at all, eliminating the risk entirely for the current
   deployment.
3. **Risk 3 (High) — contact-consent/commercial terms exposed in logs or
   outputs.** `minimize_sensitive_fields()` strips `SENSITIVE_FIELD_NAMES`
   before normalization (never enters `normalized_record` or any state
   field); S-3 `_extra_security_gate_output()` re-scans the final packet
   as defense-in-depth; `emit_trace_event()` payloads carry only field
   *names*, never values.
4. **Risk 4 (Resolved) — scope overlap with an earlier duplicate proposal.**
   The duplicate was closed; this template is the single canonical
   template ID/scope (proposal §5). No design action required.
5. **Risk 5 (Medium) — `renewal_ready` route mistaken for contractual
   approval.** `build_handoff_packet()` always sets `advisory_only: true`
   and an explicit notice string defining `renewal_ready` as data-readiness
   only; the agent never contacts the customer, sends an offer, or writes
   to any source system.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, permissions, credential handle) — via `InvocationContext.from_state(state)` (not required by current logic, available if secrets are added later)
- [ ] ConnectionPolicy (retry/timeout strategy) — not used; no external service calls in this initial build (deterministic logic only, no LLM/API/DB dependency)
- [x] SecurityViolationError — raised by framework S-1 trust gate on insufficient `caller_trust_level`
- [x] S-2: `_extra_security_gate_input()` — raw-length sanity check on `user_input` in PreProcessNode (a pattern also used by other non-regulated-data verticals); structured sensitive-field minimization happens separately at the service-layer normalization boundary (`minimize_sensitive_fields()` inside `normalize_renewal_record()`)
- [x] S-3: `_extra_security_gate_output()` — sensitive-field leak re-check in PostProcessNode
- [x] S-4: `emit_trace_event()` — at least one domain-specific event in each `execute()`

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass → framework `@final` gate always runs automatically;
>   extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only
> - `GraphNode` / `RemoteAgentNode` → deliberate no-op (upstream or remote node's gate already applied)
> - Custom `BaseNode` subclass → must implement `_security_gate_input()` and
>   `_security_gate_output()` directly (`@abstractmethod` — omission raises `TypeError` at instantiation)

### Composition Pattern

- **Pattern**: Standalone (no GraphNode/RemoteAgentNode) — 6 step helper
  functions live in `src/services/renewal_exception_router_service.py`,
  not LangGraph nodes; only 3 real `FunctionNode`s are registered
- **L1 Base**: `AgentBaseGraph`
- **Error propagation strategy**: propagate — node failures surface to the
  framework backbone; a rejected/unauthorized record is never raised as an
  exception, it is routed to `manual_exception_review` in-band

## Import Isolation Confirmation

- [x] Template does NOT import `agenticstar-platform` SDK (Level 0)
- [x] Import targets: `framework/` and `shared/` only
- [x] No `agents/base/` imports (L1 direct inheritance — no L2)

## Class Name Consistency

| Artifact | Value |
|---|---|
| `src/graph/graph.py` | `class ServiceContractRenewalExceptionRouterAgent(AgentBaseGraph)` |
| `config/agent.yaml` | `class: "ServiceContractRenewalExceptionRouterAgent"` |
| `src/api/server.py` | `from src.graph.graph import ServiceContractRenewalExceptionRouterAgent` |

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AgentBaseGraph | Cat 2 fixed pipeline; normalize/verify/classify/handoff order is deterministic, no autonomous loop needed |
| Cat 2 outer shape | `GraphNode` + inner domain-workflow graph (sample shape) | Flat backbone: outer `AgentBaseGraph`, step helpers inside 3 real `FunctionNode`s, no inner graph | Flat backbone | The framework rule explicitly permits both — "Cat is a business category... not its internal architecture" and "a Cat 2 template can inherit the L1 framework directly and implement its own multi-step workflow." This workflow is a strictly sequential 4-step pipeline with no branching/looping/independent-composition need, so an inner `GraphNode` graph would add composition overhead with no corresponding control-flow requirement. Same pattern already in production across other templates in the fleet (see "Architecture Overview" note above for full discussion) |
| 4-step realization | 4+ real `BaseNode`s (extra LangGraph nodes) | 6 pure-Python step helper functions inside 3 real `FunctionNode`s | Pure-Python step helper functions | `AgentBaseGraph` only exposes 3 domain slots; a parallel node system running outside `__call__()` would not be covered by the real S-1/S-2/S-3 gates |
| 4→3 slot mapping | Various groupings possible | pre_process=Normalize+Minimize, main=Verify+Classify+Explain, post_process=Handoff | As chosen | Matches natural boundaries: source/authority validation + sensitive-field stripping before any evidence work in pre_process, the three closely-coupled deterministic/narration steps in main, sanitized S-3-gated output assembly in post_process |
| HITL vs mandatory-disclaimer | Real `interrupt()`-based HITL sign-off gate | Mandatory `advisory_only` disclaimer + non-decision notice on every packet | Mandatory disclaimer | Proposal describes routing recommendations to human queues, not a suspend/resume checkpoint gating a specific reviewer's sign-off before completion; real HITL would misrepresent the workflow as requiring in-agent approval it does not require (playbook A5) |
| Primary/secondary route selection | Return all non-present categories as a list with no single accountable owner | Fixed-priority single primary route + secondary flags for the rest | Fixed-priority primary + secondary flags | Proposal §4 note explicitly requires "one primary accountable queue" while preserving secondary flags — an unranked list would leave routing ownership ambiguous |
| Route classification authority | LLM classifies the route | Fully deterministic rule-based classification; LLM (if ever added) restricted to narrating an already-decided gap | Fully deterministic | Proposal §4 Step 3 — "use bounded LLM explanation only for human-readable rationale without inventing evidence"; keeping classification 100% rule-based removes any risk of an LLM inferring a decision it is not authorized to make |
| Expiry ambiguity handling | Treat unparseable `expires_on` as expired (fail loud) | Treat unparseable `expires_on` as present-so-far, rely on other findings to catch real gaps | Present-so-far on ambiguity | Same "do not infer eligibility" principle as proposal §4 note; inferring expired from bad data would be exactly this prohibited inference |
| Sensitive-field minimization mechanism | Redact/mask values at output time only (S-3-style) | Strip declared sensitive field names before normalization, so the value never enters state at all | Strip before normalization | Same rationale used elsewhere in the fleet — a value redacted only at output time would still have existed in an intermediate state snapshot; stripping upstream means it is never present in any state field to begin with |
