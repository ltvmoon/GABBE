# GABBE CLI Reference (experimental)

## Installation

```bash
pip install -e ".[dev]"   # development / editable
pip install .             # production
```

Entry point: `gabbe` (defined in `pyproject.toml → [project.scripts]`)

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GABBE_API_URL` | `https://api.openai.com/v1/chat/completions` | OpenAI-compatible endpoint |
| `GABBE_API_KEY` | *(required for LLM features)* | Bearer token for the LLM API |
| `GABBE_API_MODEL` | `gpt-4o` | Model name sent in API requests |
| `GABBE_LLM_TEMPERATURE` | `0.7` | Sampling temperature (0.0–1.0) |
| `GABBE_LLM_TIMEOUT` | `30` | HTTP timeout for LLM calls (seconds) |
| `GABBE_ROUTE_THRESHOLD` | `50` | Complexity score above which a prompt routes REMOTE |
| `GABBE_LLM_MAX_RETRIES`| `3` | (Internal) Number of retry attempts for LLM calls |
| `GABBE_LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `GABBE_MAX_COST_USD` | `5.0` | Maximum cost (USD) budget per run |
| `GABBE_MAX_TOKENS_PER_RUN` | `100000` | Maximum token limit per run |
| `GABBE_MAX_TOOL_CALLS_PER_RUN` | `50` | Maximum tool calls per run |
| `GABBE_MAX_ITERATIONS` | `25` | Maximum brain loop iterations per run |
| `GABBE_MAX_WALL_TIME` | `300` | Maximum wall-clock time per run (seconds) |
| `GABBE_MAX_RECURSION_DEPTH` | `5` | Maximum agent recursion depth |
| `GABBE_MAX_RETRIES_PER_TOOL` | `3` | Maximum retries per tool call |
| `GABBE_SUBPROCESS_TIMEOUT` | `300` | Timeout for verify shell commands (seconds) |
| `GABBE_POLICY_FILE` | `project/policies.yml` | Path to YAML policy file for tool access control |
| `GABBE_ESCALATION_MODE` | `cli` | Escalation mode: `cli` (interactive), `file` (pause), `silent` (auto-reject) |
| `GABBE_OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |

---

## Global Flags

| Flag | Description |
|---|---|
| `--help` / `-h` | Show help for the command or sub-command and exit |
| `--version` | Print the installed version (`gabbe x.y.z`) and exit |

---

## Commands

### `gabbe init`

Initialize (or migrate) the SQLite database in `project/state.db`.

```bash
gabbe init
```

**Exit codes:**
- `0` — success
- `1` — configuration or filesystem error

---

### `gabbe db --init`

Alias for `gabbe init`.

```bash
gabbe db --init
```

---

### `gabbe sync`

Bidirectional sync between `project/TASKS.md` and the SQLite `tasks` table.

**Logic:**
1. **Marker-Based Sync**: Looks for `<!-- GABBE:TASKS:START -->` and `<!-- GABBE:TASKS:END -->`.
    - If found, ONLY updates the content between these markers.
    - Preserves all other content (notes, headers) in `project/TASKS.md`.
2. **Legacy Fallback**: If no markers are found, it behaves as a full-file overwrite (or wraps content in markers on first export).
3. **Conflict Resolution**:
    - File newer than DB → Import tasks between markers.
    - DB newer than file → Export tasks between markers (atomic write).

```bash
gabbe sync
```

---

### `gabbe status`

Display the project dashboard: phase, task counts, progress bar.

The phase is read from `project_state` (key `current_phase`).
Set it with:

```bash
sqlite3 project/state.db "INSERT OR REPLACE INTO project_state (key, value) VALUES ('current_phase', 'S03_ARCH');"
```

---

### `gabbe verify`

Run project integrity checks:

1. Check required files exist (`agents/AGENTS.md`, `agents/CONSTITUTION.md`, `project/TASKS.md`)
2. Warn if `project/state.db` is missing
3. Read `## Commands` section of `agents/AGENTS.md` and run `test`, `lint`, `security_scan` commands

**`AGENTS.md` Commands section format:**
```markdown
## Commands
test: pytest scripts/tests/ -v
lint: ruff check .
security_scan: bandit -r gabbe/
```

Only lines inside `## Commands` (until the next `##` heading) are parsed.

**Parsing rules:**
- Section header matching is **case-insensitive** (`## Commands` and `## commands` both work)
- Supported command keys: `test`, `lint`, `security_scan`, `build`
- Values are stripped of surrounding single/double quotes
- Commands are executed with `shell=False` (no shell expansion)

**Exit codes:** `0` = all passed, `1` = at least one check failed

---

### `gabbe route <prompt>`

Route a prompt to LOCAL or REMOTE LLM based on complexity and PII detection.

```bash
gabbe route "Fix the typo in utils.py"
gabbe route "Architect a new distributed caching layer"
```

**PII detection:** Email addresses, US phone numbers, SSNs, credit card numbers,
and patterns like `password: ...` or `api_key=...` force LOCAL routing.

**Output:** `LOCAL` or `REMOTE`

---

### `gabbe brain activate`

Run one cycle of the Active Inference Loop:
1. Observe task statistics from DB
2. Send state description to LLM
3. Print recommended next action

Requires `GABBE_API_KEY`.

---

### `gabbe brain evolve --skill <name>`

Evolutionary Prompt Optimization for a skill:
1. Fetch current best gene from `genes` table
2. Ask LLM to mutate/improve the prompt
3. Store new generation in `genes` table

```bash
gabbe brain evolve --skill code-review
```

Requires `GABBE_API_KEY`.

---

### `gabbe brain heal`

Run the Self-Healing Watchdog:
1. Verify DB connectivity
2. Check required project files exist
3. Print health report

Does **not** require `GABBE_API_KEY`.

---

### `gabbe forecast`

Run a strategic forecast evaluating remaining tasks and projecting future costs and token usage based on historical run data. Captures a snapshot to `forecast_snapshots`.

---

### `gabbe serve-mcp`

Starts a zero-dependency JSON-RPC Model Context Protocol (MCP) server on `stdin`/`stdout`. It wraps internal tools in the `ToolGateway` for strict budget, role, and rate-limit enforcement. Includes telemetry and context tracing.

---

## Database Schema

Located at `project/state.db` (SQLite 3).

### `schema_version`
| Column | Type | Description |
|---|---|---|
| `version` | INTEGER | Current schema version (incremented on migrations) |

### `tasks`
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment ID |
| `title` | TEXT NOT NULL UNIQUE | Task description (unique — used as upsert key) |
| `status` | TEXT | `TODO`, `IN_PROGRESS`, or `DONE` |
| `tags` | TEXT | Optional comma-separated tags |
| `created_at` | DATETIME | Creation timestamp (UTC) |
| `updated_at` | DATETIME | Last modification timestamp (UTC) |

### `project_state`
| Column | Type | Description |
|---|---|---|
| `key` | TEXT PK | State key (e.g., `current_phase`) |
| `value` | TEXT | State value |
| `updated_at` | DATETIME | Last modification timestamp |

### `events`
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment ID |
| `timestamp` | DATETIME | Event time |
| `actor` | TEXT | Who generated the event |
| `action` | TEXT | Action type |
| `message` | TEXT | Human-readable description |
| `context_summary` | TEXT | Optional context snapshot |

### `genes`
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment ID |
| `skill_name` | TEXT | Skill this gene belongs to |
| `prompt_content` | TEXT | Prompt text |
| `success_rate` | REAL | Score 0.0–1.0 (updated externally) |
| `generation` | INTEGER | Evolution generation counter |
| `created_at` | DATETIME | Creation timestamp |

### `pricing_registry` (v3)
| Column | Type | Description |
|---|---|---|
| `model_id` | TEXT PK | LLM Model Identifier (e.g. gpt-4o) |
| `input_token_price` | REAL | Cost per input token |
| `output_token_price` | REAL | Cost per output token |
| `reasoning_token_price` | REAL | Cost per reasoning token |
| `cache_creation_price` | REAL | Cost per cache token |
| `cache_read_price` | REAL | Cost per cache read token |

### `runs` (v3 updates)
| Column | Type | Description |
|---|---|---|
| `id` | TEXT PK | Run ID (UUID) |
| `command` | TEXT | CLI command executed |
| `status` | TEXT | running, completed, error, budget_exceeded, escalated |
| `initiator` | TEXT | Initiator type (cli, bash, mcp) |
| `total_cost_usd` | REAL | Aggregated cost in USD |
| `total_tokens_used` | INTEGER | Aggregated token scale |

### `audit_spans` (v3)
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Span increment ID |
| `run_id` | TEXT | FK to runs |
| `event_type` | TEXT | e.g. "tool_call", "llm_call" |
| `duration_ms` | REAL | Execution time |
| `cost_usd` | REAL | Cost of this specific span |
| `reasoning_content` | TEXT | Chain of Thought content |

### `forecast_snapshots` (v3)
Snapshot of projected costs generated by `gabbe forecast`.

---

### `gabbe runs [--status STATUS] [--limit N]`

List recent agent runs from the `runs` table.

```bash
gabbe runs                          # all runs, newest first (max 20)
gabbe runs --status completed       # only completed runs
gabbe runs --status budget_exceeded # runs stopped by budget
gabbe runs --limit 5                # show only 5 rows
```

**Status values:** `running`, `completed`, `error`, `budget_exceeded`, `escalated`

---

### `gabbe audit <run-id> [--format json|table]`

Display the structured audit trace for a run.

```bash
gabbe audit abc123-...              # table view (default)
gabbe audit abc123-... --format json  # full JSON trace
```

Reads from `audit_spans` table. Columns shown in table mode: event type, node name, duration (ms), cost (USD), status.

---

### `gabbe replay <run-id> [--from-step N]`

Replay a past run from its checkpoints, substituting recorded tool outputs.

```bash
gabbe replay abc123-...             # replay from step 0
gabbe replay abc123-... --from-step 3  # skip first 3 steps
```

Requires that `checkpoints` rows exist for the run. Use `gabbe brain activate` with checkpoint saving enabled.

---

### `gabbe resume <run-id>`

Resume a run that was paused by the escalation handler (file mode).

```bash
gabbe resume abc123-...
```

Lists all pending escalations for the run, prompts for approve/reject per item, and updates the `pending_escalations` table.

---

## Troubleshooting

**`EnvironmentError: GABBE_API_KEY is not set`**
Set the env var: `export GABBE_API_KEY=sk-...` before running LLM-dependent commands.

**`Database not initialized`**
Run `gabbe init` first.

**`project/TASKS.md` always re-importing on every `gabbe sync`**
Check that `updated_at` is being written correctly to the DB.
The DB timestamp (from `MAX(updated_at) FROM tasks`) must be ≥ the file mtime after an import.

**CI: `validate_skills.py` not found**
The `agents/scripts/` directory contains project-specific validators generated by `scripts/init.py`.
Run `python3 scripts/init.py` once to generate them before CI runs.
