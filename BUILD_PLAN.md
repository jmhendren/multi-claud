# Build Plan: Multi-Claud

## Objective

Build a CLI tool that orchestrates up to 4 parallel Claude Code sessions working on the same project. It creates build plans, assigns packets to workers running in isolated git worktrees, tracks progress on a web-based kanban dashboard, detects risks (file conflicts, duplicate effort, unwired code), and enforces documentation from every session.

## Technical Decisions

- **Language:** Python 3.13 (Claude Agent SDK support, universal availability)
- **CLI Framework:** Typer 0.24.1 (modern, type-hint-based CLI)
- **Web Dashboard:** FastAPI 0.135.2 + SSE + single HTML file with SortableJS 1.15.7
- **AI Integration:** Claude Agent SDK 0.1.52 (orchestrator), Claude Code CLI headless mode (workers)
- **State Management:** JSON file with filelock for safe concurrent access
- **Code Isolation:** Git worktrees (one per worker session)
- **Testing:** pytest + pytest-asyncio
- **Distribution:** pip-installable package, open source on GitHub

---

## Packets

### Packet 1: State Management Foundation
**Status:** Complete
**Scope:** Build the core data models and state file manager that everything else depends on.
**Depends on:** Nothing
**Files to create:**
- `multi_claud/state.py` — Pydantic models for Project, Packet, Worker, Risk, ProjectState. State file read/write with filelock.
- `tests/test_state.py` — Comprehensive tests for all state operations.
**Deliverable:** A working state module that can create, read, update, and query project state from a JSON file. Multiple processes can safely read/write the same file.
**Verification:**
- `python -m pytest tests/test_state.py -v` — all tests pass
- State file is valid JSON and human-readable
- File locking prevents corruption under concurrent writes

**Data Models (Pydantic):**
```
ProjectState:
  project: ProjectInfo (name, description, path, created, updated)
  packets: list[Packet]
  workers: list[Worker]
  risks: list[Risk]
  config: Config

Packet:
  id: str
  name: str
  description: str
  status: enum (backlog, ready, in_progress, review, complete, blocked)
  assigned_worker: str | None
  depends_on: list[str]  (packet IDs)
  blocks: list[str]  (packet IDs)
  files_touched: list[str]
  documentation: str | None
  created_at: datetime
  started_at: datetime | None
  completed_at: datetime | None

Worker:
  id: str
  name: str
  status: enum (idle, working, paused, stopped, error)
  assigned_packet: str | None
  worktree_path: str | None
  worktree_branch: str | None
  pid: int | None
  last_activity: datetime | None
  session_log: list[str]

Risk:
  id: str
  type: enum (file_conflict, duplicate_effort, unwired_code, dependency_violation, stale_worker)
  severity: enum (info, warning, critical)
  description: str
  affected_packets: list[str]
  affected_workers: list[str]
  affected_files: list[str]
  detected_at: datetime
  resolved: bool
  resolution: str | None

Config:
  max_workers: int (default 4)
  dashboard_port: int (default 8420)
  dashboard_host: str (default "localhost")
  stale_timeout_minutes: int (default 30)
  model: str (default "claude-opus-4-6")
  worker_model: str (default "claude-sonnet-4-6")
```

---

### Packet 2: CLI Framework
**Status:** Complete
**Scope:** Build the Typer CLI with all subcommands. Wire in `init` and `status` as fully working commands. Other commands are stubs that print "not yet implemented."
**Depends on:** Packet 1
**Files to create:**
- `multi_claud/cli.py` — Typer app with commands: init, plan, start, status, stop, dashboard, merge
**Deliverable:** Running `multi-claud --help` shows all commands. `multi-claud init` creates `.multi-claud/` in a target project. `multi-claud status` reads and displays the state file with rich formatting.
**Verification:**
- `pip install -e ".[dev]"` installs successfully
- `multi-claud --help` shows all commands
- `multi-claud init` in a git repo creates `.multi-claud/state.json`
- `multi-claud status` displays a formatted table of packets and workers

---

### Packet 3: Web Dashboard
**Status:** Complete
**Scope:** Build the FastAPI server and kanban dashboard. The dashboard reads state via SSE and displays it as a kanban board with dark theme.
**Depends on:** Packet 1
**Can be built in parallel with:** Packet 2
**Files to create:**
- `multi_claud/server.py` — FastAPI app with SSE endpoint, serves dashboard HTML
- `dashboard/index.html` — Kanban board (dark theme, SortableJS, SSE client)
- `tests/test_server.py` — Dashboard endpoint tests
**Deliverable:** Running `multi-claud dashboard` opens a browser to a kanban board showing packet status in columns (Backlog, Ready, In Progress, Review, Complete). Cards show packet name, assigned worker, and risk badges. Board updates in real-time via SSE.
**Verification:**
- `python -m pytest tests/test_server.py -v` — all tests pass
- Open `http://localhost:8420` and see the kanban board
- Modify the state file manually and see the dashboard update within 2 seconds

---

### Packet 4: Orchestrator — Build Plan Generation
**Status:** Complete
**Scope:** Integrate the Claude Agent SDK to create an orchestrator that reads a project and generates a build plan with packets, dependencies, and risk notes.
**Depends on:** Packet 1, Packet 2
**Can be built in parallel with:** Packet 5
**Files to create:**
- `multi_claud/orchestrator.py` — Agent SDK integration, plan generation, packet assignment logic
- `templates/orchestrator-prompt.md` — System prompt for the orchestrator Claude session
- `tests/test_orchestrator.py` — Tests with mocked SDK calls
**Deliverable:** Running `multi-claud plan "Build a web app that..."` launches an orchestrator Claude session that analyzes the project and generates a build plan with packets written to the state file.
**Verification:**
- `python -m pytest tests/test_orchestrator.py -v` — all tests pass
- `multi-claud plan "description"` generates packets visible in `multi-claud status`
- Packets have correct dependency ordering
- Dashboard shows the generated packets

---

### Packet 5: Worker Manager
**Status:** Complete
**Scope:** Launch Claude Code sessions in headless mode with git worktrees. Monitor their progress. Report status back to the state file.
**Depends on:** Packet 1, Packet 2
**Can be built in parallel with:** Packet 4
**Files to create:**
- `multi_claud/worker.py` — Worker launcher, monitor, lifecycle management
- `templates/worker-prompt.md` — System prompt for worker sessions (includes documentation requirements)
- `tests/test_worker.py` — Tests with mocked subprocess calls
**Deliverable:** Running `multi-claud start` launches up to 4 Claude Code sessions, each in its own git worktree, working on assigned packets. Workers report progress to the state file. `multi-claud stop` cleanly terminates all workers.
**Verification:**
- `python -m pytest tests/test_worker.py -v` — all tests pass
- Workers appear in `multi-claud status` with correct statuses
- Each worker runs in a separate worktree (verify with `git worktree list`)
- Killing `multi-claud stop` cleans up all worktrees and processes

---

### Packet 6: Risk Detection Engine
**Status:** Not Started
**Scope:** Build the risk detection system that monitors for file conflicts, duplicate effort, unwired code, dependency violations, and stale workers.
**Depends on:** Packet 1, Packet 5
**Files to create:**
- `multi_claud/risk.py` — Risk detection logic, file comparison across worktrees, staleness checks
- `tests/test_risk.py` — Tests for each risk type
**Deliverable:** The risk detector runs continuously while workers are active. It writes risk alerts to the state file. Critical risks pause the affected worker and notify the user via the dashboard.
**Verification:**
- `python -m pytest tests/test_risk.py -v` — all tests pass
- Simulate a file conflict (two worktrees editing same file) and verify a risk is detected
- Verify stale worker detection fires after configured timeout
- Risks appear as badges on dashboard cards

---

### Packet 7: Integration & Orchestration Loop
**Status:** Not Started
**Scope:** Wire everything together into the main orchestration loop. Orchestrator assigns packets to workers, workers execute, risk detector monitors, dashboard updates. Add the `merge` command for bringing worktree work back to main.
**Depends on:** Packets 1, 2, 3, 4, 5, 6
**Files to modify:**
- `multi_claud/cli.py` — Wire remaining commands (plan, start, stop, merge)
- `multi_claud/orchestrator.py` — Add packet assignment and re-planning logic
- `multi_claud/worker.py` — Add documentation enforcement checks
- `multi_claud/server.py` — Add risk alert UI, worker detail views
- `dashboard/index.html` — Add conflict resolution UI, worker logs panel
**Deliverable:** Full end-to-end workflow: `multi-claud plan` → `multi-claud start` → workers build → risks detected → dashboard shows everything → `multi-claud merge` brings work back. Documentation is enforced (workers can't mark complete without logging their work).
**Verification:**
- Run full workflow on a test project
- Verify all packets complete and merge cleanly
- Verify documentation was created by each worker
- Verify risk detection catches intentional conflicts in test scenario

---

### Packet 8: Polish & Distribution
**Status:** Not Started
**Scope:** Final polish — comprehensive README, error handling hardening, edge case fixes, GitHub Actions CI.
**Depends on:** Packet 7
**Files to create/modify:**
- `README.md` — Complete usage guide with screenshots
- `.github/workflows/ci.yml` — GitHub Actions CI (lint, test)
- `LICENSE` — MIT license
- All modules — Error handling review, edge case hardening
**Deliverable:** The tool is pip-installable, well-documented, and has CI that runs on every push. README has clear installation and usage instructions for Claude Code users.
**Verification:**
- `pip install multi-claud` works from a clean environment
- `python -m pytest tests/ -v` — all tests pass
- GitHub Actions CI passes
- README covers: installation, quickstart, all commands, configuration, troubleshooting

---

## Dependency Graph

```
Packet 1 (State)
├── Packet 2 (CLI)        ─┐
├── Packet 3 (Dashboard)   │── can run in parallel
│                          ─┘
├── Packet 4 (Orchestrator) ─┐
├── Packet 5 (Worker)       │── can run in parallel (both need 1+2)
│   │                      ─┘
│   └── Packet 6 (Risk)      ── needs 1+5
│
└── Packet 7 (Integration)   ── needs 1-6
    └── Packet 8 (Polish)    ── needs 7
```

## Parallelization Opportunities

- **Round 1:** Packet 1 (solo — foundation)
- **Round 2:** Packets 2 + 3 (parallel — CLI and Dashboard)
- **Round 3:** Packets 4 + 5 (parallel — Orchestrator and Worker)
- **Round 4:** Packet 6 (solo — Risk, needs Worker from Round 3)
- **Round 5:** Packet 7 (solo — Integration)
- **Round 6:** Packet 8 (solo — Polish)
