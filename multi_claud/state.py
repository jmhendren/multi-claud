"""Core state management for Multi-Claud.

All project state lives in a single JSON file (.multi-claud/state.json).
This module provides Pydantic models for the data and thread-safe
read/write operations using filelock.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from filelock import FileLock
from pydantic import BaseModel, Field


# --- Enums ---

class PacketStatus(str, Enum):
    backlog = "backlog"
    ready = "ready"
    in_progress = "in_progress"
    review = "review"
    complete = "complete"
    blocked = "blocked"


class WorkerStatus(str, Enum):
    idle = "idle"
    working = "working"
    paused = "paused"
    stopped = "stopped"
    error = "error"


class RiskType(str, Enum):
    file_conflict = "file_conflict"
    duplicate_effort = "duplicate_effort"
    unwired_code = "unwired_code"
    dependency_violation = "dependency_violation"
    stale_worker = "stale_worker"


class RiskSeverity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


# --- Data Models ---

class Config(BaseModel):
    """Project-level configuration for Multi-Claud."""
    max_workers: int = 4
    dashboard_port: int = 8420
    dashboard_host: str = "localhost"
    stale_timeout_minutes: int = 30
    model: str = "claude-opus-4-6"
    worker_model: str = "claude-sonnet-4-6"


class Packet(BaseModel):
    """A unit of work in the build plan."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    description: str = ""
    status: PacketStatus = PacketStatus.backlog
    assigned_worker: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    files_touched: list[str] = Field(default_factory=list)
    documentation: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 2
    last_error: str | None = None


class Worker(BaseModel):
    """A Claude Code worker session."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    status: WorkerStatus = WorkerStatus.idle
    assigned_packet: str | None = None
    worktree_path: str | None = None
    worktree_branch: str | None = None
    pid: int | None = None
    last_activity: datetime | None = None
    session_log: list[str] = Field(default_factory=list)


class Risk(BaseModel):
    """A detected risk across parallel workers."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: RiskType
    severity: RiskSeverity
    description: str
    affected_packets: list[str] = Field(default_factory=list)
    affected_workers: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved: bool = False
    resolution: str | None = None


class ProjectInfo(BaseModel):
    """Basic project metadata."""
    name: str
    description: str = ""
    path: str = ""
    created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProjectState(BaseModel):
    """Complete state for a Multi-Claud managed project."""
    project: ProjectInfo
    packets: list[Packet] = Field(default_factory=list)
    workers: list[Worker] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    config: Config = Field(default_factory=Config)


# --- State Manager ---

STATE_DIR_NAME = ".multi-claud"
STATE_FILE_NAME = "state.json"
LOCK_FILE_NAME = "state.json.lock"


class StateManager:
    """Manages reading and writing project state to a JSON file with file locking.

    All mutating operations use _transact() which holds the file lock for the
    entire read-modify-write cycle, preventing concurrent-write races.
    """

    def __init__(self, project_path: Path):
        """Initialize with the root path of the target project."""
        self.project_path = project_path
        self.state_dir = project_path / STATE_DIR_NAME
        self.state_file = self.state_dir / STATE_FILE_NAME
        self.lock_file = self.state_dir / LOCK_FILE_NAME
        self._lock = FileLock(self.lock_file)

    def _load_unlocked(self) -> ProjectState:
        """Read state from disk. Caller must hold self._lock.

        Normalizes common invalid enum values that external processes
        (like Claude worker sessions) may write to the state file.
        """
        raw = self.state_file.read_text(encoding="utf-8")
        data = json.loads(raw)

        # Normalize packet statuses
        packet_status_map = {
            "completed": "complete", "done": "complete", "finished": "complete",
            "todo": "backlog", "pending": "backlog", "queued": "ready",
            "wip": "in_progress", "working": "in_progress",
            "reviewing": "review",
        }
        for p in data.get("packets", []):
            s = p.get("status", "")
            if s in packet_status_map:
                p["status"] = packet_status_map[s]

        # Normalize worker statuses
        worker_status_map = {
            "done": "idle", "completed": "idle", "finished": "idle",
            "running": "working", "active": "working", "busy": "working",
            "failed": "error", "crashed": "error",
            "killed": "stopped", "terminated": "stopped",
        }
        for w in data.get("workers", []):
            s = w.get("status", "")
            if s in worker_status_map:
                w["status"] = worker_status_map[s]

        return ProjectState.model_validate(data)

    def _save_unlocked(self, state: ProjectState) -> None:
        """Write state to disk. Caller must hold self._lock."""
        state.project.updated = datetime.now(timezone.utc)
        self.state_file.write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _transact(self, fn: Any) -> Any:
        """Run fn(state) inside the file lock, then save. Returns fn's return value."""
        with self._lock:
            state = self._load_unlocked()
            result = fn(state)
            self._save_unlocked(state)
            return result

    def init(self, name: str, description: str = "") -> ProjectState:
        """Initialize a new Multi-Claud state for a project."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.state_file.exists():
            raise FileExistsError(
                f"Multi-Claud already initialized at {self.state_dir}"
            )
        state = ProjectState(
            project=ProjectInfo(
                name=name,
                description=description,
                path=str(self.project_path),
            )
        )
        with self._lock:
            self.state_file.write_text(
                state.model_dump_json(indent=2),
                encoding="utf-8",
            )
        return state

    def exists(self) -> bool:
        """Check if a Multi-Claud state file exists for this project."""
        return self.state_file.exists()

    def load(self) -> ProjectState:
        """Load the current state from disk."""
        if not self.state_file.exists():
            raise FileNotFoundError(
                f"No Multi-Claud state found at {self.state_file}. Run 'multi-claud init' first."
            )
        with self._lock:
            return self._load_unlocked()

    def save(self, state: ProjectState) -> None:
        """Save state to disk. Updates the project.updated timestamp."""
        with self._lock:
            self._save_unlocked(state)

    # --- Packet operations ---

    def add_packet(self, name: str, description: str = "",
                   depends_on: list[str] | None = None) -> Packet:
        """Add a new packet to the project."""
        packet = Packet(
            name=name,
            description=description,
            depends_on=depends_on or [],
        )

        def _do(state: ProjectState) -> Packet:
            # Auto-set status based on dependencies
            if packet.depends_on:
                unmet = [
                    dep for dep in packet.depends_on
                    if not any(p.id == dep and p.status == PacketStatus.complete for p in state.packets)
                ]
                packet.status = PacketStatus.blocked if unmet else PacketStatus.ready
            else:
                packet.status = PacketStatus.ready

            # Update blocks on dependency packets
            for dep_id in packet.depends_on:
                for p in state.packets:
                    if p.id == dep_id and packet.id not in p.blocks:
                        p.blocks.append(packet.id)

            state.packets.append(packet)
            return packet

        self._transact(_do)
        return packet

    def get_packet(self, packet_id: str) -> Packet | None:
        """Get a packet by ID."""
        state = self.load()
        return next((p for p in state.packets if p.id == packet_id), None)

    def update_packet(self, packet_id: str, **updates: Any) -> Packet:
        """Update a packet's fields."""
        # Coerce string values to enums where needed
        if "status" in updates and isinstance(updates["status"], str):
            updates["status"] = PacketStatus(updates["status"])

        def _do(state: ProjectState) -> Packet:
            packet = next((p for p in state.packets if p.id == packet_id), None)
            if packet is None:
                raise ValueError(f"Packet '{packet_id}' not found")

            for key, value in updates.items():
                if not hasattr(packet, key):
                    raise ValueError(f"Packet has no field '{key}'")
                setattr(packet, key, value)

            # Auto-set timestamps based on status transitions
            if "status" in updates:
                if updates["status"] == PacketStatus.in_progress and packet.started_at is None:
                    packet.started_at = datetime.now(timezone.utc)
                elif updates["status"] == PacketStatus.complete:
                    packet.completed_at = datetime.now(timezone.utc)
                    _unblock_dependents(state, packet_id)

            return packet

        return self._transact(_do)

    def get_ready_packets(self) -> list[Packet]:
        """Get all packets that are ready to be worked on."""
        state = self.load()
        return [p for p in state.packets if p.status == PacketStatus.ready]

    def get_next_packet(self) -> Packet | None:
        """Get the next packet that should be worked on (first ready packet)."""
        ready = self.get_ready_packets()
        return ready[0] if ready else None

    # --- Worker operations ---

    def add_worker(self, name: str) -> Worker:
        """Register a new worker."""
        worker = Worker(name=name)

        def _do(state: ProjectState) -> Worker:
            state.workers.append(worker)
            return worker

        self._transact(_do)
        return worker

    def get_worker(self, worker_id: str) -> Worker | None:
        """Get a worker by ID."""
        state = self.load()
        return next((w for w in state.workers if w.id == worker_id), None)

    def update_worker(self, worker_id: str, **updates: Any) -> Worker:
        """Update a worker's fields."""
        if "status" in updates and isinstance(updates["status"], str):
            updates["status"] = WorkerStatus(updates["status"])

        def _do(state: ProjectState) -> Worker:
            worker = next((w for w in state.workers if w.id == worker_id), None)
            if worker is None:
                raise ValueError(f"Worker '{worker_id}' not found")

            for key, value in updates.items():
                if not hasattr(worker, key):
                    raise ValueError(f"Worker has no field '{key}'")
                setattr(worker, key, value)

            if "last_activity" not in updates:
                worker.last_activity = datetime.now(timezone.utc)
            return worker

        return self._transact(_do)

    def assign_packet_to_worker(self, packet_id: str, worker_id: str) -> tuple[Packet, Worker]:
        """Assign a packet to a worker. Updates both records atomically."""
        def _do(state: ProjectState) -> tuple[Packet, Worker]:
            packet = next((p for p in state.packets if p.id == packet_id), None)
            worker = next((w for w in state.workers if w.id == worker_id), None)

            if packet is None:
                raise ValueError(f"Packet '{packet_id}' not found")
            if worker is None:
                raise ValueError(f"Worker '{worker_id}' not found")
            if packet.status not in (PacketStatus.ready, PacketStatus.backlog):
                raise ValueError(f"Packet '{packet_id}' is not available (status: {packet.status})")
            if worker.status not in (WorkerStatus.idle, WorkerStatus.stopped):
                raise ValueError(f"Worker '{worker_id}' is not available (status: {worker.status})")

            packet.status = PacketStatus.in_progress
            packet.assigned_worker = worker_id
            packet.started_at = datetime.now(timezone.utc)

            worker.status = WorkerStatus.working
            worker.assigned_packet = packet_id
            worker.last_activity = datetime.now(timezone.utc)

            return packet, worker

        return self._transact(_do)

    def remove_worker(self, worker_id: str) -> None:
        """Remove a worker from the project."""
        def _do(state: ProjectState) -> None:
            state.workers = [w for w in state.workers if w.id != worker_id]
            for packet in state.packets:
                if packet.assigned_worker == worker_id:
                    packet.assigned_worker = None
                    if packet.status == PacketStatus.in_progress:
                        packet.status = PacketStatus.ready

        self._transact(_do)

    # --- Risk operations ---

    def add_risk(self, risk_type: RiskType, severity: RiskSeverity,
                 description: str, affected_packets: list[str] | None = None,
                 affected_workers: list[str] | None = None,
                 affected_files: list[str] | None = None) -> Risk:
        """Record a new risk."""
        risk = Risk(
            type=risk_type,
            severity=severity,
            description=description,
            affected_packets=affected_packets or [],
            affected_workers=affected_workers or [],
            affected_files=affected_files or [],
        )

        def _do(state: ProjectState) -> Risk:
            state.risks.append(risk)
            return risk

        self._transact(_do)
        return risk

    def resolve_risk(self, risk_id: str, resolution: str) -> Risk:
        """Mark a risk as resolved."""
        def _do(state: ProjectState) -> Risk:
            risk = next((r for r in state.risks if r.id == risk_id), None)
            if risk is None:
                raise ValueError(f"Risk '{risk_id}' not found")
            risk.resolved = True
            risk.resolution = resolution
            return risk

        return self._transact(_do)

    def get_active_risks(self) -> list[Risk]:
        """Get all unresolved risks."""
        state = self.load()
        return [r for r in state.risks if not r.resolved]

    # --- Query helpers ---

    def get_packets_by_status(self, status: PacketStatus) -> list[Packet]:
        """Get all packets with a given status."""
        state = self.load()
        return [p for p in state.packets if p.status == status]

    def get_active_workers(self) -> list[Worker]:
        """Get all workers that are currently working."""
        state = self.load()
        return [w for w in state.workers if w.status == WorkerStatus.working]

    def get_files_being_edited(self) -> dict[str, list[str]]:
        """Get a mapping of files to worker IDs currently editing them."""
        state = self.load()
        file_map: dict[str, list[str]] = {}
        for packet in state.packets:
            if packet.status == PacketStatus.in_progress and packet.assigned_worker:
                for f in packet.files_touched:
                    file_map.setdefault(f, []).append(packet.assigned_worker)
        return file_map


def _unblock_dependents(state: ProjectState, completed_packet_id: str) -> None:
    """When a packet completes, check if any blocked packets can be unblocked."""
    for packet in state.packets:
        if packet.status != PacketStatus.blocked:
            continue
        if completed_packet_id not in packet.depends_on:
            continue
        all_met = all(
            any(p.id == dep and p.status == PacketStatus.complete for p in state.packets)
            for dep in packet.depends_on
        )
        if all_met:
            packet.status = PacketStatus.ready
