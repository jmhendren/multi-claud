"""Orchestrator: uses Claude Code CLI to create build plans and assign packets.

Uses the `claude` CLI in headless mode (-p flag), which means it works with
your existing Claude Code subscription — no separate API key needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from multi_claud.state import (
    PacketStatus,
    StateManager,
)

logger = logging.getLogger(__name__)

ORCHESTRATOR_PROMPT_PATH = Path(__file__).parent.parent / "templates" / "orchestrator-prompt.md"


def _build_plan_prompt(project_path: Path, description: str) -> str:
    """Build the prompt for the orchestrator to create a plan."""
    system_prompt = ORCHESTRATOR_PROMPT_PATH.read_text(encoding="utf-8")

    return f"""{system_prompt}

## Project Directory
{project_path}

## What To Build
{description}

Analyze the project directory and create a build plan. Read key files to understand
the current state, then output a JSON array of packets following the format above.

IMPORTANT: Output ONLY the JSON array. No explanation, no markdown fences, just valid JSON."""


async def create_plan(sm: StateManager, description: str, model: str | None = None) -> list[dict]:
    """Use the Claude Code CLI to generate a build plan for the project.

    Uses `claude -p` (headless mode) which authenticates with your existing
    Claude Code subscription. No separate API key required.

    Returns the list of packet dicts created by Claude.
    """
    # Verify claude CLI is available
    claude_path = shutil.which("claude")
    if not claude_path:
        raise RuntimeError(
            "Claude Code CLI not found. Install it from https://code.claude.com"
        )

    state = sm.load()
    effective_model = model or state.config.model
    prompt = _build_plan_prompt(sm.project_path, description)

    cmd = [
        "claude", "-p", prompt,
        "--model", effective_model,
        "--output-format", "text",
        "--allowedTools", "Read,Glob,Grep,Bash(ls*)",
    ]

    logger.info("Launching orchestrator via claude CLI (model: %s)", effective_model)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(sm.project_path),
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Claude CLI failed (exit {process.returncode}): {error_msg}")

    result_text = stdout.decode("utf-8", errors="replace").strip()

    if not result_text:
        raise RuntimeError("Claude CLI returned empty output")

    # Parse the JSON response
    packets_data = _parse_packets_json(result_text)

    # Create packets in state
    created_packets = []
    packet_name_to_id: dict[str, str] = {}

    # First pass: create all packets to get their IDs
    for pdata in packets_data:
        packet = sm.add_packet(
            name=pdata["name"],
            description=pdata.get("description", ""),
        )
        packet_name_to_id[pdata["name"]] = packet.id
        created_packets.append(pdata)

    # Second pass: set dependencies (by name reference)
    for pdata in packets_data:
        packet_id = packet_name_to_id[pdata["name"]]
        dep_names = pdata.get("depends_on", [])
        if dep_names:
            dep_ids = []
            for dep_name in dep_names:
                if dep_name in packet_name_to_id:
                    dep_ids.append(packet_name_to_id[dep_name])
            if dep_ids:
                sm.update_packet(packet_id, depends_on=dep_ids)
                _recompute_status(sm, packet_id)

    logger.info("Created %d packets from orchestrator plan", len(created_packets))
    return created_packets


def _parse_packets_json(text: str) -> list[dict]:
    """Extract a JSON array from the orchestrator's response text."""
    text = text.strip()

    # Try direct parse first
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # Try to find JSON array in the text (Claude sometimes wraps in markdown)
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Could not parse orchestrator response as JSON array. Response was:\n{text[:500]}"
    )


def _recompute_status(sm: StateManager, packet_id: str) -> None:
    """Re-evaluate whether a packet should be blocked or ready based on dependencies."""
    packet = sm.get_packet(packet_id)
    if not packet or not packet.depends_on:
        return

    state = sm.load()
    all_met = all(
        any(p.id == dep and p.status == PacketStatus.complete for p in state.packets)
        for dep in packet.depends_on
    )
    new_status = PacketStatus.ready if all_met else PacketStatus.blocked
    if packet.status in (PacketStatus.backlog, PacketStatus.ready, PacketStatus.blocked):
        sm.update_packet(packet_id, status=new_status)


def get_assignable_packets(sm: StateManager) -> list[dict]:
    """Get packets that are ready to be assigned to workers."""
    ready = sm.get_ready_packets()
    state = sm.load()
    active_workers = [w for w in state.workers if w.status.value == "working"]
    assigned_ids = {w.assigned_packet for w in active_workers if w.assigned_packet}

    return [
        {"id": p.id, "name": p.name, "description": p.description}
        for p in ready
        if p.id not in assigned_ids
    ]
