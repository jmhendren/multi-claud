"""Worker manager: launches and monitors Claude Code sessions in git worktrees."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from multi_claud.state import (
    PacketStatus,
    StateManager,
    WorkerStatus,
)

logger = logging.getLogger(__name__)

WORKER_PROMPT_PATH = Path(__file__).parent.parent / "templates" / "worker-prompt.md"


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


class WorkerProcess:
    """Manages a single Claude Code worker subprocess."""

    def __init__(self, worker_id: str, name: str, process: asyncio.subprocess.Process,
                 worktree_branch: str):
        self.worker_id = worker_id
        self.name = name
        self.process = process
        self.worktree_branch = worktree_branch
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
    """Manages multiple Claude Code worker sessions."""

    def __init__(self, sm: StateManager):
        self.sm = sm
        self.workers: dict[str, WorkerProcess] = {}
        self._monitoring = False

    async def launch_worker(self, packet_id: str) -> str:
        """Launch a Claude Code worker for a specific packet.

        Returns the worker ID.
        """
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

        # Generate worktree branch name
        branch_name = f"mc-worker-{worker.id}-{packet_id[:6]}"

        # Launch Claude Code in headless mode with a worktree
        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "stream-json",
            "--model", state.config.worker_model,
            "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep",
            "--dangerously-skip-permissions",
        ]

        logger.info("Launching worker %s for packet '%s'", worker.id, packet.name)

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
        )
        self.workers[worker.id] = wp

        # Update worker state with PID
        self.sm.update_worker(
            worker.id,
            pid=process.pid,
            worktree_branch=branch_name,
        )

        # Start monitoring this worker's output in the background
        asyncio.create_task(self._monitor_worker(wp, packet_id))

        return worker.id

    async def _monitor_worker(self, wp: WorkerProcess, packet_id: str) -> None:
        """Monitor a worker's output and update state when it completes."""
        try:
            # Read stdout line by line
            while wp.process.stdout and not wp.process.stdout.at_eof():
                line = await wp.process.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    wp.output_lines.append(decoded)
                    # Parse stream-json for progress updates
                    self._process_output_line(wp, decoded, packet_id)

            # Wait for process to finish
            exit_code = await wp.wait()

            # Update state based on exit code
            if exit_code == 0:
                self.sm.update_worker(wp.worker_id, status=WorkerStatus.idle)
                self.sm.update_packet(packet_id, status=PacketStatus.review)
                logger.info("Worker %s completed successfully", wp.worker_id)
            else:
                self.sm.update_worker(wp.worker_id, status=WorkerStatus.error)
                logger.error("Worker %s failed with exit code %d", wp.worker_id, exit_code)

        except Exception as e:
            logger.error("Error monitoring worker %s: %s", wp.worker_id, e)
            self.sm.update_worker(wp.worker_id, status=WorkerStatus.error)

    def _process_output_line(self, wp: WorkerProcess, line: str, packet_id: str) -> None:
        """Process a line of stream-json output from a worker."""
        try:
            data = json.loads(line)
            # Update last activity timestamp
            self.sm.update_worker(wp.worker_id, last_activity=datetime.now(timezone.utc))

            # Check for tool use that indicates file changes
            if data.get("type") == "tool_use":
                tool_name = data.get("name", "")
                if tool_name in ("Write", "Edit"):
                    file_path = data.get("input", {}).get("file_path", "")
                    if file_path:
                        self._record_file_touch(packet_id, file_path)

        except (json.JSONDecodeError, KeyError):
            pass  # Not all lines are JSON

    def _record_file_touch(self, packet_id: str, file_path: str) -> None:
        """Record that a packet touched a file."""
        packet = self.sm.get_packet(packet_id)
        if packet and file_path not in packet.files_touched:
            files = list(packet.files_touched) + [file_path]
            self.sm.update_packet(packet_id, files_touched=files)

    async def launch_available(self, max_workers: int | None = None) -> list[str]:
        """Launch workers for all available ready packets up to max_workers.

        Returns list of launched worker IDs.
        """
        state = self.sm.load()
        limit = max_workers or state.config.max_workers

        # Count currently active workers
        active_count = len([w for w in self.workers.values() if w.is_running])
        slots = limit - active_count

        if slots <= 0:
            logger.info("All worker slots full (%d/%d)", active_count, limit)
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
            except Exception as e:
                logger.error("Failed to launch worker for packet %s: %s", packet.id, e)

        return launched

    async def stop_all(self) -> None:
        """Stop all running workers."""
        for worker_id, wp in list(self.workers.items()):
            if wp.is_running:
                logger.info("Stopping worker %s", worker_id)
                wp.terminate()
                try:
                    await asyncio.wait_for(wp.wait(), timeout=10)
                except asyncio.TimeoutError:
                    logger.warning("Worker %s didn't stop gracefully, killing", worker_id)
                    await wp.kill()

                self.sm.update_worker(worker_id, status=WorkerStatus.stopped)

                # Reset packet to ready if it was in progress
                packet_id = self.sm.get_worker(worker_id)
                if packet_id:
                    worker_data = self.sm.get_worker(worker_id)
                    if worker_data and worker_data.assigned_packet:
                        pkt = self.sm.get_packet(worker_data.assigned_packet)
                        if pkt and pkt.status == PacketStatus.in_progress:
                            self.sm.update_packet(pkt.id, status=PacketStatus.ready)

    def get_status(self) -> list[dict]:
        """Get status of all managed workers."""
        result = []
        for worker_id, wp in self.workers.items():
            result.append({
                "id": worker_id,
                "name": wp.name,
                "pid": wp.pid,
                "running": wp.is_running,
                "branch": wp.worktree_branch,
                "output_lines": len(wp.output_lines),
            })
        return result
