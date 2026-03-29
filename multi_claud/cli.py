"""CLI interface for Multi-Claud."""

from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from multi_claud.state import (
    PacketStatus,
    StateManager,
    WorkerStatus,
)

app = typer.Typer(
    name="multi-claud",
    help="Orchestrate multiple Claude Code sessions on the same project.",
    no_args_is_help=True,
)
console = Console()


def _get_sm(project_dir: Path | None = None) -> StateManager:
    """Get a StateManager for the given or current directory."""
    path = project_dir or Path.cwd()
    return StateManager(path)


def _require_init(sm: StateManager) -> None:
    """Exit with error if Multi-Claud hasn't been initialized."""
    if not sm.exists():
        console.print(
            "[red]Error:[/red] Multi-Claud is not initialized in this directory.\n"
            "Run [bold]multi-claud init[/bold] first."
        )
        raise typer.Exit(1)


@app.command()
def init(
    name: str = typer.Option(None, "--name", "-n", help="Project name (defaults to directory name)"),
    description: str = typer.Option("", "--description", "-d", help="Project description"),
    project_dir: Path = typer.Option(None, "--dir", help="Project directory (defaults to current)"),
) -> None:
    """Initialize Multi-Claud in a project directory."""
    path = project_dir or Path.cwd()
    sm = StateManager(path)

    if sm.exists():
        console.print(f"[yellow]Multi-Claud is already initialized in {path}[/yellow]")
        raise typer.Exit(1)

    # Check for git repo
    git_dir = path / ".git"
    if not git_dir.exists():
        console.print(
            "[red]Error:[/red] This directory is not a git repository.\n"
            "Multi-Claud requires git for worktree isolation. Run [bold]git init[/bold] first."
        )
        raise typer.Exit(1)

    project_name = name or path.name
    state = sm.init(project_name, description)
    console.print(f"[green]Initialized Multi-Claud[/green] for [bold]{state.project.name}[/bold]")
    console.print(f"  State file: {sm.state_file}")
    console.print(f"  Run [bold]multi-claud plan \"description\"[/bold] to create a build plan.")


@app.command()
def status(
    project_dir: Path = typer.Option(None, "--dir", help="Project directory"),
) -> None:
    """Show the current state of the project."""
    sm = _get_sm(project_dir)
    _require_init(sm)
    state = sm.load()

    # Project header
    console.print(f"\n[bold]{state.project.name}[/bold]")
    if state.project.description:
        console.print(f"  {state.project.description}")
    console.print()

    # Packets table
    if state.packets:
        table = Table(title="Packets", show_lines=True)
        table.add_column("ID", style="dim", width=10)
        table.add_column("Name", style="bold")
        table.add_column("Status")
        table.add_column("Worker", style="dim")
        table.add_column("Depends On", style="dim")

        status_colors = {
            PacketStatus.backlog: "dim",
            PacketStatus.ready: "cyan",
            PacketStatus.in_progress: "yellow",
            PacketStatus.review: "magenta",
            PacketStatus.complete: "green",
            PacketStatus.blocked: "red",
        }

        for p in state.packets:
            color = status_colors.get(p.status, "white")
            deps = ", ".join(p.depends_on) if p.depends_on else "-"
            worker = p.assigned_worker or "-"
            table.add_row(
                p.id,
                p.name,
                f"[{color}]{p.status.value}[/{color}]",
                worker,
                deps,
            )
        console.print(table)
    else:
        console.print("[dim]No packets yet. Run [bold]multi-claud plan[/bold] to create a build plan.[/dim]")

    # Workers table
    if state.workers:
        console.print()
        w_table = Table(title="Workers", show_lines=True)
        w_table.add_column("ID", style="dim", width=10)
        w_table.add_column("Name", style="bold")
        w_table.add_column("Status")
        w_table.add_column("Packet", style="dim")

        w_colors = {
            WorkerStatus.idle: "dim",
            WorkerStatus.working: "green",
            WorkerStatus.paused: "yellow",
            WorkerStatus.stopped: "dim",
            WorkerStatus.error: "red",
        }

        for w in state.workers:
            color = w_colors.get(w.status, "white")
            table_packet = w.assigned_packet or "-"
            w_table.add_row(
                w.id,
                w.name,
                f"[{color}]{w.status.value}[/{color}]",
                table_packet,
            )
        console.print(w_table)

    # Risks
    active_risks = [r for r in state.risks if not r.resolved]
    if active_risks:
        console.print()
        r_table = Table(title="Active Risks", show_lines=True)
        r_table.add_column("Type", style="bold")
        r_table.add_column("Severity")
        r_table.add_column("Description")

        severity_colors = {"info": "blue", "warning": "yellow", "critical": "red"}

        for r in active_risks:
            color = severity_colors.get(r.severity.value, "white")
            r_table.add_row(
                r.type.value,
                f"[{color}]{r.severity.value}[/{color}]",
                r.description,
            )
        console.print(r_table)

    console.print()


@app.command()
def plan(
    description: str = typer.Argument(help="Describe what to build"),
    project_dir: Path = typer.Option(None, "--dir", help="Project directory"),
) -> None:
    """Generate a build plan using the orchestrator."""
    sm = _get_sm(project_dir)
    _require_init(sm)
    console.print("[yellow]Plan command not yet implemented.[/yellow] Coming in Packet 4 (Orchestrator).")


@app.command()
def start(
    project_dir: Path = typer.Option(None, "--dir", help="Project directory"),
    workers: int = typer.Option(None, "--workers", "-w", help="Number of workers to launch"),
) -> None:
    """Launch worker sessions for ready packets."""
    sm = _get_sm(project_dir)
    _require_init(sm)
    console.print("[yellow]Start command not yet implemented.[/yellow] Coming in Packet 5 (Worker Manager).")


@app.command()
def stop(
    project_dir: Path = typer.Option(None, "--dir", help="Project directory"),
) -> None:
    """Stop all running workers."""
    sm = _get_sm(project_dir)
    _require_init(sm)
    console.print("[yellow]Stop command not yet implemented.[/yellow] Coming in Packet 5 (Worker Manager).")


@app.command()
def dashboard(
    project_dir: Path = typer.Option(None, "--dir", help="Project directory"),
    port: int = typer.Option(None, "--port", "-p", help="Dashboard port"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open browser"),
) -> None:
    """Open the web-based kanban dashboard."""
    sm = _get_sm(project_dir)
    _require_init(sm)
    state = sm.load()

    host = state.config.dashboard_host
    dashboard_port = port or state.config.dashboard_port

    console.print(f"[green]Starting dashboard[/green] at http://{host}:{dashboard_port}")

    if not no_open:
        webbrowser.open(f"http://{host}:{dashboard_port}")

    # Import here to avoid circular imports and slow startup for other commands
    from multi_claud.server import create_app

    import uvicorn
    uvicorn.run(
        create_app(sm),
        host=host,
        port=dashboard_port,
        log_level="warning",
    )


@app.command()
def merge(
    project_dir: Path = typer.Option(None, "--dir", help="Project directory"),
) -> None:
    """Merge completed worktree branches back to main."""
    sm = _get_sm(project_dir)
    _require_init(sm)
    console.print("[yellow]Merge command not yet implemented.[/yellow] Coming in Packet 7 (Integration).")


if __name__ == "__main__":
    app()
