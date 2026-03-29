---
description: Python code style conventions for Multi-Claud
globs: "*.py"
---

# Python Code Style

- Use `pathlib.Path` for all file paths. Never use string concatenation for paths.
- Use type hints on all function signatures.
- Use Pydantic `BaseModel` for data models (state, packets, workers, risks).
- Use `async`/`await` for I/O operations (file reads, subprocess, HTTP).
- Use `filelock.FileLock` when writing to the shared state file.
- Imports: stdlib first, then third-party, then local. One blank line between groups.
- Use f-strings for string formatting.
- Prefer `logging` over `print` for operational output. Use `rich` for user-facing CLI output.
- All public functions need a one-line docstring. Skip docstrings for private helpers unless logic is non-obvious.
