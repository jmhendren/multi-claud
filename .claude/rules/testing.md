---
description: Testing conventions for Multi-Claud
globs: "tests/*.py"
---

# Testing Rules

- Use `pytest` and `pytest-asyncio` for all tests.
- Test files mirror the source: `multi_claud/state.py` → `tests/test_state.py`.
- Use `tmp_path` fixture for tests that create files (state files, worktrees).
- Mock the Claude Agent SDK and Claude CLI subprocess calls — don't make real API calls in tests.
- Use `httpx.AsyncClient` with FastAPI's `TestClient` for dashboard endpoint tests.
- Every public function in the state module MUST have tests — it's the foundation.
- Test both success and error cases. Test file locking behavior with concurrent access.
- Use descriptive test names: `test_create_packet_sets_pending_status`, not `test_create`.
