"""CLI interface for Multi-Claud."""

from __future__ import annotations

import os
import signal
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
    model: str = typer.Option(None, "--model", "-m", help="Override orchestrator model"),
) -> None:
    """Generate a build plan using the orchestrator."""
    import asyncio
    from multi_claud.orchestrator import create_plan

    sm = _get_sm(project_dir)
    _require_init(sm)

    console.print(f"[cyan]Creating build plan...[/cyan] This may take a minute.")
    console.print(f"  Description: {description}")

    try:
        packets = asyncio.run(create_plan(sm, description, model=model))
        console.print(f"\n[green]Created {len(packets)} packets.[/green]")
        for i, p in enumerate(packets, 1):
            console.print(f"  {i}. {p['name']}")
        console.print(f"\nRun [bold]multi-claud status[/bold] to see the full plan.")
        console.print(f"Run [bold]multi-claud dashboard[/bold] to see it visually.")
    except Exception as e:
        console.print(f"[red]Error creating plan:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def start(
    project_dir: Path = typer.Option(None, "--dir", help="Project directory"),
    workers: int = typer.Option(None, "--workers", "-w", help="Number of workers to launch"),
    auto: bool = typer.Option(False, "--auto", "-a", help="Auto-continue: launch next round when current finishes"),
) -> None:
    """Launch worker sessions for ready packets."""
    import asyncio
    from multi_claud.worker import WorkerManager

    sm = _get_sm(project_dir)
    _require_init(sm)

    state = sm.load()
    ready = sm.get_ready_packets()
    if not ready:
        console.print("[yellow]No packets are ready to work on.[/yellow]")
        console.print("Run [bold]multi-claud plan[/bold] to create a build plan first.")
        raise typer.Exit(0)

    max_w = workers or state.config.max_workers

    if auto:
        console.print(f"[cyan]Auto-continue mode:[/cyan] launching up to {max_w} workers per round")
        console.print("[dim]Will automatically start next round when packets become ready.[/dim]")
        console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

        async def run_auto():
            wm = WorkerManager(sm)
            try:
                await wm.run_auto(max_workers=max_w)
                console.print("\n[green]All work complete.[/green]")
            except KeyboardInterrupt:
                console.print("\n[yellow]Stopping workers...[/yellow]")
                await wm.stop_all()

        try:
            asyncio.run(run_auto())
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
    else:
        console.print(f"[cyan]Launching up to {max_w} workers...[/cyan]")

        async def run():
            wm = WorkerManager(sm)
            launched = await wm.launch_available(max_workers=max_w)
            if launched:
                console.print(f"[green]Launched {len(launched)} worker(s).[/green]")
                for wid in launched:
                    w = sm.get_worker(wid)
                    if w:
                        console.print(f"  {w.name} → packet {w.assigned_packet}")
                console.print("\n[dim]Workers running. Use [bold]multi-claud status[/bold] to check progress.[/dim]")
                console.print("[dim]Tip: use [bold]multi-claud start --auto[/bold] to auto-continue to next rounds.[/dim]")

                try:
                    await asyncio.gather(*[
                        wp.wait() for wp in wm.workers.values() if wp.is_running
                    ])
                    console.print("\n[green]All workers finished.[/green]")
                except KeyboardInterrupt:
                    console.print("\n[yellow]Stopping workers...[/yellow]")
                    await wm.stop_all()
            else:
                console.print("[yellow]No workers launched.[/yellow] All slots may be full or no packets ready.")

        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")


@app.command()
def stop(
    project_dir: Path = typer.Option(None, "--dir", help="Project directory"),
) -> None:
    """Stop all running workers."""
    sm = _get_sm(project_dir)
    _require_init(sm)

    state = sm.load()
    active = [w for w in state.workers if w.status.value == "working"]
    if not active:
        console.print("[dim]No active workers to stop.[/dim]")
        return

    # Kill processes by PID
    import os
    stopped = 0
    for w in active:
        if w.pid:
            try:
                os.kill(w.pid, signal.SIGTERM)
                stopped += 1
            except ProcessLookupError:
                pass  # Already dead
        sm.update_worker(w.id, status=WorkerStatus.stopped)
        if w.assigned_packet:
            pkt = sm.get_packet(w.assigned_packet)
            if pkt and pkt.status == PacketStatus.in_progress:
                sm.update_packet(pkt.id, status=PacketStatus.ready)

    console.print(f"[green]Stopped {stopped} worker(s).[/green]")


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

    state = sm.load()
    completed = [w for w in state.workers if w.worktree_branch and w.status != WorkerStatus.working]

    if not completed:
        console.print("[dim]No completed worktree branches to merge.[/dim]")
        return

    project_path = project_dir or Path.cwd()
    merged = 0
    for w in completed:
        if not w.worktree_branch:
            continue
        console.print(f"  Merging branch [bold]{w.worktree_branch}[/bold] from {w.name}...")
        try:
            result = subprocess.run(
                ["git", "merge", w.worktree_branch, "--no-edit"],
                cwd=str(project_path),
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                console.print(f"    [green]Merged successfully.[/green]")
                merged += 1
            else:
                console.print(f"    [red]Merge conflict.[/red] Resolve manually:")
                console.print(f"    {result.stderr.strip()}")
        except FileNotFoundError:
            console.print("    [red]Git not found.[/red]")

    console.print(f"\n[green]Merged {merged}/{len(completed)} branches.[/green]")


@app.command()
def scan(
    project_dir: Path = typer.Option(None, "--dir", help="Project directory"),
) -> None:
    """Run a risk scan across all active workers."""
    from multi_claud.risk import RiskDetector

    sm = _get_sm(project_dir)
    _require_init(sm)

    rd = RiskDetector(sm)
    risks = rd.scan_all()

    if risks:
        console.print(f"\n[yellow]Found {len(risks)} new risk(s):[/yellow]")
        for r in risks:
            severity_colors = {"info": "blue", "warning": "yellow", "critical": "red"}
            color = severity_colors.get(r.severity.value, "white")
            console.print(f"  [{color}]{r.severity.value.upper()}[/{color}] {r.description}")
    else:
        console.print("[green]No new risks detected.[/green]")


if __name__ == "__main__":
    app()
