"""Comprehensive tests for the state management module."""

import json
import threading
from pathlib import Path

import pytest

from multi_claud.state import (
    Config,
    Packet,
    PacketStatus,
    ProjectInfo,
    ProjectState,
    Risk,
    RiskSeverity,
    RiskType,
    StateManager,
    Worker,
    WorkerStatus,
)


# --- Fixtures ---

@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a temporary project directory."""
    return tmp_path / "test-project"


@pytest.fixture
def sm(project_dir: Path) -> StateManager:
    """Create a StateManager pointed at a temp directory."""
    project_dir.mkdir()
    return StateManager(project_dir)


@pytest.fixture
def initialized_sm(sm: StateManager) -> StateManager:
    """Create and initialize a StateManager."""
    sm.init("Test Project", "A test project")
    return sm


# --- Model Tests ---

class TestModels:
    def test_packet_defaults(self):
        p = Packet(name="Test Packet")
        assert p.name == "Test Packet"
        assert p.status == PacketStatus.backlog
        assert p.assigned_worker is None
        assert p.depends_on == []
        assert p.blocks == []
        assert p.files_touched == []
        assert p.documentation is None
        assert p.id  # auto-generated
        assert p.created_at is not None

    def test_worker_defaults(self):
        w = Worker(name="Worker 1")
        assert w.name == "Worker 1"
        assert w.status == WorkerStatus.idle
        assert w.assigned_packet is None
        assert w.session_log == []
        assert w.id  # auto-generated

    def test_risk_defaults(self):
        r = Risk(
            type=RiskType.file_conflict,
            severity=RiskSeverity.critical,
            description="Two workers editing the same file",
        )
        assert r.type == RiskType.file_conflict
        assert r.severity == RiskSeverity.critical
        assert r.resolved is False
        assert r.resolution is None

    def test_config_defaults(self):
        c = Config()
        assert c.max_workers == 4
        assert c.dashboard_port == 8420
        assert c.dashboard_host == "localhost"
        assert c.stale_timeout_minutes == 30
        assert c.model == "claude-opus-4-6"
        assert c.worker_model == "claude-sonnet-4-6"

    def test_project_state_serialization_roundtrip(self):
        state = ProjectState(
            project=ProjectInfo(name="Roundtrip Test", description="Testing serialization"),
            packets=[Packet(name="P1"), Packet(name="P2")],
            workers=[Worker(name="W1")],
            risks=[Risk(type=RiskType.stale_worker, severity=RiskSeverity.info, description="test")],
        )
        json_str = state.model_dump_json(indent=2)
        restored = ProjectState.model_validate_json(json_str)
        assert restored.project.name == "Roundtrip Test"
        assert len(restored.packets) == 2
        assert len(restored.workers) == 1
        assert len(restored.risks) == 1
        assert restored.packets[0].name == "P1"


# --- StateManager Init Tests ---

class TestStateManagerInit:
    def test_init_creates_state_dir_and_file(self, sm: StateManager, project_dir: Path):
        sm.init("My Project")
        assert (project_dir / ".multi-claud").is_dir()
        assert (project_dir / ".multi-claud" / "state.json").is_file()

    def test_init_creates_valid_json(self, sm: StateManager):
        sm.init("My Project", "desc")
        raw = sm.state_file.read_text()
        data = json.loads(raw)
        assert data["project"]["name"] == "My Project"
        assert data["project"]["description"] == "desc"
        assert data["packets"] == []
        assert data["workers"] == []
        assert data["risks"] == []

    def test_init_raises_if_already_initialized(self, sm: StateManager):
        sm.init("My Project")
        with pytest.raises(FileExistsError, match="already initialized"):
            sm.init("My Project Again")

    def test_exists_false_before_init(self, sm: StateManager):
        assert sm.exists() is False

    def test_exists_true_after_init(self, sm: StateManager):
        sm.init("Test")
        assert sm.exists() is True


# --- StateManager Load/Save Tests ---

class TestStateManagerLoadSave:
    def test_load_returns_state(self, initialized_sm: StateManager):
        state = initialized_sm.load()
        assert state.project.name == "Test Project"
        assert state.project.description == "A test project"

    def test_load_raises_if_no_state(self, sm: StateManager):
        with pytest.raises(FileNotFoundError, match="No Multi-Claud state found"):
            sm.load()

    def test_save_updates_timestamp(self, initialized_sm: StateManager):
        state = initialized_sm.load()
        old_updated = state.project.updated
        initialized_sm.save(state)
        reloaded = initialized_sm.load()
        assert reloaded.project.updated >= old_updated

    def test_save_persists_changes(self, initialized_sm: StateManager):
        state = initialized_sm.load()
        state.packets.append(Packet(name="New Packet"))
        initialized_sm.save(state)
        reloaded = initialized_sm.load()
        assert len(reloaded.packets) == 1
        assert reloaded.packets[0].name == "New Packet"


# --- Packet Operation Tests ---

class TestPacketOperations:
    def test_add_packet_no_dependencies(self, initialized_sm: StateManager):
        packet = initialized_sm.add_packet("Foundation")
        assert packet.name == "Foundation"
        assert packet.status == PacketStatus.ready  # no deps = ready
        state = initialized_sm.load()
        assert len(state.packets) == 1

    def test_add_packet_with_unmet_dependencies(self, initialized_sm: StateManager):
        p1 = initialized_sm.add_packet("First")
        p2 = initialized_sm.add_packet("Second", depends_on=[p1.id])
        assert p2.status == PacketStatus.blocked

    def test_add_packet_with_met_dependencies(self, initialized_sm: StateManager):
        p1 = initialized_sm.add_packet("First")
        initialized_sm.update_packet(p1.id, status=PacketStatus.complete)
        p2 = initialized_sm.add_packet("Second", depends_on=[p1.id])
        assert p2.status == PacketStatus.ready

    def test_add_packet_updates_blocks_on_dependency(self, initialized_sm: StateManager):
        p1 = initialized_sm.add_packet("First")
        p2 = initialized_sm.add_packet("Second", depends_on=[p1.id])
        # Reload p1 to see updated blocks
        p1_reloaded = initialized_sm.get_packet(p1.id)
        assert p2.id in p1_reloaded.blocks

    def test_get_packet_exists(self, initialized_sm: StateManager):
        p = initialized_sm.add_packet("Test")
        found = initialized_sm.get_packet(p.id)
        assert found is not None
        assert found.name == "Test"

    def test_get_packet_not_found(self, initialized_sm: StateManager):
        assert initialized_sm.get_packet("nonexistent") is None

    def test_update_packet_fields(self, initialized_sm: StateManager):
        p = initialized_sm.add_packet("Test")
        updated = initialized_sm.update_packet(p.id, description="Updated desc")
        assert updated.description == "Updated desc"

    def test_update_packet_invalid_field(self, initialized_sm: StateManager):
        p = initialized_sm.add_packet("Test")
        with pytest.raises(ValueError, match="has no field"):
            initialized_sm.update_packet(p.id, nonexistent_field="value")

    def test_update_packet_not_found(self, initialized_sm: StateManager):
        with pytest.raises(ValueError, match="not found"):
            initialized_sm.update_packet("nonexistent", name="New Name")

    def test_update_packet_to_in_progress_sets_started_at(self, initialized_sm: StateManager):
        p = initialized_sm.add_packet("Test")
        assert p.started_at is None
        updated = initialized_sm.update_packet(p.id, status=PacketStatus.in_progress)
        assert updated.started_at is not None

    def test_update_packet_to_complete_sets_completed_at(self, initialized_sm: StateManager):
        p = initialized_sm.add_packet("Test")
        initialized_sm.update_packet(p.id, status=PacketStatus.in_progress)
        updated = initialized_sm.update_packet(p.id, status=PacketStatus.complete)
        assert updated.completed_at is not None

    def test_completing_packet_unblocks_dependents(self, initialized_sm: StateManager):
        p1 = initialized_sm.add_packet("First")
        p2 = initialized_sm.add_packet("Second", depends_on=[p1.id])
        assert initialized_sm.get_packet(p2.id).status == PacketStatus.blocked

        initialized_sm.update_packet(p1.id, status=PacketStatus.complete)
        assert initialized_sm.get_packet(p2.id).status == PacketStatus.ready

    def test_completing_packet_does_not_unblock_if_other_deps_remain(self, initialized_sm: StateManager):
        p1 = initialized_sm.add_packet("First")
        p2 = initialized_sm.add_packet("Second")
        p3 = initialized_sm.add_packet("Third", depends_on=[p1.id, p2.id])
        assert initialized_sm.get_packet(p3.id).status == PacketStatus.blocked

        initialized_sm.update_packet(p1.id, status=PacketStatus.complete)
        # p3 should still be blocked because p2 isn't complete
        assert initialized_sm.get_packet(p3.id).status == PacketStatus.blocked

        initialized_sm.update_packet(p2.id, status=PacketStatus.complete)
        assert initialized_sm.get_packet(p3.id).status == PacketStatus.ready

    def test_get_ready_packets(self, initialized_sm: StateManager):
        p1 = initialized_sm.add_packet("Ready One")
        p2 = initialized_sm.add_packet("Ready Two")
        p3 = initialized_sm.add_packet("Blocked", depends_on=[p1.id])
        ready = initialized_sm.get_ready_packets()
        ids = [p.id for p in ready]
        assert p1.id in ids
        assert p2.id in ids
        assert p3.id not in ids

    def test_get_next_packet(self, initialized_sm: StateManager):
        p1 = initialized_sm.add_packet("First")
        initialized_sm.add_packet("Second")
        nxt = initialized_sm.get_next_packet()
        assert nxt is not None
        assert nxt.id == p1.id

    def test_get_next_packet_none_when_all_blocked(self, initialized_sm: StateManager):
        p1 = initialized_sm.add_packet("First")
        initialized_sm.add_packet("Second", depends_on=[p1.id])
        initialized_sm.update_packet(p1.id, status=PacketStatus.in_progress)
        nxt = initialized_sm.get_next_packet()
        assert nxt is None

    def test_get_packets_by_status(self, initialized_sm: StateManager):
        initialized_sm.add_packet("A")
        p2 = initialized_sm.add_packet("B")
        initialized_sm.update_packet(p2.id, status=PacketStatus.in_progress)
        ready = initialized_sm.get_packets_by_status(PacketStatus.ready)
        in_progress = initialized_sm.get_packets_by_status(PacketStatus.in_progress)
        assert len(ready) == 1
        assert len(in_progress) == 1


# --- Worker Operation Tests ---

class TestWorkerOperations:
    def test_add_worker(self, initialized_sm: StateManager):
        w = initialized_sm.add_worker("Worker 1")
        assert w.name == "Worker 1"
        assert w.status == WorkerStatus.idle
        state = initialized_sm.load()
        assert len(state.workers) == 1

    def test_get_worker_exists(self, initialized_sm: StateManager):
        w = initialized_sm.add_worker("W1")
        found = initialized_sm.get_worker(w.id)
        assert found is not None
        assert found.name == "W1"

    def test_get_worker_not_found(self, initialized_sm: StateManager):
        assert initialized_sm.get_worker("nonexistent") is None

    def test_update_worker(self, initialized_sm: StateManager):
        w = initialized_sm.add_worker("W1")
        updated = initialized_sm.update_worker(w.id, status=WorkerStatus.working)
        assert updated.status == WorkerStatus.working
        assert updated.last_activity is not None

    def test_update_worker_not_found(self, initialized_sm: StateManager):
        with pytest.raises(ValueError, match="not found"):
            initialized_sm.update_worker("nonexistent", status=WorkerStatus.idle)

    def test_update_worker_invalid_field(self, initialized_sm: StateManager):
        w = initialized_sm.add_worker("W1")
        with pytest.raises(ValueError, match="has no field"):
            initialized_sm.update_worker(w.id, fake_field="value")

    def test_assign_packet_to_worker(self, initialized_sm: StateManager):
        p = initialized_sm.add_packet("Task")
        w = initialized_sm.add_worker("W1")
        packet, worker = initialized_sm.assign_packet_to_worker(p.id, w.id)
        assert packet.status == PacketStatus.in_progress
        assert packet.assigned_worker == w.id
        assert packet.started_at is not None
        assert worker.status == WorkerStatus.working
        assert worker.assigned_packet == p.id

    def test_assign_unavailable_packet_raises(self, initialized_sm: StateManager):
        p = initialized_sm.add_packet("Task")
        w = initialized_sm.add_worker("W1")
        initialized_sm.update_packet(p.id, status=PacketStatus.in_progress)
        with pytest.raises(ValueError, match="not available"):
            initialized_sm.assign_packet_to_worker(p.id, w.id)

    def test_assign_busy_worker_raises(self, initialized_sm: StateManager):
        p1 = initialized_sm.add_packet("Task 1")
        p2 = initialized_sm.add_packet("Task 2")
        w = initialized_sm.add_worker("W1")
        initialized_sm.assign_packet_to_worker(p1.id, w.id)
        with pytest.raises(ValueError, match="not available"):
            initialized_sm.assign_packet_to_worker(p2.id, w.id)

    def test_assign_nonexistent_packet_raises(self, initialized_sm: StateManager):
        w = initialized_sm.add_worker("W1")
        with pytest.raises(ValueError, match="Packet.*not found"):
            initialized_sm.assign_packet_to_worker("fake", w.id)

    def test_assign_nonexistent_worker_raises(self, initialized_sm: StateManager):
        p = initialized_sm.add_packet("Task")
        with pytest.raises(ValueError, match="Worker.*not found"):
            initialized_sm.assign_packet_to_worker(p.id, "fake")

    def test_remove_worker(self, initialized_sm: StateManager):
        w = initialized_sm.add_worker("W1")
        initialized_sm.remove_worker(w.id)
        assert initialized_sm.get_worker(w.id) is None

    def test_remove_worker_unassigns_packet(self, initialized_sm: StateManager):
        p = initialized_sm.add_packet("Task")
        w = initialized_sm.add_worker("W1")
        initialized_sm.assign_packet_to_worker(p.id, w.id)
        initialized_sm.remove_worker(w.id)
        packet = initialized_sm.get_packet(p.id)
        assert packet.assigned_worker is None
        assert packet.status == PacketStatus.ready

    def test_get_active_workers(self, initialized_sm: StateManager):
        w1 = initialized_sm.add_worker("W1")
        initialized_sm.add_worker("W2")
        initialized_sm.update_worker(w1.id, status=WorkerStatus.working)
        active = initialized_sm.get_active_workers()
        assert len(active) == 1
        assert active[0].id == w1.id


# --- Risk Operation Tests ---

class TestRiskOperations:
    def test_add_risk(self, initialized_sm: StateManager):
        risk = initialized_sm.add_risk(
            RiskType.file_conflict,
            RiskSeverity.critical,
            "Workers W1 and W2 both editing auth.py",
            affected_files=["auth.py"],
        )
        assert risk.type == RiskType.file_conflict
        assert risk.severity == RiskSeverity.critical
        assert risk.resolved is False
        state = initialized_sm.load()
        assert len(state.risks) == 1

    def test_resolve_risk(self, initialized_sm: StateManager):
        risk = initialized_sm.add_risk(
            RiskType.stale_worker,
            RiskSeverity.warning,
            "Worker W1 hasn't reported in 30 minutes",
        )
        resolved = initialized_sm.resolve_risk(risk.id, "Worker was restarted")
        assert resolved.resolved is True
        assert resolved.resolution == "Worker was restarted"

    def test_resolve_nonexistent_risk_raises(self, initialized_sm: StateManager):
        with pytest.raises(ValueError, match="not found"):
            initialized_sm.resolve_risk("fake", "n/a")

    def test_get_active_risks(self, initialized_sm: StateManager):
        r1 = initialized_sm.add_risk(RiskType.file_conflict, RiskSeverity.critical, "Conflict 1")
        r2 = initialized_sm.add_risk(RiskType.stale_worker, RiskSeverity.info, "Stale")
        initialized_sm.resolve_risk(r2.id, "Fixed")
        active = initialized_sm.get_active_risks()
        assert len(active) == 1
        assert active[0].id == r1.id


# --- File Tracking Tests ---

class TestFileTracking:
    def test_get_files_being_edited_empty(self, initialized_sm: StateManager):
        assert initialized_sm.get_files_being_edited() == {}

    def test_get_files_being_edited_with_active_work(self, initialized_sm: StateManager):
        p = initialized_sm.add_packet("Task")
        w = initialized_sm.add_worker("W1")
        initialized_sm.assign_packet_to_worker(p.id, w.id)
        initialized_sm.update_packet(p.id, files_touched=["src/auth.py", "src/models.py"])

        files = initialized_sm.get_files_being_edited()
        assert "src/auth.py" in files
        assert "src/models.py" in files
        assert w.id in files["src/auth.py"]

    def test_get_files_being_edited_detects_overlap(self, initialized_sm: StateManager):
        p1 = initialized_sm.add_packet("Task 1")
        p2 = initialized_sm.add_packet("Task 2")
        w1 = initialized_sm.add_worker("W1")
        w2 = initialized_sm.add_worker("W2")
        initialized_sm.assign_packet_to_worker(p1.id, w1.id)
        initialized_sm.assign_packet_to_worker(p2.id, w2.id)
        initialized_sm.update_packet(p1.id, files_touched=["shared.py"])
        initialized_sm.update_packet(p2.id, files_touched=["shared.py"])

        files = initialized_sm.get_files_being_edited()
        assert len(files["shared.py"]) == 2


# --- Concurrent Access Tests ---

class TestConcurrentAccess:
    def test_concurrent_packet_adds(self, initialized_sm: StateManager):
        """Multiple threads adding packets shouldn't corrupt state."""
        errors = []

        def add_packet(name: str):
            try:
                initialized_sm.add_packet(name)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_packet, args=(f"Packet-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors during concurrent adds: {errors}"
        state = initialized_sm.load()
        assert len(state.packets) == 10

    def test_concurrent_worker_adds(self, initialized_sm: StateManager):
        """Multiple threads adding workers shouldn't corrupt state."""
        errors = []

        def add_worker(name: str):
            try:
                initialized_sm.add_worker(name)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_worker, args=(f"Worker-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors during concurrent adds: {errors}"
        state = initialized_sm.load()
        assert len(state.workers) == 10

    def test_state_file_is_valid_json_after_concurrent_writes(self, initialized_sm: StateManager):
        """After concurrent writes, the state file should be valid JSON."""
        def writer(i: int):
            initialized_sm.add_packet(f"Packet-{i}")

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        raw = initialized_sm.state_file.read_text()
        data = json.loads(raw)  # Should not raise
        assert len(data["packets"]) == 5
