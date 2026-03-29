"""Tests for the orchestrator module."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from multi_claud.orchestrator import (
    _parse_packets_json,
    get_assignable_packets,
)
from multi_claud.state import StateManager, WorkerStatus


@pytest.fixture
def sm(tmp_path: Path) -> StateManager:
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    sm = StateManager(project_dir)
    sm.init("Test Project", "Testing orchestrator")
    return sm


class TestParsePacketsJson:
    def test_parse_clean_json_array(self):
        text = '[{"name": "P1", "description": "Packet 1"}]'
        result = _parse_packets_json(text)
        assert len(result) == 1
        assert result[0]["name"] == "P1"

    def test_parse_json_with_markdown_fences(self):
        text = '```json\n[{"name": "P1"}]\n```'
        result = _parse_packets_json(text)
        assert len(result) == 1

    def test_parse_json_with_surrounding_text(self):
        text = 'Here is the plan:\n[{"name": "P1"}, {"name": "P2"}]\nDone!'
        result = _parse_packets_json(text)
        assert len(result) == 2

    def test_parse_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            _parse_packets_json("This is not JSON at all")

    def test_parse_empty_array(self):
        result = _parse_packets_json("[]")
        assert result == []

    def test_parse_multiple_packets(self):
        text = """[
            {"name": "Foundation", "description": "Core models", "depends_on": []},
            {"name": "CLI", "description": "Command interface", "depends_on": ["Foundation"]},
            {"name": "Dashboard", "description": "Web UI", "depends_on": ["Foundation"]}
        ]"""
        result = _parse_packets_json(text)
        assert len(result) == 3
        assert result[1]["depends_on"] == ["Foundation"]


class TestGetAssignablePackets:
    def test_returns_ready_unassigned_packets(self, sm: StateManager):
        sm.add_packet("Ready 1")
        sm.add_packet("Ready 2")
        assignable = get_assignable_packets(sm)
        assert len(assignable) == 2

    def test_excludes_assigned_packets(self, sm: StateManager):
        p1 = sm.add_packet("Task 1")
        sm.add_packet("Task 2")
        w = sm.add_worker("W1")
        sm.assign_packet_to_worker(p1.id, w.id)
        assignable = get_assignable_packets(sm)
        assert len(assignable) == 1
        assert assignable[0]["name"] == "Task 2"

    def test_returns_empty_when_all_assigned(self, sm: StateManager):
        p1 = sm.add_packet("Task 1")
        w = sm.add_worker("W1")
        sm.assign_packet_to_worker(p1.id, w.id)
        assignable = get_assignable_packets(sm)
        assert len(assignable) == 0

    def test_returns_empty_when_no_ready(self, sm: StateManager):
        p1 = sm.add_packet("First")
        sm.add_packet("Second", depends_on=[p1.id])
        sm.update_packet(p1.id, status="in_progress")
        assignable = get_assignable_packets(sm)
        assert len(assignable) == 0
