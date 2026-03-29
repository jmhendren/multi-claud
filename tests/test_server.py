"""Tests for the dashboard server."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from multi_claud.server import create_app
from multi_claud.state import StateManager


@pytest.fixture
def sm(tmp_path: Path) -> StateManager:
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    sm = StateManager(project_dir)
    sm.init("Test Project", "A test project for dashboard")
    return sm


@pytest.fixture
def app(sm: StateManager):
    return create_app(sm)


@pytest.fixture
async def client(app) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_root_returns_html(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Multi-Claud" in resp.text


@pytest.mark.asyncio
async def test_api_state_returns_json(client: AsyncClient):
    resp = await client.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project"]["name"] == "Test Project"
    assert "packets" in data
    assert "workers" in data
    assert "risks" in data


@pytest.mark.asyncio
async def test_api_state_reflects_changes(client: AsyncClient, sm: StateManager):
    sm.add_packet("New Packet")
    resp = await client.get("/api/state")
    data = resp.json()
    assert len(data["packets"]) == 1
    assert data["packets"][0]["name"] == "New Packet"


# Note: SSE streaming endpoint (/api/events) is tested manually rather than
# in unit tests because the persistent connection doesn't terminate cleanly
# in httpx's async test client. Verified working via browser + curl.
