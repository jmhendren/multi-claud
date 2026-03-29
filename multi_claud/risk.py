"""Risk detection engine for Multi-Claud.

Monitors for risks across parallel workers:
- File conflicts (two workers editing the same file)
- Duplicate effort (overlapping packet scopes)
- Unwired code (built but not connected)
- Stale workers (no activity for too long)
- Dependency violations (working before prerequisites complete)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from multi_claud.state import (
    PacketStatus,
    Risk,
    RiskSeverity,
    RiskType,
    StateManager,
    WorkerStatus,
)

logger = logging.getLogger(__name__)


class RiskDetector:
    """Detects risks across parallel Claude Code worker sessions."""

    def __init__(self, sm: StateManager):
        self.sm = sm

    def scan_all(self) -> list[Risk]:
        """Run all risk scans and return newly detected risks."""
        new_risks: list[Risk] = []
        new_risks.extend(self.detect_file_conflicts())
        new_risks.extend(self.detect_stale_workers())
        new_risks.extend(self.detect_dependency_violations())
        new_risks.extend(self.detect_unwired_code())
        return new_risks

    def detect_file_conflicts(self) -> list[Risk]:
        """Detect when two workers are editing the same file."""
        file_map = self.sm.get_files_being_edited()
        state = self.sm.load()
        existing_risk_files = {
            tuple(sorted(r.affected_files))
            for r in state.risks
            if r.type == RiskType.file_conflict and not r.resolved
        }

        new_risks = []
        for file_path, worker_ids in file_map.items():
            if len(worker_ids) <= 1:
                continue

            risk_key = tuple(sorted([file_path]))
            if risk_key in existing_risk_files:
                continue  # Already flagged

            worker_names = []
            for wid in worker_ids:
                w = next((w for w in state.workers if w.id == wid), None)
                worker_names.append(w.name if w else wid)

            risk = self.sm.add_risk(
                risk_type=RiskType.file_conflict,
                severity=RiskSeverity.critical,
                description=f"File '{file_path}' is being edited by multiple workers: {', '.join(worker_names)}",
                affected_workers=worker_ids,
                affected_files=[file_path],
            )
            new_risks.append(risk)
            logger.warning("File conflict detected: %s", file_path)

        return new_risks

    def detect_stale_workers(self, timeout_minutes: int | None = None) -> list[Risk]:
        """Detect workers that haven't reported activity recently."""
        state = self.sm.load()
        timeout = timeout_minutes or state.config.stale_timeout_minutes
        threshold = datetime.now(timezone.utc) - timedelta(minutes=timeout)

        existing_stale_workers = {
            r.affected_workers[0]
            for r in state.risks
            if r.type == RiskType.stale_worker and not r.resolved and r.affected_workers
        }

        new_risks = []
        for worker in state.workers:
            if worker.status != WorkerStatus.working:
                continue
            if worker.id in existing_stale_workers:
                continue

            if worker.last_activity and worker.last_activity < threshold:
                risk = self.sm.add_risk(
                    risk_type=RiskType.stale_worker,
                    severity=RiskSeverity.warning,
                    description=f"Worker '{worker.name}' has not reported activity for {timeout} minutes",
                    affected_workers=[worker.id],
                    affected_packets=[worker.assigned_packet] if worker.assigned_packet else [],
                )
                new_risks.append(risk)
                logger.warning("Stale worker detected: %s", worker.name)

        return new_risks

    def detect_dependency_violations(self) -> list[Risk]:
        """Detect packets being worked on before their dependencies are complete."""
        state = self.sm.load()
        existing_violations = {
            r.affected_packets[0]
            for r in state.risks
            if r.type == RiskType.dependency_violation and not r.resolved and r.affected_packets
        }

        new_risks = []
        for packet in state.packets:
            if packet.status != PacketStatus.in_progress:
                continue
            if packet.id in existing_violations:
                continue
            if not packet.depends_on:
                continue

            incomplete_deps = []
            for dep_id in packet.depends_on:
                dep = next((p for p in state.packets if p.id == dep_id), None)
                if dep and dep.status != PacketStatus.complete:
                    incomplete_deps.append(dep.name)

            if incomplete_deps:
                risk = self.sm.add_risk(
                    risk_type=RiskType.dependency_violation,
                    severity=RiskSeverity.critical,
                    description=f"Packet '{packet.name}' is in progress but depends on incomplete packets: {', '.join(incomplete_deps)}",
                    affected_packets=[packet.id],
                    affected_workers=[packet.assigned_worker] if packet.assigned_worker else [],
                )
                new_risks.append(risk)
                logger.warning("Dependency violation: %s", packet.name)

        return new_risks

    def detect_unwired_code(self) -> list[Risk]:
        """Detect completed packets that report files built but not wired in.

        This checks for packets marked complete that have documentation
        mentioning 'not wired', 'not connected', 'needs integration', etc.
        """
        state = self.sm.load()
        existing_unwired = {
            r.affected_packets[0]
            for r in state.risks
            if r.type == RiskType.unwired_code and not r.resolved and r.affected_packets
        }

        unwired_keywords = [
            "not wired", "not connected", "needs integration",
            "needs to be wired", "not yet integrated", "stub",
            "placeholder", "todo: wire", "todo: connect",
        ]

        new_risks = []
        for packet in state.packets:
            if packet.status != PacketStatus.complete:
                continue
            if packet.id in existing_unwired:
                continue
            if not packet.documentation:
                continue

            doc_lower = packet.documentation.lower()
            matches = [kw for kw in unwired_keywords if kw in doc_lower]
            if matches:
                risk = self.sm.add_risk(
                    risk_type=RiskType.unwired_code,
                    severity=RiskSeverity.warning,
                    description=f"Packet '{packet.name}' completed but documentation mentions unwired code: {', '.join(matches)}",
                    affected_packets=[packet.id],
                    affected_files=packet.files_touched,
                )
                new_risks.append(risk)
                logger.warning("Unwired code detected in: %s", packet.name)

        return new_risks

    def detect_duplicate_effort(self, packet_a_id: str, packet_b_id: str) -> Risk | None:
        """Check if two packets have significant file overlap suggesting duplicate effort."""
        pa = self.sm.get_packet(packet_a_id)
        pb = self.sm.get_packet(packet_b_id)

        if not pa or not pb:
            return None
        if not pa.files_touched or not pb.files_touched:
            return None

        overlap = set(pa.files_touched) & set(pb.files_touched)
        if not overlap:
            return None

        # Only flag if overlap is > 50% of either packet's files
        ratio_a = len(overlap) / len(pa.files_touched) if pa.files_touched else 0
        ratio_b = len(overlap) / len(pb.files_touched) if pb.files_touched else 0

        if ratio_a > 0.5 or ratio_b > 0.5:
            risk = self.sm.add_risk(
                risk_type=RiskType.duplicate_effort,
                severity=RiskSeverity.warning,
                description=f"Packets '{pa.name}' and '{pb.name}' have significant file overlap: {', '.join(overlap)}",
                affected_packets=[packet_a_id, packet_b_id],
                affected_files=list(overlap),
            )
            logger.warning("Duplicate effort detected: %s and %s", pa.name, pb.name)
            return risk

        return None
