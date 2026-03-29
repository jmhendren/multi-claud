# Multi-Claud

Orchestrate multiple Claude Code sessions working on the same project — with a kanban dashboard, risk detection, and documentation enforcement.

## What Is This?

Multi-Claud is a CLI tool that lets you run up to 4 Claude Code sessions in parallel on the same project. An orchestrator session creates a build plan, assigns work packets to workers, and monitors everything through a web-based kanban dashboard.

### Key Features

- **Orchestrator** — A Claude session that reads your project, creates a build plan broken into packets, and assigns them to workers
- **Parallel Workers** — Up to 4 Claude Code sessions running simultaneously, each in an isolated git worktree
- **Kanban Dashboard** — A real-time web dashboard showing packet status, worker assignments, and risk alerts
- **Risk Detection** — Actively monitors for file conflicts, duplicate effort, unwired code, dependency violations, and stale workers
- **Documentation Enforcement** — Every worker session is required to document what it built
- **Dependency Tracking** — Packets have explicit dependencies; blocked work waits until prerequisites complete

## Installation

```bash
pip install multi-claud
```

**Requirements:**
- Python 3.10+
- [Claude Code CLI](https://code.claude.com) installed and authenticated
- Git (target projects must be git repositories)
- An Anthropic API key (for the orchestrator)

## Quick Start

```bash
# 1. Go to your project
cd /path/to/your/project

# 2. Initialize Multi-Claud
multi-claud init

# 3. Create a build plan (Claude generates this)
multi-claud plan "Build a web app that tracks restaurant health inspections"

# 4. Open the dashboard
multi-claud dashboard

# 5. Start workers
multi-claud start

# 6. Watch progress on the dashboard, intervene when asked

# 7. When all packets are done, merge everything back
multi-claud merge
```

## Commands

| Command | What It Does |
|---------|-------------|
| `multi-claud init` | Initialize Multi-Claud in your project (creates `.multi-claud/`) |
| `multi-claud plan "<description>"` | Generate a build plan with packets |
| `multi-claud start` | Launch worker sessions for ready packets |
| `multi-claud status` | Show current state in the terminal |
| `multi-claud dashboard` | Open the web-based kanban dashboard |
| `multi-claud stop` | Stop all running workers |
| `multi-claud scan` | Run a risk scan and report findings |
| `multi-claud merge` | Merge completed worktree branches back to main |

## How It Works

1. **You describe what to build** → The orchestrator (a Claude Opus session) analyzes your project and creates a build plan with ordered, dependency-aware packets.

2. **Workers pick up packets** → Up to 4 Claude Code sessions launch in isolated git worktrees. Each gets a specific packet assignment with clear scope.

3. **Risks are monitored** → While workers build, Multi-Claud watches for problems:
   - Two workers editing the same file
   - Duplicate effort across packets
   - Code built but not wired in
   - Workers stuck or unresponsive

4. **You see everything** → The kanban dashboard shows real-time progress. When a risk needs your input, it pauses and asks.

5. **Work merges back** → When packets complete, their worktree branches merge back to your main branch.

## Configuration

Multi-Claud stores its state in `.multi-claud/` inside your project directory. Configuration lives in `.multi-claud/state.json`.

| Setting | Default | Description |
|---------|---------|-------------|
| `max_workers` | 4 | Maximum parallel Claude Code sessions |
| `dashboard_port` | 8420 | Port for the web dashboard |
| `model` | `claude-opus-4-6` | Model for the orchestrator |
| `worker_model` | `claude-sonnet-4-6` | Model for worker sessions |
| `stale_timeout_minutes` | 30 | Minutes before a silent worker is flagged |

## Development

```bash
# Clone and install in dev mode
git clone https://github.com/johnhendren/multi-claud.git
cd multi-claud
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v
```

## Status

This project is in active development. See `BUILD_PLAN.md` for the current build plan and progress.

## License

MIT
