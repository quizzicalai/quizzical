# Copilot Instructions

- `backend/README.md` is a public-facing technical document and must be kept accurate.
- `frontend/README.md` is a public-facing technical document and must be kept accurate.
- When backend routes, request or response shapes, persistence behavior, configuration sources, operational constraints, or local run instructions change, update `backend/README.md` in the same change.
- When modifying files under `frontend/`, update `frontend/README.md` in the same change if routing, config loading, state management, API integration, environment variables, scripts, testing, or user-visible behavior changed.
- Keep `backend/README.md` aligned with public GitHub README best practices: clear purpose, current architecture, API surface, setup, configuration, testing, and known operational behavior.
- Keep `frontend/README.md` aligned with public GitHub README best practices: clear purpose, current architecture, routes, state flow, setup, configuration, testing, and known operational behavior.
- Do not leave placeholder examples, stale endpoint descriptions, or undocumented behavior drift in `backend/README.md`.
- Do not leave placeholder notes, implementation plans, or stale behavioral descriptions in `frontend/README.md`.

## Backend Specification & TDD Process (MANDATORY for `backend/` changes)

### `specifications/backend-design.MD` is the source of truth for the backend

> **Before making ANY change to files under `backend/`**, you MUST update `specifications/backend-design.MD` first.

This includes (but is not limited to):
- New or modified API endpoints (routes, request/response schemas, status codes, auth requirements)
- Changes to database models, migrations, or persistence logic
- Changes to the LangGraph agent (nodes, edges, routing, tools, prompts)
- Changes to services (`database.py`, `redis_cache.py`, `llm_service.py`)
- Changes to security mechanisms (CORS, Turnstile, headers, body limits)
- Changes to configuration (new env vars, new YAML settings, default value changes)
- Changes to error handling, HTTP status codes, or error response shapes
- Any new business rules, thresholds, or magic numbers

**Spec update steps:**
1. Identify which section(s) of `specifications/backend-design.MD` are affected
2. Add or update the relevant acceptance criteria (AC-XXXX-N format) to precisely describe the new/changed behaviour
3. Update architecture diagrams, data structure tables, or flow descriptions as needed
4. The updated spec is what reviewers and tests will be validated against

### Test-Driven Development (TDD) is required for all `backend/` changes

**Mandatory TDD workflow:**
```
Spec change → write failing tests → implement code → green tests → update backend/README.md
```

1. **Write failing tests FIRST** — based on the acceptance criteria you just added/updated in `specifications/backend-design.MD`
2. Tests must cover (as applicable for the change):
   - **Happy path** — the intended success case
   - **Error paths** — each documented error code and condition
   - **Edge cases** — boundary values, empty inputs, duplicates, idempotency
   - **Security** — auth bypass attempts, oversized payloads, injection, token validation
   - **Performance** — response time expectations for critical paths (mark as slow/integration if needed)
   - **Resilience** — Redis miss, DB down, LLM timeout, retry exhaustion
3. Run tests to confirm they **fail** for the right reason before writing implementation code
4. Implement the minimum code change to make tests pass
5. Run the **full test suite** to confirm no regressions
6. Update `backend/README.md` if the public API surface or behaviour changed

### Test categories and locations

| Category | Directory | When required |
|----------|-----------|---------------|
| Unit | `tests/unit/` | Every function/class change |
| Integration | `tests/integration/` | Every endpoint change |
| Security | `tests/security/` | Auth, headers, body size, CORS, input validation changes |
| Performance | `tests/performance/` | Timeout, concurrency, pool changes |
| Smoke | `tests/smoke/` | Happy-path end-to-end flow |
| Reliability | `tests/reliability/` | Redis/DB failure modes, config parsing |

### Acceptance criteria format

When writing ACs in `specifications/backend-design.MD`, use this format:
```
- AC-<CATEGORY>-<N>: <Condition> → <Expected outcome>
```
Examples:
- `AC-START-11: category containing only spaces → 422`
- `AC-SEC-CORS-7: Missing Origin header → response returned without Access-Control-Allow-Origin`

Each AC should be specific enough to write a single automated test for it.