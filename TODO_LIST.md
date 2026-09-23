# MIA Architecture, Debugging, and Evaluation Roadmap

## Principles

- Keep the application as one repository while the frontend, API, workflow, worker, and deployment contract evolve together.
- Split a component into a separate repository only after it has an independently versioned public API, release cadence, and external consumer.
- Preserve catalogue run events and artifacts as the local source of truth; external tracing must remain optional.
- Do not remove code until import, runtime, and test evidence establish that it is unused.

## Change Log Rules

- [ ] Treat this file as an append-only project log as well as a roadmap.
- [ ] When a task is completed, keep its original text and timestamp unchanged, mark it `[x]`, and append a dated completion entry with a concise conclusion, affected files, and validation performed.
- [ ] For every new task, scope change, decision, implementation, removal, or follow-up, append a new timestamped entry; never edit or replace an earlier timestamped entry.
- [ ] Use ISO 8601 timestamps with timezone, for example `2026-09-23T14:30:00+02:00`.
- [ ] Record blocked work and rejected alternatives as new timestamped entries so future debugging retains the reasoning and context.

### Log Entries

Append new entries below this line. Do not modify prior entries.

- `2026-09-23T09:49:29+02:00` — Created the architecture, live-runtime-graph, observability, evaluation, and consolidation roadmap. Conclusion: implementation remains deferred pending phase approval.

## Phase 1 — Architecture Inventory

- [ ] Produce a dependency and ownership map for the frontend, API, worker, workflow, agents, tools, AAS pipeline, persistence, storage, and integrations.
- [ ] Identify every executable entry point and document its inputs, outputs, dependencies, and smoke test.
- [ ] Record oversized modules, circular dependencies, duplicated logic, stale adapters, and likely dead code.
- [ ] Classify `integrations/basyx.py` and `integrations/pdf2aas.py` as retain, extract, or remove after a focused compatibility review.

Acceptance: an architecture report identifies a single owner and test command for every production component, with no code moved or removed in this phase.

## Phase 2 — Internal Module Boundaries

- [ ] Define and enforce dependency direction: API/worker -> application facade -> workflow/services -> domain/tools -> persistence/storage/integrations.
- [ ] Keep `workflow/` responsible only for orchestration and state transitions; move business logic into focused services or tools where necessary.
- [ ] Break oversized modules into cohesive units without changing public API contracts.
- [ ] Create one stable Python command entry point for extraction, mapping, template inspection, AAS build/validation, and evaluation.
- [ ] Add a concise command catalogue with input examples and deterministic fixture-based smoke tests.

Acceptance: every tool can run independently from a command line without starting the frontend or full application stack.

## Phase 3 — Code Reduction and Hardening

- [ ] Establish a baseline for source size, dependency count, test coverage by module, and current behavior before changing code.
- [ ] Find duplicated implementations, obsolete compatibility paths, unused dependencies, dead branches, unreachable handlers, and redundant abstractions with static searches and runtime/import evidence.
- [ ] Remove or consolidate confirmed bloat in small changes, preserving behavior and recording each removal with its evidence and conclusion in the change log.
- [ ] Review oversized files by responsibility and split only where a module has multiple independently testable jobs; avoid splitting solely to reduce line count.
- [ ] Harden external boundaries: validate inputs, bound network/download sizes and retries, set timeouts, normalize errors, and avoid leaking secrets or raw sensitive payloads into logs/traces.
- [ ] Harden persistence and worker behavior: exercise restart/resume, duplicate delivery, concurrent claims, failed jobs, and cleanup/retention behavior.
- [ ] Add focused security and robustness checks for URL/redirect policy, artifact access ownership, malformed model output, and invalid workflow resume payloads.
- [ ] Compare the resulting dependency and size baseline; document intentional growth and any remaining high-risk debt.

Acceptance: each removed or consolidated path has evidence it is unused or behaviorally equivalent; boundary and recovery failures have targeted regression tests; the full quality gate remains green.

## Phase 4 — Isolated Tool Testing

- [ ] Give each tool a narrow input/output contract and a stable Python module entry point where command-line execution is useful.
- [ ] Make tests construct tools directly without importing or starting FastAPI, LangGraph, the worker, or the frontend.
- [ ] Inject network clients, model clients, clocks, storage, and persistence through small protocols or constructors so tests can use fakes.
- [ ] Separate pure transformations from I/O: deterministic parsing/mapping/validation should accept data and return results without implicit network or database access.
- [ ] Provide fixture-driven CLI examples for extraction, mapping, template inspection, AAS build/validation, and evaluation, with explicit output directories and no writes to shared production data.
- [ ] Add per-tool unit tests, contract tests for adapters, and a small number of integration tests for the seams between tools.
- [ ] Label network, model, browser, database, and other credential-dependent tests so the default suite stays offline and repeatable.
- [ ] Document the exact command to run one test file, one test case, one tool CLI, and the complete suite.

Acceptance: a developer can diagnose a tool in isolation with one command, deterministic fixtures, and no unrelated service startup; integration tests still verify that the same tools compose correctly.

## Phase 5 — Live Runtime Graph

- [ ] Define a shared `RunEvent` schema for workflow node start/end/failure, tool calls, artifacts, parent/child span IDs, timestamps, duration, and safe summary metadata.
- [ ] Emit graph-friendly events from FastAPI, the worker, LangGraph nodes, agents, and tool boundaries; never store credentials, request bodies, or raw private content in graph metadata.
- [ ] Build a local Python runtime viewer that opens in a separate window while MIA runs.
- [ ] The Python viewer must show workflow topology, active node, execution order, status, latency, retries, errors, and artifact links for a selected run.
- [ ] Prefer a lightweight viewer backed by the durable catalogue/event stream; choose the Python UI library after verifying development-environment compatibility.
- [ ] Add a debug-only frontend graph using the same event API and graph model, rather than a second implementation of workflow state.
- [ ] Guard frontend debugging with an explicit local/development configuration flag and authorization; never expose traces in normal production screens.
- [ ] Add replay mode: animate a completed run's recorded node/event sequence without invoking agents or network tools again.

Acceptance: a local run appears in the Python graph window and debug frontend with matching status, durations, and errors; replaying never mutates data or invokes external services.

## Phase 6 — Observability and Performance

- [ ] Consolidate process logging and durable run events under consistent event names and correlation IDs.
- [ ] Add request, workflow, node, tool, queue-wait, persistence, and end-to-end duration metrics.
- [ ] Track model name, request count, tokens, cost when supplied by the provider, retry count, and cache hit rate without persisting secrets.
- [ ] Add optional LangSmith tracing around workflow, agent, tool, and evaluation boundaries using environment configuration.
- [ ] Keep local development and CI operational with LangSmith disabled.
- [ ] Surface trace IDs in the local graph/debug frontend for direct navigation to the external trace when configured.
- [ ] Establish baseline latency and quality reports before optimization work begins.

Acceptance: one run can be followed from HTTP request or worker job through durable events, local graph, logs, artifacts, and optional LangSmith trace.

## Phase 7 — Testing and Evaluation

- [ ] Keep deterministic unit, contract, and workflow tests as the mandatory CI gate.
- [ ] Add checked-in regression datasets for extraction, mapping, AAS validation, routing, and failure handling.
- [ ] Report per-case pass/fail, duration, and regression against the accepted baseline.
- [ ] Add separately labelled live tests for real models, crawling, and remote services; exclude them from default CI and require explicit credentials.
- [ ] Use LangSmith datasets/evaluators for agent quality experiments and preserve a local JSON equivalent for reproducibility and offline debugging.
- [ ] Add performance budgets for critical paths and fail the appropriate non-live gate on meaningful regressions.

Acceptance: CI detects deterministic correctness regressions; developers can compare quality, latency, model usage, and failures across evaluated versions.

## Phase 8 — Consolidation and Repository Decisions

- [ ] Remove confirmed dead code in small, separately reviewed changes with replacement tests where needed.
- [ ] Extract optional features behind explicit extras or packages before considering new repositories.
- [ ] Consider a separate repository only for a reusable IDTA/AAS library or generic extraction library with independent consumers and a stable contract.
- [ ] Keep a compatibility matrix for extracted packages, the MIA backend, and the frontend API.

Acceptance: the remaining application has documented ownership, smaller cohesive modules, and no dormant production dependencies without an explicit reason.

## Timestamped Updates

- `2026-09-23T09:50:36+02:00` — Added planned work for code reduction, boundary hardening, recovery behavior, and isolated tool testing. Conclusion: implementation is still pending; sequence these after the architecture inventory and module-boundary review.
- `2026-09-23T09:57:13+02:00` — Consolidation review found two lint issues in the branch delta; shortened the worker-claim docstring and removed the obsolete download type import. Conclusion: changes are limited to lint cleanup; validation is pending.
- `2026-09-23T09:57:32+02:00` — Applied Ruff formatting to four branch-touched files flagged by the formatter check. Conclusion: style cleanup only; full quality validation is pending.
- `2026-09-23T09:57:56+02:00` — Added an explicit optional URL type to the database selection variable after mypy reported an incompatible assignment. Conclusion: type annotation resolves the branch's strict typecheck; full validation is pending.
- `2026-09-23T10:24:36+02:00` — Completed pre-consolidation validation: 110 Python tests pass with isolated local runtime/auth fixtures; Ruff, Ruff format, ESLint, mypy, TypeScript, production frontend build, Compose config, and pinned-standards checks pass. Conclusion: the 56-commit feature history is ready to consolidate into one commit on `develop`; local workstation credentials require test isolation because `.env.local` selects Clerk and remote Postgres.
