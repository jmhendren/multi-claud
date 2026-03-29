"""Tests for the risk detection engine."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from multi_claud.risk import RiskDetector
from multi_claud.state import (
    PacketStatus,
    RiskType,
    StateManager,
    WorkerStatus,
)


@pytest.fixture
def sm(tmp_path: Path) -> StateManager:
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    sm = StateManager(project_dir)
    sm.init("Test Project")
    return sm


@pytest.fixture
def rd(sm: StateManager) -> RiskDetector:
    return RiskDetector(sm)


class TestFileConflictDetection:
    def test_no_conflicts_when_no_overlap(self, sm: StateManager, rd: RiskDetector):
        p1 = sm.add_packet("P1")
        p2 = sm.add_packet("P2")
        w1 = sm.add_worker("W1")
        w2 = sm.add_worker("W2")
        sm.assign_packet_to_worker(p1.id, w1.id)
        sm.assign_packet_to_worker(p2.id, w2.id)
        sm.update_packet(p1.id, files_touched=["a.py"])
        sm.update_packet(p2.id, files_touched=["b.py"])

        risks = rd.detect_file_conflicts()
        assert len(risks) == 0

    def test_detects_conflict_on_shared_file(self, sm: StateManager, rd: RiskDetector):
        p1 = sm.add_packet("P1")
        p2 = sm.add_packet("P2")
        w1 = sm.add_worker("W1")
        w2 = sm.add_worker("W2")
        sm.assign_packet_to_worker(p1.id, w1.id)
        sm.assign_packet_to_worker(p2.id, w2.id)
        sm.update_packet(p1.id, files_touched=["shared.py"])
        sm.update_packet(p2.id, files_touched=["shared.py"])

        risks = rd.detect_file_conflicts()
        assert len(risks) == 1
        assert risks[0].type == RiskType.file_conflict
        assert "shared.py" in risks[0].affected_files

    def test_does_not_duplicate_existing_risk(self, sm: StateManager, rd: RiskDetector):
        p1 = sm.add_packet("P1")
        p2 = sm.add_packet("P2")
        w1 = sm.add_worker("W1")
        w2 = sm.add_worker("W2")
        sm.assign_packet_to_worker(p1.id, w1.id)
        sm.assign_packet_to_worker(p2.id, w2.id)
        sm.update_packet(p1.id, files_touched=["shared.py"])
        sm.update_packet(p2.id, files_touched=["shared.py"])

        rd.detect_file_conflicts()
        # Run again — should not create duplicate
        risks = rd.detect_file_conflicts()
        assert len(risks) == 0


class TestStaleWorkerDetection:
    def test_no_stale_when_recent_activity(self, sm: StateManager, rd: RiskDetector):
        p = sm.add_packet("Task")
        w = sm.add_worker("W1")
        sm.assign_packet_to_worker(p.id, w.id)
        # update_worker sets last_activity to now
        sm.update_worker(w.id, status=WorkerStatus.working)

        risks = rd.detect_stale_workers(timeout_minutes=30)
        assert len(risks) == 0

    def test_detects_stale_worker(self, sm: StateManager, rd: RiskDetector):
        p = sm.add_packet("Task")
        w = sm.add_worker("W1")
        sm.assign_packet_to_worker(p.id, w.id)
        # Manually set last_activity to 60 minutes ago
        old_time = datetime.now(timezone.utc) - timedelta(minutes=60)
        sm.update_worker(w.id, last_activity=old_time)

        risks = rd.detect_stale_workers(timeout_minutes=30)
        assert len(risks) == 1
        assert risks[0].type == RiskType.stale_worker

    def test_ignores_non_working_workers(self, sm: StateManager, rd: RiskDetector):
        w = sm.add_worker("W1")
        old_time = datetime.now(timezone.utc) - timedelta(minutes=60)
        sm.update_worker(w.id, last_activity=old_time, status=WorkerStatus.idle)

        risks = rd.detect_stale_workers(timeout_minutes=30)
        assert len(risks) == 0


class TestDependencyViolation:
    def test_no_violation_when_deps_complete(self, sm: StateManager, rd: RiskDetector):
        p1 = sm.add_packet("Foundation")
        p2 = sm.add_packet("CLI", depends_on=[p1.id])
        sm.update_packet(p1.id, status=PacketStatus.complete)
        sm.update_packet(p2.id, status=PacketStatus.in_progress)

        risks = rd.detect_dependency_violations()
        assert len(risks) == 0

    def test_detects_violation_when_dep_incomplete(self, sm: StateManager, rd: RiskDetector):
        p1 = sm.add_packet("Foundation")
        p2 = sm.add_packet("CLI", depends_on=[p1.id])
        # Force p2 to in_progress despite p1 not being complete
        sm.update_packet(p2.id, status=PacketStatus.in_progress)

        risks = rd.detect_dependency_violations()
        assert len(risks) == 1
        assert risks[0].type == RiskType.dependency_violation

    def test_no_violation_for_packet_without_deps(self, sm: StateManager, rd: RiskDetector):
        p = sm.add_packet("Standalone")
        sm.update_packet(p.id, status=PacketStatus.in_progress)

        risks = rd.detect_dependency_violations()
        assert len(risks) == 0


class TestUnwiredCodeDetection:
    def test_detects_unwired_keywords_in_docs(self, sm: StateManager, rd: RiskDetector):
        p = sm.add_packet("Feature")
        sm.update_packet(
            p.id,
            status=PacketStatus.complete,
            documentation="Built the auth module. Note: not wired into the main app yet.",
            files_touched=["src/auth.py"],
        )

        risks = rd.detect_unwired_code()
        assert len(risks) == 1
        assert risks[0].type == RiskType.unwired_code

    def test_no_risk_when_docs_clean(self, sm: StateManager, rd: RiskDetector):
        p = sm.add_packet("Feature")
        sm.update_packet(
            p.id,
            status=PacketStatus.complete,
            documentation="Built and fully integrated the auth module.",
        )

        risks = rd.detect_unwired_code()
        assert len(risks) == 0

    def test_ignores_incomplete_packets(self, sm: StateManager, rd: RiskDetector):
        p = sm.add_packet("Feature")
        sm.update_packet(
            p.id,
            status=PacketStatus.in_progress,
            documentation="Not wired yet — still working on it.",
        )

        risks = rd.detect_unwired_code()
        assert len(risks) == 0


class TestDuplicateEffortDetection:
    def test_detects_high_overlap(self, sm: StateManager, rd: RiskDetector):
        p1 = sm.add_packet("P1")
        p2 = sm.add_packet("P2")
        sm.update_packet(p1.id, files_touched=["a.py", "b.py"])
        sm.update_packet(p2.id, files_touched=["a.py", "b.py", "c.py"])

        risk = rd.detect_duplicate_effort(p1.id, p2.id)
        assert risk is not None
        assert risk.type == RiskType.duplicate_effort

    def test_no_duplicate_with_low_overlap(self, sm: StateManager, rd: RiskDetector):
        p1 = sm.add_packet("P1")
        p2 = sm.add_packet("P2")
        sm.update_packet(p1.id, files_touched=["a.py", "b.py", "c.py", "d.py"])
        sm.update_packet(p2.id, files_touched=["a.py", "e.py", "f.py", "g.py"])

        risk = rd.detect_duplicate_effort(p1.id, p2.id)
        assert risk is None

    def test_no_duplicate_with_no_overlap(self, sm: StateManager, rd: RiskDetector):
        p1 = sm.add_packet("P1")
        p2 = sm.add_packet("P2")
        sm.update_packet(p1.id, files_touched=["a.py"])
        sm.update_packet(p2.id, files_touched=["b.py"])

        risk = rd.detect_duplicate_effort(p1.id, p2.id)
        assert risk is None

    def test_returns_none_for_empty_files(self, sm: StateManager, rd: RiskDetector):
        p1 = sm.add_packet("P1")
        p2 = sm.add_packet("P2")

        risk = rd.detect_duplicate_effort(p1.id, p2.id)
        assert risk is None


class TestScanAll:
    def test_scan_all_returns_combined_risks(self, sm: StateManager, rd: RiskDetector):
        # Set up a file conflict
        p1 = sm.add_packet("P1")
        p2 = sm.add_packet("P2")
        w1 = sm.add_worker("W1")
        w2 = sm.add_worker("W2")
        sm.assign_packet_to_worker(p1.id, w1.id)
        sm.assign_packet_to_worker(p2.id, w2.id)
        sm.update_packet(p1.id, files_touched=["shared.py"])
        sm.update_packet(p2.id, files_touched=["shared.py"])

        risks = rd.scan_all()
        assert len(risks) >= 1
        types = {r.type for r in risks}
        assert RiskType.file_conflict in types

    def test_scan_all_on_clean_state(self, sm: StateManager, rd: RiskDetector):
        risks = rd.scan_all()
        assert len(risks) == 0
