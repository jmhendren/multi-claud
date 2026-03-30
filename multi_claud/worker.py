"""Worker manager: launches and monitors Claude Code sessions.

Includes:
- Live streaming of worker activity via stream-json
- Automatic reaper for dead worker processes
- Auto-retry on failure (up to max_retries per packet)
- Self-healing orchestration loop
- CLI dry-run validation before launching workers
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
from datetime import datetime, timezone
from pathlib import Path

from multi_claud.state import (
    PacketStatus,
    StateManager,
    WorkerStatus,
)

logger = logging.getLogger(__name__)

WORKER_PROMPT_PATH = Path(__file__).parent.parent / "templates" / "worker-prompt.md"

MAX_RETRIES_DEFAULT = 2


def _build_worker_prompt(packet_name: str, packet_description: str,
                         project_path: Path) -> str:
    """Build the prompt sent to a worker Claude Code session."""
    template = WORKER_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = template.replace("{packet_name}", packet_name)
    prompt = prompt.replace("{packet_description}", packet_description)

    return f"""{prompt}

## Project Path
{project_path}

Now begin working on your assigned packet. Stay in scope, document everything,
and report what files you create or modify."""


def validate_cli() -> str | None:
    """Validate that the claude CLI is available and working.

    Returns None on success, or an error message string.
    """
    claude_path = shutil.which("claude")
    if not claude_path:
        return "Claude Code CLI not found. Install it from https://code.claude.com"

    # Dry-run: test that the flags we use actually work
    try:
        result = asyncio.get_event_loop().run_until_complete(
            _dry_run_cli()
        )
        return result
    except Exception:
        # If we can't get an event loop, try sync
        try:
            import subprocess
            proc = subprocess.run(
                ["claude", "-p", "Say OK", "--output-format", "stream-json", "--verbose",
                 "--dangerously-skip-permissions"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                return f"Claude CLI test failed: {proc.stderr[:200]}"
        except Exception as e:
            return f"Claude CLI test failed: {e}"

    return None


async def _dry_run_cli() -> str | None:
    """Async dry-run of the claude CLI to verify flags work."""
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", "Say OK", "--output-format", "stream-json", "--verbose",
        "--dangerously-skip-permissions",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        return f"Claude CLI test failed: {stderr.decode()[:200]}"
    return None


class WorkerProcess:
    """Manages a single Claude Code worker subprocess."""

    def __init__(self, worker_id: str, name: str, process: asyncio.subprocess.Process,
                 worktree_branch: str, packet_id: str):
        self.worker_id = worker_id
        self.name = name
        self.process = process
        self.worktree_branch = worktree_branch
        self.packet_id = packet_id
        self.output_lines: list[str] = []

    @property
    def pid(self) -> int | None:
        return self.process.pid

    @property
    def is_running(self) -> bool:
        return self.process.returncode is None

    async def wait(self) -> int:
        """Wait for the process to complete. Returns exit code."""
        return await self.process.wait()

    def terminate(self) -> None:
        """Send SIGTERM to the worker process."""
        if self.is_running:
            self.process.terminate()

    async def kill(self) -> None:
        """Force kill the worker process."""
        if self.is_running:
            self.process.kill()
            await self.process.wait()


class WorkerManager:
    """Manages multiple Claude Code worker sessions with self-healing capabilities."""

    def __init__(self, sm: StateManager):
        self.sm = sm
        self.workers: dict[str, WorkerProcess] = {}

    async def launch_worker(self, packet_id: str) -> str:
        """Launch a Claude Code worker for a specific packet. Returns the worker ID."""
        state = self.sm.load()
        packet = next((p for p in state.packets if p.id == packet_id), None)
        if not packet:
            raise ValueError(f"Packet '{packet_id}' not found")

        # Create worker in state
        worker_num = len(state.workers) + 1
        worker = self.sm.add_worker(f"Worker {worker_num}")

        # Assign packet to worker
        self.sm.assign_packet_to_worker(packet_id, worker.id)

        # Build the prompt
        prompt = _build_worker_prompt(
            packet.name,
            packet.description,
            self.sm.project_path,
        )

        branch_name = f"mc-worker-{worker.id}-{packet_id[:6]}"

        # Launch Claude Code in headless mode with streaming output
        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--model", state.config.worker_model,
            "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep",
            "--dangerously-skip-permissions",
        ]

        logger.info("Launching worker %s for packet '%s' (attempt %d)",
                     worker.id, packet.name, packet.retry_count + 1)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.sm.project_path),
        )

        wp = WorkerProcess(
            worker_id=worker.id,
            name=worker.name,
            process=process,
            worktree_branch=branch_name,
            packet_id=packet_id,
        )
        self.workers[worker.id] = wp

        self.sm.update_worker(
            worker.id,
            pid=process.pid,
            worktree_branch=branch_name,
        )

        # Start monitoring in the background
        asyncio.create_task(self._monitor_worker(wp, packet_id))

        return worker.id

    async def _monitor_worker(self, wp: WorkerProcess, packet_id: str) -> None:
        """Monitor a worker's stream-json output and update state live."""
        last_result_text = ""
        try:
            while wp.process.stdout and not wp.process.stdout.at_eof():
                line = await wp.process.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue

                wp.output_lines.append(decoded)

                try:
                    data = json.loads(decoded)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "")

                if msg_type == "assistant":
                    msg = data.get("message", {})
                    content = msg.get("content", [])
                    for block in content:
                        if block.get("type") == "tool_use":
                            tool_name = block.get("name", "")
                            tool_input = block.get("input", {})
                            activity = self._describe_tool_use(tool_name, tool_input)
                            self.sm.update_worker(wp.worker_id, session_log=[activity])

                            if tool_name in ("Write", "Edit"):
                                fpath = tool_input.get("file_path", "")
                                if fpath:
                                    self._record_file_touch(packet_id, fpath)

                        elif block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                last_result_text = text

                if msg_type == "result":
                    result_text = data.get("result", "")
                    if result_text:
                        last_result_text = result_text

            # Read remaining stderr
            stderr_text = ""
            if wp.process.stderr:
                stderr_data = await wp.process.stderr.read()
                stderr_text = stderr_data.decode("utf-8", errors="replace").strip()

            exit_code = await wp.wait()

            if last_result_text:
                self.sm.update_packet(packet_id, documentation=last_result_text[:3000])

            if exit_code == 0:
                self._handle_success(wp, packet_id, last_result_text)
            else:
                error_msg = stderr_text[:500] if stderr_text else f"Exit code {exit_code}"
                self._handle_failure(wp, packet_id, error_msg)

        except Exception as e:
            logger.error("Exception monitoring worker %s: %s", wp.worker_id, e)
            self._handle_failure(wp, packet_id, str(e))

    def _handle_success(self, wp: WorkerProcess, packet_id: str, result_text: str) -> None:
        """Handle a successful worker completion."""
        packet = self.sm.get_packet(packet_id)
        packet_name = packet.name if packet else packet_id

        # Write to build log
        self._write_build_log(packet_name, wp, result_text)

        # Mark complete — triggers _unblock_dependents
        self.sm.update_packet(packet_id, status=PacketStatus.complete)

        # Clean up worker from state
        self.sm.remove_worker(wp.worker_id)

        # Remove from local tracking
        self.workers.pop(wp.worker_id, None)

        logger.info("Worker %s completed '%s' — dependents unblocked", wp.worker_id, packet_name)

    def _handle_failure(self, wp: WorkerProcess, packet_id: str, error_msg: str) -> None:
        """Handle a worker failure with auto-retry logic."""
        packet = self.sm.get_packet(packet_id)
        packet_name = packet.name if packet else packet_id
        retry_count = packet.retry_count if packet else 0
        max_retries = packet.max_retries if packet else MAX_RETRIES_DEFAULT

        if retry_count < max_retries:
            # Retry: reset packet to ready, increment retry counter
            logger.warning("Worker %s failed on '%s' (attempt %d/%d) — will retry: %s",
                           wp.worker_id, packet_name, retry_count + 1, max_retries, error_msg)
            self.sm.update_packet(
                packet_id,
                status=PacketStatus.ready,
                assigned_worker=None,
                retry_count=retry_count + 1,
                last_error=error_msg[:500],
            )
        else:
            # Max retries exceeded — mark as error status
            logger.error("Worker %s failed on '%s' after %d attempts — giving up: %s",
                         wp.worker_id, packet_name, max_retries, error_msg)
            self.sm.update_packet(
                packet_id,
                status=PacketStatus.blocked,
                assigned_worker=None,
                last_error=f"Failed after {max_retries} attempts: {error_msg[:400]}",
            )

        # Clean up worker
        try:
            self.sm.remove_worker(wp.worker_id)
        except Exception:
            pass
        self.workers.pop(wp.worker_id, None)

    def _write_build_log(self, packet_name: str, wp: WorkerProcess, result_text: str) -> None:
        """Append a completed packet's work summary to the project's build log."""
        build_log = self.sm.project_path / "docs" / "build-log.md"
        build_log.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        packet = None
        state = self.sm.load()
        for p in state.packets:
            if p.assigned_worker == wp.worker_id:
                packet = p
                break

        files_list = ""
        if packet and packet.files_touched:
            files_list = "\n".join(f"- `{f}`" for f in packet.files_touched)
        else:
            files_list = "- (no files tracked)"

        entry = f"""
---

## {packet_name} — {now}
**Worker:** {wp.name}

### Files Modified
{files_list}

### Work Summary
{result_text[:2000] if result_text else "(No summary provided)"}
"""

        if build_log.exists():
            existing = build_log.read_text(encoding="utf-8")
        else:
            existing = "# Build Log\n\nAutomatically generated by Multi-Claud.\n"

        build_log.write_text(existing + entry, encoding="utf-8")
        logger.info("Build log updated: %s", build_log)

    @staticmethod
    def _describe_tool_use(tool_name: str, tool_input: dict) -> str:
        """Create a short human-readable description of a tool call."""
        if tool_name == "Read":
            path = tool_input.get("file_path", "?")
            return f"Reading {path.split('/')[-1]}"
        elif tool_name == "Write":
            path = tool_input.get("file_path", "?")
            return f"Writing {path.split('/')[-1]}"
        elif tool_name == "Edit":
            path = tool_input.get("file_path", "?")
            return f"Editing {path.split('/')[-1]}"
        elif tool_name == "Bash":
            cmd = tool_input.get("command", "?")
            desc = tool_input.get("description", "")
            return f"Running: {desc or cmd[:60]}"
        elif tool_name == "Glob":
            pattern = tool_input.get("pattern", "?")
            return f"Searching: {pattern}"
        elif tool_name == "Grep":
            pattern = tool_input.get("pattern", "?")
            return f"Searching for: {pattern}"
        elif tool_name == "WebSearch":
            query = tool_input.get("query", "?")
            return f"Searching web: {query[:50]}"
        else:
            return f"Using {tool_name}"

    def _record_file_touch(self, packet_id: str, file_path: str) -> None:
        """Record that a packet touched a file."""
        packet = self.sm.get_packet(packet_id)
        if packet and file_path not in packet.files_touched:
            files = list(packet.files_touched) + [file_path]
            self.sm.update_packet(packet_id, files_touched=files)

    # --- Reaper: detect and clean up dead workers ---

    def reap_dead_workers(self) -> list[str]:
        """Check all workers in state for dead PIDs and clean them up.

        Returns list of packet IDs that were freed up for retry.
        """
        state = self.sm.load()
        freed_packets = []

        for worker in list(state.workers):
            if worker.status != WorkerStatus.working:
                continue
            if not worker.pid:
                continue

            # Check if PID is alive
            try:
                os.kill(worker.pid, 0)
            except (ProcessLookupError, PermissionError):
                # Process is dead — clean up
                logger.warning("Reaper: Worker %s (PID %d) is dead", worker.name, worker.pid)

                if worker.assigned_packet:
                    packet = next((p for p in state.packets if p.id == worker.assigned_packet), None)
                    if packet and packet.status == PacketStatus.in_progress:
                        freed_packets.append(packet.id)

                # Use the state manager to handle cleanup properly
                self._handle_failure(
                    _GhostWorker(worker.id, worker.name, worker.assigned_packet),
                    worker.assigned_packet or "",
                    "Worker process died unexpectedly",
                )

        return freed_packets

    # --- Launch helpers ---

    async def launch_available(self, max_workers: int | None = None) -> list[str]:
        """Launch workers for all available ready packets up to max_workers."""
        state = self.sm.load()
        limit = max_workers or state.config.max_workers

        # Count currently active workers (both local and in state)
        active_local = len([w for w in self.workers.values() if w.is_running])
        active_state = len([w for w in state.workers if w.status == WorkerStatus.working])
        active_count = max(active_local, active_state)
        slots = limit - active_count

        if slots <= 0:
            return []

        # Get ready packets not already assigned
        ready = self.sm.get_ready_packets()
        assigned_ids = {w.assigned_packet for w in state.workers
                        if w.status == WorkerStatus.working and w.assigned_packet}
        unassigned = [p for p in ready if p.id not in assigned_ids]

        launched = []
        for packet in unassigned[:slots]:
            try:
                worker_id = await self.launch_worker(packet.id)
                launched.append(worker_id)
                logger.info("Launched worker for '%s'%s",
                            packet.name,
                            f" (retry {packet.retry_count})" if packet.retry_count > 0 else "")
            except Exception as e:
                logger.error("Failed to launch worker for packet %s: %s", packet.id, e)

        return launched

    # --- Self-healing auto-continue loop ---

    async def run_auto(self, max_workers: int | None = None, poll_interval: int = 5) -> None:
        """Self-healing auto-continue mode.

        Continuously:
        1. Reaps dead workers and resets their packets for retry
        2. Launches workers for any ready packets
        3. Waits and repeats
        4. Stops when all packets are complete or permanently stuck
        """
        state = self.sm.load()
        limit = max_workers or state.config.max_workers
        total_packets = len(state.packets)

        logger.info("Auto mode: %d packets, up to %d workers, %d max retries",
                     total_packets, limit, MAX_RETRIES_DEFAULT)

        stuck_cycles = 0
        max_stuck_cycles = 12  # 60 seconds of no progress before declaring stuck

        while True:
            # 1. Reap dead workers
            freed = self.reap_dead_workers()
            if freed:
                logger.info("Reaper freed %d packet(s) for retry", len(freed))
                stuck_cycles = 0

            # 2. Launch available work
            launched = await self.launch_available(max_workers=limit)
            if launched:
                logger.info("Launched %d new worker(s)", len(launched))
                stuck_cycles = 0

            # 3. Check overall progress
            state = self.sm.load()
            complete = [p for p in state.packets if p.status == PacketStatus.complete]
            in_prog = [p for p in state.packets if p.status == PacketStatus.in_progress]
            ready = [p for p in state.packets if p.status == PacketStatus.ready]
            blocked = [p for p in state.packets if p.status == PacketStatus.blocked]

            running_local = [wp for wp in self.workers.values() if wp.is_running]

            # All done?
            if len(complete) == total_packets:
                logger.info("All %d packets complete!", total_packets)
                break

            # Nothing running and nothing to launch?
            if not running_local and not ready:
                if blocked and not in_prog:
                    stuck_cycles += 1
                    if stuck_cycles >= max_stuck_cycles:
                        failed = [p for p in blocked if p.last_error]
                        logger.warning(
                            "Stuck: %d blocked packets, no work in progress. "
                            "%d packets failed permanently.",
                            len(blocked), len(failed)
                        )
                        break
                elif not blocked and not in_prog:
                    logger.info("No more work to do. %d/%d complete.", len(complete), total_packets)
                    break

            # Wait before next cycle
            await asyncio.sleep(poll_interval)

        # Final summary
        state = self.sm.load()
        complete = len([p for p in state.packets if p.status == PacketStatus.complete])
        failed = [p for p in state.packets if p.last_error and p.status != PacketStatus.complete]
        logger.info("Auto mode finished: %d/%d complete, %d failed", complete, total_packets, len(failed))
        for p in failed:
            logger.info("  FAILED: %s — %s", p.name, p.last_error[:100])

    async def stop_all(self) -> None:
        """Stop all running workers."""
        for worker_id, wp in list(self.workers.items()):
            if wp.is_running:
                logger.info("Stopping worker %s", worker_id)
                wp.terminate()
                try:
                    await asyncio.wait_for(wp.wait(), timeout=10)
                except asyncio.TimeoutError:
                    await wp.kill()

                self.sm.update_worker(worker_id, status=WorkerStatus.stopped)
                worker_data = self.sm.get_worker(worker_id)
                if worker_data and worker_data.assigned_packet:
                    pkt = self.sm.get_packet(worker_data.assigned_packet)
                    if pkt and pkt.status == PacketStatus.in_progress:
                        self.sm.update_packet(pkt.id, status=PacketStatus.ready, assigned_worker=None)

    def get_status(self) -> list[dict]:
        """Get status of all managed workers."""
        return [
            {
                "id": worker_id,
                "name": wp.name,
                "pid": wp.pid,
                "running": wp.is_running,
                "branch": wp.worktree_branch,
                "packet_id": wp.packet_id,
                "output_lines": len(wp.output_lines),
            }
            for worker_id, wp in self.workers.items()
        ]


class _GhostWorker:
    """Minimal stand-in for a dead WorkerProcess so _handle_failure can clean up."""

    def __init__(self, worker_id: str, name: str, packet_id: str | None):
        self.worker_id = worker_id
        self.name = name
        self.packet_id = packet_id
