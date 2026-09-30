# Test Specification

## Test Strategy
- Coverage target: 90% on `src/nodes/` and `src/services/` (business logic) —
  achieved 100% (see Test Execution Summary). `src/api/server.py`,
  `src/graph/graph.py`, `src/schemas/state.py` are intentionally not
  pytest-covered: `state.py` is a `TypedDict` declaration with nothing to
  execute; `graph.py`/`server.py` are verified instead by live e2e
  (`agent.invoke()` and `curl` against a running `uvicorn` server — see
  `docs/07_operation_guide.md` Deployment Steps and Troubleshooting, both
  sections built from commands actually run against this codebase).
- Test types: Unit (service + node, isolated) / Proof-of-Boundary (framework
  boundary + domain-specific) / manual e2e (documented, not automated in CI)

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | PASS — `src/schemas/state.py` extends `AgentState`, all 11 domain fields `Optional[str]`; verified by `tests/proof_of_boundary/test_state_safety.py` |
| TC-02 | SecurityViolationError fires on invalid input | Error raised | PASS — S-1 trust gate on `VERIFIED_EXTERNAL` nodes; framework returns `ERROR` before `execute()` runs on an `ANONYMOUS` caller (verified live via `curl` without `Authorization` header — see `docs/07_operation_guide.md` Troubleshooting) |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations | PASS — no credential-like field names in `state.py`; `ruff check` clean |
| TC-04 | InvocationContext via configurable only | Direct access raises error | N/A — no node accesses secrets (no external service calls in this template; `ExplanationLLM` Protocol in `renewal_exception_router_service.py` is unused by default, `llm=None`) |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start` / `node_complete` / `node_error` absent from `execute()` body | PASS — only domain events emitted (7 distinct event types across 3 nodes, see Security Design table in `docs/02_design.md`) |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode` subclass) | `TypeError` at class definition if overridden | PASS — only `_extra_security_gate_input()` used in `PreProcessNode` |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode` subclass) | `TypeError` at class definition if overridden | PASS — only `_extra_security_gate_output()` used in `PostProcessNode` |
| TC-08 | `required_trust_level` enforced | Insufficient trust → refused | PASS — all 3 domain nodes declare `VERIFIED_EXTERNAL`; covered by PB-6 and live e2e (ANONYMOUS-caller test in `docs/07_operation_guide.md`) |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial | Domain checks execute correctly | PASS — raw-length sanity check in `PreProcessNode` (2 tests, `test_pre_process_node.py::TestPreProcessNodeInputGate`); structured sensitive-data minimization is a separate, additional S-2 control — `minimize_sensitive_fields()` strips `SENSITIVE_FIELD_NAMES` inside `normalize_renewal_record()`, 8 dedicated tests across `test_renewal_exception_router_service.py::TestMinimizeSensitiveFields`/`TestNormalizeRenewalRecord` + `test_pre_process_node.py` |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial | Domain checks execute correctly | PASS — sensitive-field leak re-check in `PostProcessNode`, 2 dedicated tests in `test_post_process_node.py::TestPostProcessNodeOutputGate` |
| TC-11 | S-4: at least one domain `emit_trace_event()` inside each `execute()` | Domain event emitted on every invocation path | PASS — `renewal_record_normalized`/`renewal_record_rejected`/`sensitive_fields_minimized` (PreProcessNode), `evidence_verified`/`exception_classified` (MainNode), `handoff_packet_routed` (PostProcessNode) |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures | PASS |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass | PASS — `tests/proof_of_boundary/test_state_safety.py` |
| PB-3 | Level 2 → External service | Real external service connection | Data retrieved | N/A — no external service dependency; all verification/routing logic is deterministic in-process Python |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations | PASS — `tests/proof_of_boundary/test_import_isolation.py` |
| PB-5 | Checkpoint safety | No JWT/Pydantic in checkpoint | Inspection pass | PASS — covered by `test_state_safety.py` |
| PB-6 | Invoke execution order | S-1 → S-4 `node_start` → S-2 → `execute()` → S-3 → S-4 `node_complete` | Order verified | PASS — `tests/proof_of_boundary/test_pb_invoke_order.py`, runs against all 3 concrete node classes via `pkgutil.walk_packages` discovery |
| PB-7 | HITL interrupt propagation *(conditional)* | Required only when `hitl.enabled: true` | Auto-waived — non-HITL | **Auto-waived — non-HITL** (2 test functions `SKIPPED` via `pytest.mark.skipif`; mandatory-disclaimer pattern used instead, see `docs/02_design.md` "HITL — not used") |
| PB-8 | Sensitive-field minimization + advisory-only boundary (domain-specific) | Stripped sensitive value never in audit payload; nested sensitive fields stripped before reaching output; S-3 leak re-check blocks at any nesting depth; every handoff packet is `advisory_only: true` | 0 raw-value log occurrences; 0 unblocked leaks (incl. deeply nested); 100% packets advisory | PASS — `tests/proof_of_boundary/test_pb08_sensitive_field_and_advisory_boundary.py`, 5 tests covering S-2 minimization audit-payload leak check, S-3 output blocking (top-level and deeply-nested, T2-1 fix), nested-field stripped before reaching PreProcessNode output (T2-1 fix), and all 6 route values' advisory-only invariant |

> **Pre-gate checklist:** PB-1 through PB-6 are mandatory. PB-7 is Auto-waived — non-HITL for this template.

## Business Logic Tests

Grouped by service function — each row cites its concrete test module/class,
not just a claim of coverage.

| Function | Test class | Cases covered |
|---|---|---|
| `normalize_renewal_record()` | `test_renewal_exception_router_service.py::TestNormalizeRenewalRecord` (9 tests) | missing contract_id → reject; missing renewal_window_end → reject; unauthorized source → reject; missing rule version → reject; valid record → accept; sensitive contact/pricing fields stripped from output; **nested sensitive fields stripped from contact_consent; nested sensitive fields stripped from entitlement and asset (T2-1 fix)** |
| `minimize_sensitive_fields()` | `test_renewal_exception_router_service.py::TestMinimizeSensitiveFields` (4 tests) | known sensitive fields stripped; no-op when none present; stripped list contains names not values; every declared `SENSITIVE_FIELD_NAMES` entry stripped |
| `minimize_sensitive_fields_recursive()` | `test_renewal_exception_router_service.py::TestMinimizeSensitiveFieldsRecursive` (5 tests) | strips at top level; strips inside nested dict; strips inside list of dicts; no-op when none present; stripped-names deduplicated and sorted |
| `verify_renewal_window()` | `test_renewal_exception_router_service.py::TestVerifyRenewalWindow` (8 tests) | missing → missing finding; empty string → missing finding; unparseable → expired (not present); past date → expired; far-past date (`2000-01-01`, the exact review scenario) → expired; future date → present; today → present; category/source fields correct |
| `verify_evidence()` | `test_renewal_exception_router_service.py::TestVerifyEvidence` (9 tests) | all-present (5 categories incl. renewal_window); empty-dict-is-missing; explicit `present: false`; conflict flag; past/future/unparseable `expires_on`; `source` field preserved; expired renewal window included in findings even when all 4 evidence categories are clean |
| `classify_exception()` | `test_renewal_exception_router_service.py::TestClassifyException` (7 tests) | rejected; all-present → renewal_ready; entitlement gap primary over asset; asset gap when entitlement present; pricing gap when entitlement+asset present; consent gap lowest priority; unrecognized category → manual review |
| `select_human_queue()` | `test_renewal_exception_router_service.py::TestSelectHumanQueue` (3 tests) | escalation_directory override; default fallback; unknown route falls back to manual review queue |
| `explain_route()` | `test_renewal_exception_router_service.py::TestExplainRoute` (3 tests) | deterministic template for renewal_ready; deterministic template names affected categories; `llm` provided delegates to `llm.generate()` with a prompt containing the already-final route/reason |
| `build_handoff_packet()` | `test_renewal_exception_router_service.py::TestBuildHandoffPacket` (4 tests) | `advisory_only` always `True`; no route name contains decision language; explanation included/omitted correctly |
| Deterministic/LLM boundary | `test_renewal_exception_router_service.py::TestDeterministicSignatures` (2 tests) | no decision function accepts `llm` (incl. `verify_renewal_window`, T1-1 fix); `explain_route` is the sole, explicitly-verified exception with `llm=None` default |
| `PreProcessNode.execute()` | `test_pre_process_node.py::TestPreProcessNode` (5 tests) | valid record normalizes; unauthorized source rejected; missing contract_id rejected; no-sensitive-fields yields empty stripped list; sensitive fields stripped + audited (value never in audit payload) |
| `PreProcessNode._extra_security_gate_input()` | `test_pre_process_node.py::TestPreProcessNodeInputGate` (2 tests) | oversized `user_input` (>4000 chars) blocks; normal-length input passes through |
| `MainNode.execute()` | `test_main_node.py::TestMainNode` (8 tests) | rejected-upstream routing; all-present routing (5 findings); **expired renewal window never routes renewal_ready; unparseable renewal window never routes renewal_ready (T1-1 fix)**; asset-gap routing; secondary-flags with multiple gaps; explanation present + non-decision-language on every path; node-contract signature check |
| `PostProcessNode.execute()` | `test_post_process_node.py::TestPostProcessNode` (4 tests) | advisory-only packet built; asset-queue routing for asset gap; explanation included when present; explanation key omitted when absent |
| `PostProcessNode._extra_security_gate_output()` | `test_post_process_node.py::TestPostProcessNodeOutputGate` (2 tests) | sensitive field leak in output blocks (raises); clean output passes through |

## Manual E2E Verification — local only, NOT Stage ⑤→⑥ readiness evidence

> **Scope correction (review T2-2):** the table below records commands
> that were actually run, on a developer workstation, against
> `agent.compile()`/`agent.invoke()` and a locally-started `uvicorn`
> server — via `python3 -m pytest`, direct Python invocation, and `curl`
> against `http://localhost:8000`. **This is not, and must not be read
> as, `deploy-stg` CI pipeline evidence.** `docs/07_operation_guide.md`
> "STG Sign-off Record" is authoritative on Stage ⑤→⑥ status: as of this
> writing, only a **local** `check-local.sh` run has produced evidence
> (`stage5_evidence/stage5_v1.json`, `overall: PASS`) — there is no
> `deploy-stg` CI pipeline ID/job URL yet, and the required authorized
> sign-off (per the Stage ⑤ release RACI) is still **pending**. The rows
> below establish that the code behaves as designed under manual
> inspection; they do not substitute for the CI-job evidence and
> authorized sign-off that Stage ⑤→⑥ requires.

Run against a live `agent.compile()`/`agent.invoke()` instance and,
separately, a live `uvicorn` server via `curl` — exercises
`src/graph/graph.py` and `src/api/server.py`, the two files pytest does
not cover (see Test Strategy above).

| Scenario | Verified behavior |
|---|---|
| Authorized, all-present renewal record | `status: "success"`, `primary_route: "renewal_ready"`, `advisory_only: true` |
| Missing asset evidence | `primary_route: "asset_data_correction"`, routed to asset data steward queue |
| Multiple gaps (entitlement + asset) | `primary_route: "entitlement_verification"` (priority), `secondary_flags` includes `asset:missing` |
| Unauthorized source (`rules.source_authorized: false`) | `status: "success"` (fail-safe, not an error), `primary_route: "manual_exception_review"` |
| Sensitive field present (`contact_email`) | Record processes normally; field is absent from `normalized_record.contact_consent` |
| `ANONYMOUS` caller (no/invalid `Authorization` header) | S-1 trust gate refuses before `PreProcessNode.execute()` runs — `node_history` stops at `MainNode` with no `PostProcessNode` entry, `status: "error"` |
| Expired renewal window (`2000-01-01`), all 4 evidence categories present | `primary_route: "manual_exception_review"` — never `renewal_ready` (T1-1 fix regression check) |

Full commands and exact observed responses are recorded in
`docs/07_operation_guide.md` (Deployment Steps, Troubleshooting) — every
claim there was executed against this codebase, not written from
assumption. **Required before Stage ⑤→⑥:** a real `deploy-stg` CI
pipeline run (pipeline ID + job URL) and authorized sign-off per
`docs/07_operation_guide.md` "STG Sign-off Record" — see that section for
current status.

## Test Execution Summary
- Execution date: 2026-07-25
- Total tests: 84 (`pytest tests/ -v`)
- Pass: 82 / Fail: 0 / Skip: 2 (both PB-7, correctly auto-waived — non-HITL template)
- Coverage (`pytest --cov=src --cov-report=term-missing`):
  - `src/services/renewal_exception_router_service.py`: **100%** (118/118 statements)
  - `src/nodes/main_node.py`: **100%** (30/30)
  - `src/nodes/post_process_node.py`: **100%** (33/33)
  - `src/nodes/pre_process_node.py`: **100%** (35/35)
  - `src/api/server.py`, `src/graph/graph.py`, `src/schemas/state.py`: not
    pytest-exercised by design (see Test Strategy) — covered by the Manual
    E2E Verification table above instead
  - Overall (`src/` total, including the non-pytest-covered files): 77%
    (216/281)
- Lint: `ruff check src/ tests/ scripts/` — clean, 0 violations
- Local gates: `check_cat_consistency.py`, `check_dep_pinning.py`,
  `check_stub_tests.py`, `check_trust_level.py` — 4/4 PASS
- `check-local.sh`: all checks passed, including Stage 5 STG invoke
  evidence `overall: PASS` (local `check-local.sh` run — not `deploy-stg`
  CI pipeline evidence, see "Manual E2E Verification" scope correction
  above and `docs/07_operation_guide.md` STG Sign-off Record for current
  Stage ⑤→⑥ status)
- `check-security.sh`: 8/8 checks passed

### 2026-07-25 re-review (MR !1) — 2 blockers, 2 majors, 1 minor addressed

1. **BLOCKER T1-1 (renewal-window validation)** — `normalize_renewal_record()`
   previously only checked `renewal_window_end` was non-empty, never
   parsed or compared it against today; an expired window (e.g.
   `2000-01-01`) could reach `renewal_ready`. Fixed by
   `verify_renewal_window()`, a new finding category
   (`"renewal_window"`) evaluated first in `verify_evidence()`'s 5-category
   list and given highest priority in `classify_exception()` — missing,
   unparseable, or lapsed windows now always route to
   `manual_exception_review`, never `renewal_ready`, regardless of the
   other 4 categories' state. 10 new/updated unit tests
   (`TestVerifyRenewalWindow`, updated `TestVerifyEvidence`/`TestMainNode`
   fixtures) plus a live e2e check reproducing the reviewer's exact
   `2000-01-01` scenario.
2. **BLOCKER T1-2 (Cat 2 architecture)** — clarified, not changed: added
   an explicit design-doc note citing the framework rule ("Cat is a
   business category... not its internal architecture") and its Cat 2
   criterion ("a Cat 2 template can inherit the L1 framework directly and
   implement its own multi-step workflow") as the authoritative basis for
   the flat-backbone shape, alongside the alternative `GraphNode` + inner
   workflow shape the template baseline's sample also demonstrates — both are valid
   Cat 2 architectures per the authoritative rule; the flat-backbone
   step-helper pattern was chosen because this workflow has no
   branching/looping/independent-composition need, and is already the
   established pattern across other templates in the fleet. See `docs/02_design.md` Architecture Overview note and new
   Design Decision Record row.
3. **MAJOR T2-1 (nested sensitive data)** — `minimize_sensitive_fields()`
   only stripped the top level of `contact_consent`/`pricing_reference`;
   nested fields, and any field inside `entitlement`/`asset`, were never
   scanned. Fixed by `minimize_sensitive_fields_recursive()`, now applied
   to all 4 source categories in `normalize_renewal_record()`. Explicit
   recursion/detection boundary documented (name-based structural
   stripping, not free-text value scanning — see `docs/02_design.md`
   "Fix — recursive sensitive-field minimization"). 7 new unit tests
   (`TestMinimizeSensitiveFieldsRecursive`, 2 new `TestNormalizeRenewalRecord`
   nested-field tests) and 2 new PB-08 tests (deep-nesting S-3 block,
   nested-field stripped before reaching PreProcessNode output).
4. **MAJOR T2-2 (E2E/Stage-5 evidence claims)** — "Manual E2E
   Verification" section corrected to explicitly state it is local-only
   verification, not `deploy-stg` CI pipeline evidence, and points readers
   to `docs/07_operation_guide.md`'s STG Sign-off Record (still pending
   real pipeline ID/job URL and authorized sign-off) as the authoritative
   Stage ⑤→⑥ status — reconciling the wording so it can no longer be read
   as claiming Stage ⑤→⑥ readiness.
5. **MINOR T3-1 (proposal traceability)** — `docs/01_proposal.md`'s PM
   Decision & Score Breakdown, Risks (§11), and Dependencies (§12) are now
   reproduced verbatim in-repo (previously only referenced via the
   developer-local path), so the file is self-contained; flagged the
   tracking-issue-number gap as an explicit action item (this template's
   `DRAFT-2095` issue number is not yet confirmed, unlike some other
   templates in the fleet that already have a confirmed tracking issue).
