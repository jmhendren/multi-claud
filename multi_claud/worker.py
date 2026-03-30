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
        """Monitor a worker's stream-json output and update state live."""
        last_result_text = ""
        try:
            # Read stdout line by line (stream-json is newline-delimited JSON)
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

                # Track live activity for dashboard
                if msg_type == "assistant":
                    msg = data.get("message", {})
                    content = msg.get("content", [])
                    for block in content:
                        if block.get("type") == "tool_use":
                            tool_name = block.get("name", "")
                            tool_input = block.get("input", {})
                            activity = self._describe_tool_use(tool_name, tool_input)
                            self.sm.update_worker(wp.worker_id, session_log=[activity])

                            # Track file touches
                            if tool_name in ("Write", "Edit", "Read"):
                                fpath = tool_input.get("file_path", "")
                                if fpath and tool_name in ("Write", "Edit"):
                                    self._record_file_touch(packet_id, fpath)

                        elif block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                last_result_text = text

                # Capture final result
                if msg_type == "result":
                    result_text = data.get("result", "")
                    if result_text:
                        last_result_text = result_text

            # Read any remaining stderr
            stderr_text = ""
            if wp.process.stderr:
                stderr_data = await wp.process.stderr.read()
                stderr_text = stderr_data.decode("utf-8", errors="replace").strip()

            # Wait for process to finish
            exit_code = await wp.wait()

            # Save documentation from the worker's output
            if last_result_text:
                self.sm.update_packet(packet_id, documentation=last_result_text[:3000])

            # Update state based on exit code
            if exit_code == 0:
                packet = self.sm.get_packet(packet_id)
                packet_name = packet.name if packet else packet_id

                # 1. Write to central build log in the project
                self._write_build_log(packet_name, wp, last_result_text)

                # 2. Mark worker idle
                self.sm.update_worker(wp.worker_id, status=WorkerStatus.idle)
                self.sm.update_worker(wp.worker_id, session_log=["Completed successfully"])

                # 3. Mark packet COMPLETE (not review) — this triggers _unblock_dependents
                #    which automatically makes blocked packets ready for the next round
                self.sm.update_packet(packet_id, status=PacketStatus.complete)

                logger.info("Worker %s completed packet '%s' — dependents unblocked", wp.worker_id, packet_name)
            else:
                self.sm.update_worker(wp.worker_id, status=WorkerStatus.error)
                error_msg = stderr_text[:500] if stderr_text else f"Exit code {exit_code}"
                self.sm.update_worker(wp.worker_id, session_log=[f"FAILED: {error_msg}"])
                if not last_result_text:
                    self.sm.update_packet(packet_id, documentation=f"Worker failed: {error_msg}")
                logger.error("Worker %s failed (exit %d): %s", wp.worker_id, exit_code, error_msg)

        except Exception as e:
            logger.error("Error monitoring worker %s: %s", wp.worker_id, e)
            self.sm.update_worker(wp.worker_id, status=WorkerStatus.error)
            self.sm.update_worker(wp.worker_id, session_log=[f"EXCEPTION: {e}"])

    def _write_build_log(self, packet_name: str, wp: WorkerProcess, result_text: str) -> None:
        """Append a completed packet's work summary to the project's build log."""
        build_log = self.sm.project_path / "docs" / "build-log.md"
        build_log.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Get files touched from state
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

        # Append to build log
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

    async def run_auto(self, max_workers: int | None = None, poll_interval: int = 5) -> None:
        """Auto-continue mode: launch workers, wait, launch next round, repeat.

        Keeps running until all packets are complete or no more work can be done.
        """
        state = self.sm.load()
        limit = max_workers or state.config.max_workers
        total_packets = len(state.packets)

        logger.info("Auto-continue mode: %d packets, up to %d workers", total_packets, limit)

        while True:
            # Launch any available work
            launched = await self.launch_available(max_workers=limit)
            if launched:
                logger.info("Launched %d new worker(s)", len(launched))

            # Check if there's any running work
            running = [wp for wp in self.workers.values() if wp.is_running]

            if not running:
                # Nothing running — check if there's anything left to do
                state = self.sm.load()
                ready = [p for p in state.packets if p.status.value == "ready"]
                blocked = [p for p in state.packets if p.status.value == "blocked"]
                complete = [p for p in state.packets if p.status.value == "complete"]

                if not ready and not blocked:
                    logger.info("All packets complete or no more work available")
                    break
                elif not ready and blocked:
                    # Everything remaining is blocked — check if it's stuck
                    in_prog = [p for p in state.packets if p.status.value == "in_progress"]
                    if not in_prog:
                        logger.warning("Remaining packets are blocked with no work in progress — may be stuck")
                        break

            # Wait before checking again
            await asyncio.sleep(poll_interval)

        # Final summary
        state = self.sm.load()
        complete = len([p for p in state.packets if p.status.value == "complete"])
        total = len(state.packets)
        logger.info("Auto-continue finished: %d/%d packets complete", complete, total)

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
