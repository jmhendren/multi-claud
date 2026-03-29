"""FastAPI dashboard server with SSE for real-time state updates."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from sse_starlette.sse import EventSourceResponse

from multi_claud.risk import RiskDetector
from multi_claud.state import StateManager


DASHBOARD_HTML = Path(__file__).parent.parent / "dashboard" / "index.html"

# Track scan interval to avoid scanning on every SSE tick
_RISK_SCAN_INTERVAL = 10  # seconds


def create_app(sm: StateManager) -> FastAPI:
    """Create a FastAPI app wired to the given StateManager."""

    app = FastAPI(title="Multi-Claud Dashboard")
    risk_detector = RiskDetector(sm)

    @app.get("/", response_class=HTMLResponse)
    async def root() -> FileResponse:
        """Serve the dashboard HTML."""
        return FileResponse(DASHBOARD_HTML, media_type="text/html")

    @app.get("/api/state")
    async def get_state() -> dict:
        """Get the current project state as JSON."""
        state = sm.load()
        return json.loads(state.model_dump_json())

    @app.post("/api/scan")
    async def run_scan() -> dict:
        """Trigger a risk scan and return new risks."""
        new_risks = risk_detector.scan_all()
        return {
            "new_risks": len(new_risks),
            "risks": [json.loads(r.model_dump_json()) for r in new_risks],
        }

    @app.get("/api/events")
    async def events(request: Request) -> EventSourceResponse:
        """Stream state updates via Server-Sent Events."""

        async def event_generator():
            last_hash = None
            ticks_since_scan = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Periodically run risk scan
                    ticks_since_scan += 1
                    if ticks_since_scan >= _RISK_SCAN_INTERVAL:
                        ticks_since_scan = 0
                        risk_detector.scan_all()

                    state = sm.load()
                    state_json = state.model_dump_json()
                    current_hash = hash(state_json)
                    if current_hash != last_hash:
                        last_hash = current_hash
                        yield {"event": "state", "data": state_json}
                except FileNotFoundError:
                    yield {"event": "error", "data": json.dumps({"error": "State file not found"})}
                    break
                await asyncio.sleep(1)

        return EventSourceResponse(event_generator())

    return app
