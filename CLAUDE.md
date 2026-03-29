# Multi-Claud

A CLI tool that orchestrates multiple Claude Code sessions working on the same project in parallel, with a web-based kanban dashboard for real-time tracking.

## Quick Reference

- **Language:** Python 3.13
- **Framework:** FastAPI (dashboard), Typer (CLI), Claude Agent SDK (orchestrator)
- **Test:** `python -m pytest tests/ -v`
- **Run:** `multi-claud --help` (after `pip install -e ".[dev]"`)
- **Lint/Format:** `python -m py_compile multi_claud/*.py` (basic syntax check)
- **Install (dev):** `pip install -e ".[dev]"`

## Project Structure

```
Multi Claud/
├── CLAUDE.md              # This file — project instructions
├── BUILD_PLAN.md          # Build plan with packets and status
├── pyproject.toml         # Python package config
├── .claude/               # Claude Code configuration
│   ├── settings.json      # Permissions and hooks
│   ├── rules/             # Coding conventions
│   └── commands/          # Slash commands
├── multi_claud/           # Main Python package
│   ├── __init__.py
│   ├── cli.py             # Typer CLI (multi-claud command)
│   ├── orchestrator.py    # Build plan generation via Agent SDK
│   ├── worker.py          # Claude Code session launcher/monitor
│   ├── state.py           # JSON state file with file locking
│   ├── risk.py            # Risk detection engine
│   └── server.py          # FastAPI dashboard server + SSE
├── dashboard/
│   └── index.html         # Kanban board (HTML + CSS + JS, single file)
├── templates/
│   ├── orchestrator-prompt.md  # System prompt for orchestrator session
│   └── worker-prompt.md        # System prompt for worker sessions
├── tests/                 # pytest test suite
├── docs/
│   ├── architecture.json  # Living architecture doc
│   ├── architecture.html  # Visual architecture viewer
│   └── build-log.md       # Log of what was built and audited
└── README.md              # GitHub readme
```

## How We Work

### The Packet Workflow
This project is built in **packets** — scoped chunks from the build plan (`BUILD_PLAN.md`). Each packet is built in a fresh Claude Code session.

**Starting a packet:**
1. Read `BUILD_PLAN.md` to find the next pending packet
2. Read this CLAUDE.md and the architecture doc at `docs/architecture.json`
3. Build the packet
4. Run tests: `python -m pytest tests/ -v`
5. Run `/audit-packet` to review your work
6. Run `/update-arch` if the architecture changed
7. Update the packet status in `BUILD_PLAN.md` to "Complete"

### Feedback Loops
- **Tests:** `python -m pytest tests/ -v` — run after every change
- **CLI:** `multi-claud --help` — verify CLI commands work
- **Dashboard:** `multi-claud dashboard` then open `http://localhost:8420` — verify the kanban board renders
- **State file:** Check `.multi-claud/state.json` in a test project to verify state is written correctly

### Testing
- Use `pytest` with `pytest-asyncio` for async tests
- Test state management thoroughly (it's the foundation everything else depends on)
- Use `httpx` for testing FastAPI endpoints
- Mock external calls (Claude Agent SDK, Claude CLI) in unit tests
- Integration tests should use real state files in tmp directories

Run tests after building ANYTHING. If tests fail — even pre-existing failures — fix them immediately.

## Architecture

The living architecture document is at `docs/architecture.json`. Open `docs/architecture.html` in a browser to visualize it.

**CRITICAL:** If your work adds, removes, or changes any component, dependency, data flow, or structural element — update `docs/architecture.json` IMMEDIATELY.

## Secrets and Environment Variables

Secrets are stored in `.env.local` (gitignored). Claude has full access.

- `ANTHROPIC_API_KEY` — Required for the orchestrator (Claude Agent SDK)
- `MULTI_CLAUD_PORT` — Dashboard port (default: 8420)
- `MULTI_CLAUD_HOST` — Dashboard host (default: localhost)

## Documentation

- `BUILD_PLAN.md` — The build plan with all packets and their status
- `docs/architecture.json` — Living architecture document (JSON, machine-readable)
- `docs/architecture.html` — Architecture viewer (open in browser)
- `docs/build-log.md` — Running log of what was built, audited, and fixed

### Build Log Format
After completing each packet, add an entry to `docs/build-log.md`:
```markdown
## Packet [N]: [Name] — [Date]

### Built
- [What was built, with file paths]

### Audit Findings
- [What the audit found]

### Fixes
- [What was fixed and why]

### Architecture Changes
- [What changed in the architecture, or "None"]
```

## Slash Commands

- `/audit-packet` — Review your current work with ultrathink, find and fix issues
- `/update-arch` — Update the architecture document
- `/next-packet` — Get a briefing on the next packet to build
- `/run-tests` — Run the full test suite and fix any failures

## Common Pitfalls

- **File locking:** The state file uses `filelock`. Always acquire the lock before writing, release after. Never hold the lock longer than necessary.
- **Process management:** Workers are Claude Code CLI subprocesses. Always handle cleanup (kill workers on exit, remove stale worktrees).
- **SSE connections:** Browser SSE auto-reconnects, but the server must handle dropped connections gracefully.
- **Git worktrees:** Target projects must be git repos. Check for this in `init` and give a clear error if not.
- **Path handling:** Use `pathlib.Path` everywhere. Never string-concatenate paths. Some users have spaces in paths (like this project's Dropbox path).
