# SVC-C2-128 — ServiceContractRenewalExceptionRouter

> **Category**: Cat 2 (orchestrates multiple steps for a specific use case)
> **Industry**: SVC (service industry)

## Overview

Validates a single B2B service-contract renewal record against approved,
read-only entitlement, asset-coverage, pricing-reference, contact-consent,
and renewal-window sources; classifies a bounded exception route (never a
commercial or renewal decision); and hands off a sanitized, source-cited
evidence packet to the accountable human queue (entitlement verification,
asset-data correction, pricing/manual exception review, consent/contact
update, or renewal-ready).

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

This agent is written against the `agenticstar-agentcore` framework and is
runnable standalone via `src/api/server.py` — it does not require the wider
AgentCore platform (registry, gateway, orchestrator) to function. Outside the
platform: request authentication falls back to an optional bearer token read
from an environment variable (unset means every caller is treated as
unauthenticated), and there is no persistent checkpointer unless you wire one
yourself, so in-flight state does not survive a process restart.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
deploy/       deployment manifests and invoke payload sample
```

See `docs/` for the design spec and test specification.

## Customising

1. Adjust `config/` for your own environment and evidence-source policies.
2. Replace the entitlement, asset-coverage, pricing-reference, contact-consent, and renewal-window data sources with your own.
3. Review the step-helper implementations in `src/services/renewal_exception_router_service.py` and the node wiring in `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
