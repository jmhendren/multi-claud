"""Tests for the worker manager module."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from multi_claud.state import PacketStatus, StateManager, WorkerStatus
from multi_claud.worker import WorkerManager, _build_worker_prompt


@pytest.fixture
def sm(tmp_path: Path) -> StateManager:
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    sm = StateManager(project_dir)
    sm.init("Test Project", "Testing workers")
    return sm


@pytest.fixture
def wm(sm: StateManager) -> WorkerManager:
    return WorkerManager(sm)


class TestBuildWorkerPrompt:
    def test_prompt_includes_packet_info(self):
        prompt = _build_worker_prompt("Auth Module", "Build authentication", Path("/test"))
        assert "Auth Module" in prompt
        assert "Build authentication" in prompt

    def test_prompt_includes_project_path(self):
        prompt = _build_worker_prompt("Task", "Desc", Path("/my/project"))
        assert "/my/project" in prompt

    def test_prompt_includes_rules(self):
        prompt = _build_worker_prompt("Task", "Desc", Path("/test"))
        assert "Stay in scope" in prompt
        assert "Document everything" in prompt


class TestWorkerManager:
    def test_init(self, wm: WorkerManager):
        assert wm.workers == {}
        assert wm.sm is not None

    def test_get_status_empty(self, wm: WorkerManager):
        assert wm.get_status() == []

    @pytest.mark.asyncio
    async def test_launch_available_no_ready_packets(self, wm: WorkerManager):
        launched = await wm.launch_available()
        assert launched == []

    @pytest.mark.asyncio
    async def test_launch_available_respects_max_workers(self, sm: StateManager):
        sm.add_packet("P1")
        sm.add_packet("P2")
        sm.add_packet("P3")

        wm = WorkerManager(sm)

        # Mock subprocess creation to avoid actually launching claude
        mock_process = AsyncMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        mock_process.stdout = AsyncMock()
        mock_process.stdout.at_eof.return_value = True
        mock_process.stdout.readline = AsyncMock(return_value=b"")
        mock_process.wait = AsyncMock(return_value=0)
        mock_process.terminate = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            launched = await wm.launch_available(max_workers=2)

        assert len(launched) == 2

    @pytest.mark.asyncio
    async def test_stop_all_terminates_processes(self, sm: StateManager):
        sm.add_packet("P1")
        wm = WorkerManager(sm)

        mock_process = AsyncMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        mock_process.stdout = AsyncMock()
        mock_process.stdout.at_eof.return_value = True
        mock_process.stdout.readline = AsyncMock(return_value=b"")
        mock_process.wait = AsyncMock(return_value=0)
        mock_process.terminate = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            launched = await wm.launch_available(max_workers=1)

        # Force process to look running
        mock_process.returncode = None
        for wp in wm.workers.values():
            wp.process = mock_process

        await wm.stop_all()
        mock_process.terminate.assert_called()
