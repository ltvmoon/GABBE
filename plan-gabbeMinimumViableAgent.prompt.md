Plan: Harden GABBE Agent Platform (DRAFT)

TL;DR: Add platform controls around the current CLI/brain loop so each run has enforced budgets, deterministic stops, mediated tool access, policy checks, structured audit, replayable traces, and clear human escalation. Anchor changes in the existing SQLite state, LLM wrapper, and router while keeping CLI UX intact.

Steps
1) Define runtime budget model (tokens, tool calls, wall time, iterations) and config knobs in gabbe/config.py; persist per-run/session budget rows in SQLite (extend gabbe/database.py schema) and expose flags in gabbe/main.py CLI.
2) Wrap agent loop with a BudgetManager and HardStop guard in gabbe/brain.py to count tokens/tool calls/iterations, enforce wall-clock timeout, and terminate deterministically with structured stop reasons.
3) Introduce Tool Gateway module (new file) mediating all external calls; refactor LLM/tool entry points to route through it, adding parameter/schema validation, rate limiting, circuit breaking, and audit hooks before delegating to existing helpers like gabbe/llm.py.
4) Add Policy Engine (RBAC/ABAC rules stored in DB or versioned YAML) to evaluate allowed tools/params/context; integrate checks into the gateway and pre-action gates in gabbe/brain.py and routing in gabbe/route.py.
5) Implement structured audit logging: per-step JSON logs with correlation IDs, inputs/outputs, tool calls, policy decisions, budget snapshots, final state, and failure reasons; emit to file/DB events table in gabbe/database.py; wire logging in brain, gateway, router, and sync flows gabbe/sync.py.
6) Add replay capability: persist prompt/tool call transcripts and policy version in DB; add CLI command (e.g., gabbe replay <run-id>) to rehydrate state and re-execute with deterministic controls using captured tool stubs/mocks.
7) Define escalation triggers (budget exhaustion, repeated tool failures, ambiguous routing, policy violations) and implement an EscalationHandler that posts tasks or marks state for human review; surface in CLI status and optionally send notifications; integrate into brain stop path and gateway errors.
8) Enhance observability: structured logging backend, optional OpenTelemetry traces/spans, metrics for budgets/latency/error rates; minimal adapter layer to avoid leaking secrets.
9) Extend tests: unit tests for budget enforcement, hard stops, policy allow/deny, gateway validation, audit emission, replay determinism; integration tests for CLI flows in scripts/tests; update docs/CLI reference and agent guides to describe new controls.

Verification
- Run pytest suite (scripts/tests) with new cases for budgets, gateway, policy, replay.
- Manual end-to-end: init project, run brain with tight budgets to confirm hard stops; force tool failure to see escalation; run replay to reproduce a prior run; inspect structured logs and DB events.

Decisions
- Enforce budgets and stop conditions in platform code, not prompts; tools gated through a single mediator; policies externalized from prompts and versioned for replay.





# Plan: Harden GABBE into a Minimum Viable Agent Platform

**TL;DR** — Introduce 7 platform-level modules (`budget`, `hardstop`, `gateway`, `policy`, `audit`, `replay`, `escalation`) alongside the existing GABBE CLI. Each module is a new file under `gabbe/` wired into the existing `brain.py` loop and `llm.py` wrapper via thin integration points. All state persists to existing SQLite via a schema v3 migration. Zero changes to existing function signatures — only additive wrapping. No new external dependencies beyond `jsonschema` (optional, for tool gateway schema validation).

## Architecture Overview

```
CLI (main.py)
  │
  ├── gabbe brain activate
  │     │
  │     ▼
  │   ┌─────────────────────────────────────────────┐
  │   │  RunContext (new)                            │
  │   │  ┌──────────┐ ┌──────────┐ ┌─────────────┐  │
  │   │  │ Budget   │ │ HardStop │ │ AuditTracer │  │
  │   │  └────┬─────┘ └────┬─────┘ └──────┬──────┘  │
  │   │       │            │               │         │
  │   │       ▼            ▼               ▼         │
  │   │  ┌──────────────────────────────────────┐    │
  │   │  │          Agent Loop (brain.py)       │    │
  │   │  │  observe → decide → act → repeat     │    │
  │   │  └───────────────┬──────────────────────┘    │
  │   │                  │                           │
  │   │       ┌──────────▼──────────┐                │
  │   │       │   PolicyEngine      │                │
  │   │       │ (evaluate before    │                │
  │   │       │  each action)       │                │
  │   │       └──────────┬──────────┘                │
  │   │                  │                           │
  │   │       ┌──────────▼──────────┐                │
  │   │       │   ToolGateway       │                │
  │   │       │ validate → rate     │                │
  │   │       │ limit → execute     │                │
  │   │       │ → audit             │                │
  │   │       └──────────┬──────────┘                │
  │   │                  │                           │
  │   │       ┌──────────▼──────────┐                │
  │   │       │  EscalationHandler  │                │
  │   │       │ (triggers on        │                │
  │   │       │  budget/failure/    │                │
  │   │       │  policy violation)  │                │
  │   │       └─────────────────────┘                │
  │   └─────────────────────────────────────────────┘
  │
  ├── gabbe replay <run-id>    (new command)
  ├── gabbe audit  <run-id>    (new command)
  └── gabbe resume <run-id>    (new command)
```

---

## New Files

| File | Responsibility |
|---|---|
| `gabbe/budget.py` | `Budget` dataclass + `BudgetExceeded` exception |
| `gabbe/hardstop.py` | `HardStop` guard with iteration/depth/time limits |
| `gabbe/gateway.py` | `ToolGateway` registry, validation, rate limiting, circuit breaking |
| `gabbe/policy.py` | `PolicyEngine` + rule definitions (YAML-backed) |
| `gabbe/audit.py` | `AuditTracer` — structured span logging to SQLite |
| `gabbe/replay.py` | `ReplayRunner` — checkpoint store + replay logic |
| `gabbe/escalation.py` | `EscalationHandler` — triggers, persistence, CLI resume |
| `gabbe/context.py` | `RunContext` — ties all 7 modules into a single per-run object |
| `project/policies.yml` | Declarative policy rules (tool allowlists, budget limits, escalation triggers) |

---

## Step-by-Step Implementation

### Step 1 — Schema Migration (v3) in `gabbe/database.py`

Add tables inside existing `_migrate()` function under `if current < 3:` block. **No changes to existing tables.**

**New tables:**

- **`runs`** — one row per agent execution
  - `id TEXT PRIMARY KEY` (UUID), `command TEXT`, `started_at DATETIME`, `ended_at DATETIME`, `status TEXT` (running/completed/budget_exceeded/escalated/error), `stop_reason TEXT`, `config_snapshot TEXT` (JSON — budget limits, policy version)

- **`audit_spans`** — replaces/extends unused `events` table concept
  - `id INTEGER PRIMARY KEY`, `run_id TEXT FK→runs`, `span_id TEXT`, `parent_span_id TEXT`, `timestamp DATETIME`, `event_type TEXT` (llm_call/tool_call/policy_check/step/escalation), `node_name TEXT`, `input_data TEXT` (JSON), `output_data TEXT` (JSON), `token_usage TEXT` (JSON), `duration_ms REAL`, `status TEXT`, `metadata TEXT` (JSON)

- **`budget_snapshots`** — periodic budget state per run
  - `id INTEGER PRIMARY KEY`, `run_id TEXT FK→runs`, `step INT`, `tokens_used INT`, `tool_calls_used INT`, `wall_time_sec REAL`, `iterations INT`, `timestamp DATETIME`

- **`checkpoints`** — replay state snapshots
  - `id INTEGER PRIMARY KEY`, `run_id TEXT FK→runs`, `step INT`, `node_name TEXT`, `state_snapshot TEXT` (JSON), `policy_version TEXT`, `parent_checkpoint_id INTEGER FK→checkpoints`, `created_at DATETIME`

- **`pending_escalations`** — human-in-the-loop queue
  - `id INTEGER PRIMARY KEY`, `run_id TEXT FK→runs`, `step INT`, `trigger TEXT`, `context TEXT` (JSON), `status TEXT` (pending/approved/rejected), `response TEXT`, `created_at DATETIME`, `resolved_at DATETIME`

Bump `SCHEMA_VERSION` to `3`. Existing `events` table stays as-is (backward compat).

### Step 2 — Config Extensions in `gabbe/config.py`

Add env-driven defaults **after** existing constants (around L106):

```
GABBE_MAX_TOKENS_PER_RUN     (default: 100000)
GABBE_MAX_TOOL_CALLS_PER_RUN (default: 50)
GABBE_MAX_ITERATIONS         (default: 25)
GABBE_MAX_WALL_TIME          (default: 300 seconds)
GABBE_MAX_RECURSION_DEPTH    (default: 5)
GABBE_MAX_RETRIES_PER_TOOL   (default: 3)
GABBE_POLICY_FILE            (default: PROJECT_ROOT / "project/policies.yml")
GABBE_ESCALATION_MODE        (default: "cli" — options: cli/file/silent)
```

Use existing `_safe_int` helper. No changes to existing constants.

### Step 3 — Budget Module (`gabbe/budget.py`) — NEW FILE

- `BudgetExceeded(Exception)` with `reason: str` and `snapshot: dict`
- `Budget` dataclass: `max_tokens`, `max_tool_calls`, `max_wall_seconds`, `max_iterations`, `max_cost_usd` + current counters + `_start_time`
- `Budget.check()` — raises `BudgetExceeded` if any limit exceeded
- `Budget.record_tokens(usage_dict)` — accumulate from LLM response `usage` field
- `Budget.record_tool_call()` — increment tool call counter
- `Budget.record_iteration()` — increment iteration counter
- `Budget.snapshot() -> dict` — serializable current state
- `Budget.remaining() -> dict` — remaining budget per dimension
- Factory: `Budget.from_config()` reads config constants; `Budget.from_dict(d)` for replay

**Integration point:** Called from `RunContext`; checked before each LLM call and tool call.

### Step 4 — HardStop Module (`gabbe/hardstop.py`) — NEW FILE

- `MaxIterationsExceeded`, `MaxDepthExceeded`, `TimeoutExceeded` exceptions (all subclass `HardStopTriggered`)
- `HardStop` class: `max_iterations`, `max_depth`, `timeout_sec` + counters
- `HardStop.tick(depth=0)` — increment iteration, check all limits
- `HardStop.remaining_steps() -> int` — for proactive wrap-up (LangGraph pattern)
- `HardStop.should_wrap_up(threshold=2) -> bool` — true when ≤ `threshold` iterations remain

**Integration point:** `brain.py` loop calls `hard_stop.tick()` each iteration. When `should_wrap_up()` is true, system prompt gets appended with "wrap up" instruction.

### Step 5 — Audit Tracer (`gabbe/audit.py`) — NEW FILE

- `AuditTracer` class initialized with `run_id` and DB connection
- `tracer.start_span(event_type, node_name, input_data, parent_span_id=None) -> span_id` — generates `span_id` (hex UUID prefix), records start time
- `tracer.end_span(span_id, output_data, token_usage, status, metadata)` — computes duration, writes to `audit_spans` table
- `@traced` decorator for wrapping functions: auto-creates span, captures args/return/exception
- `tracer.snapshot_budget(step, budget)` — writes to `budget_snapshots`
- `tracer.get_run_trace(run_id) -> list[dict]` — reconstructs full trace tree
- `tracer.export_json(run_id) -> str` — full run as JSON for debugging

**Integration points:**
- Wrap `call_llm()` — extract `usage` from OpenAI response (currently discarded at llm.py L36)
- Wrap tool gateway calls
- Wrap policy evaluations
- Record final run status

**Key change to `llm.py`:** Modify `_handle_response()` to return `(content, usage_dict)` tuple instead of just `content`. Existing callers that only need `content` still work via `call_llm()` wrapper returning just `content`. New `call_llm_traced()` in audit module returns both.

### Step 6 — Tool Gateway (`gabbe/gateway.py`) — NEW FILE

- `ToolDefinition` dataclass: `name`, `description`, `parameters` (JSON Schema), `handler: Callable`, `allowed_roles: set`, `rate_limit_per_min: int`, `circuit_breaker_threshold: int`
- `ToolGateway` class:
  - `register(tool_def)` — add tool to registry
  - `execute(name, arguments, role, run_context) -> result` — the single entry point:
    1. Lookup tool → `ToolNotFound` if missing
    2. Policy check via `run_context.policy.evaluate(...)` → `PolicyDenied` if rejected
    3. Budget check via `run_context.budget.record_tool_call()` → `BudgetExceeded`
    4. Schema validation via `jsonschema.validate()` (soft fail if `jsonschema` not installed)
    5. Rate limit check (sliding window on `_call_times` deque)
    6. Circuit breaker check (consecutive failures ≥ threshold → `CircuitOpen`)
    7. Execute handler
    8. Audit log via `run_context.tracer`
    9. Return result
  - `list_tools(role) -> list` — return allowed tools for a role

**Current tools to register:** `call_llm` (wrapping existing function), subprocess commands from `verify.py`, future MCP/external integrations. Initially the gateway wraps `call_llm` and any shell commands.

**No breaking changes:** Existing `call_llm()` function stays as-is. Gateway calls it internally. Brain loop goes through gateway instead of calling `call_llm()` directly.

### Step 7 — Policy Engine (`gabbe/policy.py`) — NEW FILE

- `PolicyResult` dataclass: `allowed: bool`, `reason: str`, `policy_name: str`
- `Policy` protocol/base: `name: str`, `check(context: dict) -> PolicyResult`
- Built-in policies:
  - `ToolAllowlistPolicy` — from `policies.yml` `allowed_tools` list
  - `BudgetPolicy` — deny if remaining budget below threshold
  - `ContentSafetyPolicy` — uses existing `PII_PATTERNS` from config.py
  - `ParameterRangePolicy` — tool param bounds from `policies.yml`
  - `RolePolicy` — simple RBAC (agent role → permitted tool set)
- `PolicyEngine`:
  - `__init__(policies: list[Policy])` + `PolicyEngine.from_yaml(path)` factory
  - `evaluate(context: dict) -> PolicyResult` — deny-first chain; first denial wins
  - `evaluate_all(context: dict) -> list[PolicyResult]` — for audit (log all checks)

**`project/policies.yml` format:**
```yaml
version: "1"
tools:
  allowed: ["call_llm", "run_test", "run_lint"]
  denied: ["run_security_scan"]  # override for specific contexts
budgets:
  max_tokens: 100000
  max_tool_calls: 50
escalation:
  triggers:
    - budget_exhaustion
    - repeated_tool_failure
    - policy_violation
  threshold_consecutive_failures: 3
roles:
  agent: ["call_llm", "run_test"]
  admin: ["*"]
```

Policy version string is captured in checkpoints for replay determinism.

### Step 8 — Escalation Handler (`gabbe/escalation.py`) — NEW FILE

- `EscalationTrigger` enum: `BUDGET_EXHAUSTED`, `REPEATED_TOOL_FAILURE`, `AMBIGUOUS_DECISION`, `POLICY_VIOLATION`, `MAX_ITERATIONS`
- `EscalationHandler`:
  - `__init__(mode, db_conn, run_id)` — mode from `GABBE_ESCALATION_MODE`
  - `escalate(trigger, context_dict, state_snapshot) -> EscalationResult`
    - **CLI mode:** prints context, prompts `[approve/reject/edit]` via `input()`
    - **File mode:** writes to `pending_escalations` table + prints "Resume with `gabbe resume <run-id>`" then exits
    - **Silent mode:** logs escalation, returns auto-reject (safe default)
  - `check_pending(run_id) -> list` — list pending escalations
  - `resolve(escalation_id, response)` — mark resolved

**Integration points:**
- `BudgetExceeded` caught in brain loop → escalation
- Gateway circuit breaker open → escalation
- Policy denial → escalation (configurable: deny-and-escalate vs deny-and-stop)
- `HardStop` remaining ≤ 1 → escalation

### Step 9 — Run Context (`gabbe/context.py`) — NEW FILE

Ties everything together in a single per-run object:

- `RunContext`:
  - `run_id: str` (UUID)
  - `budget: Budget`
  - `hard_stop: HardStop`
  - `tracer: AuditTracer`
  - `gateway: ToolGateway`
  - `policy: PolicyEngine`
  - `escalation: EscalationHandler`
  - `__enter__` / `__exit__` — context manager that creates the `runs` row on enter, finalizes status/stop_reason on exit
  - `RunContext.from_config()` — factory from env/config
  - `RunContext.from_checkpoint(checkpoint_id)` — factory for replay

### Step 10 — Brain Loop Integration in `gabbe/brain.py`

Modify `activate_brain()` to accept optional `RunContext`. **Preserve existing no-arg behavior for backward compat:**

```python
def activate_brain(run_context=None):
    ctx = run_context or RunContext.from_config()  # default: auto-create
    with ctx:
        # existing observe step (DB read) — wrapped in ctx.tracer span
        # existing LLM call — goes through ctx.gateway.execute("call_llm", ...)
        # ctx.hard_stop.tick() each iteration
        # ctx.budget.check() each iteration
        # on BudgetExceeded → ctx.escalation.escalate(...)
        # on HardStopTriggered → ctx.escalation.escalate(...)
```

Similarly wrap `evolve_prompts()` and `run_healer()`.

**Key principle:** The loop structure doesn't change — we add `ctx.tick()` and `ctx.gateway.execute()` calls around existing logic.

### Step 11 — LLM Response Enhancement in `gabbe/llm.py`

Modify `_handle_response()` at L30 to also return token usage:

```python
def _handle_response(response):
    data = response.json()
    usage = data.get("usage", {})  # NEW: extract token usage
    if "choices" in data and data["choices"]:
        content = data["choices"][0]["message"]["content"].strip()
        return content, usage  # Changed: tuple
    return None, usage
```

Update `call_llm()` to maintain backward compat — returns `str | None` as before. Add `call_llm_with_usage()` that returns `(str | None, dict)` for budget tracking.

### Step 12 — Replay Module (`gabbe/replay.py`) — NEW FILE

- `CheckpointStore`:
  - `save(run_id, step, node_name, state, policy_version, parent_id=None)` → insert into `checkpoints`
  - `get_history(run_id) -> list[dict]` → ordered checkpoint list
  - `load(checkpoint_id) -> dict` → deserialize state snapshot
- `ReplayRunner`:
  - `replay(run_id, from_step=0)` — load checkpoints, reconstruct `RunContext` from `runs.config_snapshot`, replay with same policy version, substitute recorded tool outputs (from `audit_spans`) instead of live calls
  - `diff(run_id_a, run_id_b) -> list` — compare two runs step-by-step

**Determinism guarantee:** Same policy version + same tool stubs + same budget limits = same step sequence and termination logic. LLM responses may differ (stochastic), but architectural flow is identical.

### Step 13 — New CLI Commands in `gabbe/main.py`

Add three new subcommands **after** existing brain parser (around L60):

- `gabbe replay <run-id> [--from-step N]` — replay a past run
- `gabbe audit <run-id> [--format json|table]` — display structured trace
- `gabbe resume <run-id>` — resume a paused/escalated run
- `gabbe runs [--status running|completed|escalated]` — list recent runs

Follow existing lazy-import pattern for dispatch.

### Step 14 — Tests (new files in `scripts/tests/`)

| Test File | Coverage |
|---|---|
| `test_budget.py` | Budget creation, check, record, exceeded exception, from_config, snapshot |
| `test_hardstop.py` | Tick, max iterations, max depth, timeout, remaining_steps, should_wrap_up |
| `test_gateway.py` | Register, execute, not-found, schema validation, rate limit, circuit breaker |
| `test_policy.py` | Allowlist, deny, budget policy, content safety, chain evaluation, YAML load |
| `test_audit.py` | Start/end span, DB writes, trace reconstruction, JSON export |
| `test_replay.py` | Checkpoint save/load, replay with stubs, state reconstruction |
| `test_escalation.py` | Trigger types, CLI mode (mock input), file mode (DB persistence), resolve |
| `test_context.py` | RunContext lifecycle, context manager, from_config, from_checkpoint |
| `test_brain_integration.py` | Brain loop with RunContext: budget stop, hard stop, escalation trigger |

Follow existing patterns: `tmp_project` fixture, mock LLM calls, real SQLite in temp dir.

### Step 15 — Documentation Updates

- `docs/CLI_REFERENCE.md` — Add `replay`, `audit`, `resume`, `runs` commands + new env vars
- `docs/QUICK_GUIDE.md` — Add "Platform Controls" section
- `README.md` — Update architecture section with platform layer diagram
- `agents/AGENTS.md` — Update `## Commands` section with new verification commands
- New: `docs/PLATFORM_CONTROLS.md` — comprehensive guide to budgets, policies, audit, replay, escalation

---

## Dependency Impact

| Dependency | Status | Required? |
|---|---|---|
| `jsonschema` | New (optional) | Optional — gateway does best-effort validation without it |
| `PyYAML` | Already in `pyproject.toml` | Used for `policies.yml` loading |
| `requests` | Already in `pyproject.toml` | No change |
| `uuid` (stdlib) | New import | For `run_id` and `span_id` generation |
| `time` (stdlib) | Already used | For monotonic clocks |
| `json` (stdlib) | Already used | For state serialization |
| `collections.deque` (stdlib) | New import | For rate limiting sliding windows |

---

## Migration Safety

| Concern | Mitigation |
|---|---|
| Existing `activate_brain()` callers | `run_context=None` default → auto-creates context, preserving current behavior |
| Existing `call_llm()` return type | Returns `str | None` unchanged; new `call_llm_with_usage()` for budget tracking |
| Schema migration | Additive only (new tables); existing tables untouched; `SCHEMA_VERSION` bump from 2→3 |
| Existing tests | `conftest.py` `tmp_project` fixture creates DB with migration; v3 migration adds new tables transparently |
| Existing CLI commands | No changes to `init/db/sync/verify/status/route/brain` commands |
| `events` table | Kept for backward compat; new `audit_spans` table preferred for structured tracing |

---

## Implementation Order (Dependency Graph)

```
Phase 1 (foundations, no cross-deps):
  ├── config.py additions (Step 2)
  ├── database.py migration v3 (Step 1)
  ├── budget.py (Step 3)
  ├── hardstop.py (Step 4)
  └── audit.py (Step 5)

Phase 2 (depends on Phase 1):
  ├── policy.py (Step 7) — needs config
  ├── gateway.py (Step 6) — needs budget, audit, policy
  ├── escalation.py (Step 8) — needs budget, hardstop
  └── llm.py enhancement (Step 11) — needs audit

Phase 3 (integration, depends on Phase 2):
  ├── context.py (Step 9) — ties all modules
  ├── brain.py integration (Step 10) — needs context
  ├── replay.py (Step 12) — needs audit, checkpoints
  └── main.py CLI additions (Step 13)

Phase 4 (verification):
  ├── Tests (Step 14)
  └── Docs (Step 15)
```

---

## Verification

- `pytest scripts/tests/ -v` — all existing + new tests pass
- `gabbe brain activate` with `GABBE_MAX_ITERATIONS=3` → hard stop after 3 iterations
- `gabbe brain activate` with `GABBE_MAX_TOKENS_PER_RUN=100` → budget exceeded + escalation
- `gabbe audit <run-id>` → structured trace with spans
- `gabbe replay <run-id>` → reproduces step sequence from checkpoints
- `gabbe resume <run-id>` → continues from escalation pause point
- `gabbe runs` → lists all runs with status

## Decisions

- **Budget in platform, not prompts** — The model reasons, the platform enforces (separation of concerns)
- **Additive-only schema migration** — No ALTER TABLE on existing tables; new tables only
- **Optional `jsonschema`** — Gateway works without it (logs warning), no hard dependency
- **Backward-compatible function signatures** — `run_context=None` defaults everywhere
- **SQLite as single persistence layer** — No external services; keeps GABBE self-contained
- **Deny-first policy chain** — Matches NeMo Guardrails pattern; safe default
- **Checkpoint-based replay** — Mirrors LangGraph persistence; JSON state snapshots in SQLite
- **CLI escalation as default** — synchronous `input()` for simplicity; file/silent modes for CI







