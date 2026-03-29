"""Orchestrator: uses Claude Agent SDK to create build plans and assign packets."""

from __future__ import annotations

import json
import logging
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
    """Use the Claude Agent SDK to generate a build plan for the project.

    Returns the list of packet dicts created by Claude.
    """
    from claude_agent_sdk import ClaudeAgentOptions, query

    state = sm.load()
    effective_model = model or state.config.model
    prompt = _build_plan_prompt(sm.project_path, description)

    options = ClaudeAgentOptions(
        model=effective_model,
        cwd=str(sm.project_path),
        system_prompt=ORCHESTRATOR_PROMPT_PATH.read_text(encoding="utf-8"),
        permission_mode="auto",
        allowed_tools=["Read", "Glob", "Grep", "Bash(ls*)"],
        max_turns=20,
        max_budget_usd=2.0,
    )

    # Collect the final text response
    result_text = ""
    async for message in query(prompt=prompt, options=options):
        if hasattr(message, "content"):
            for block in message.content:
                if hasattr(block, "text"):
                    result_text = block.text  # Keep the last text block

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
                # Re-evaluate blocked status
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
