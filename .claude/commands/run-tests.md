---
name: run-tests
description: Run the full test suite and fix any failures immediately.
---

# Run Tests

## Step 1 — Identify Test Command

Run: `python -m pytest tests/ -v`

## Step 2 — Run All Tests

Execute the full test suite. Capture all output.

## Step 3 — Analyze Results

- How many tests passed?
- How many tests failed?
- How many tests were skipped?

## Step 4 — Fix ALL Failures

If ANY tests fail — even pre-existing failures unrelated to current work:

1. Read the failure output carefully
2. Identify the root cause
3. Fix it
4. Run the tests again
5. Repeat until all tests pass

Do NOT skip failures. Do NOT mark them as "known issues." Fix them.

## Step 5 — Report

Tell the user:
- Total tests: [N]
- Passed: [N]
- Fixed: [N] (list what was broken and how you fixed it)
- All passing: Yes/No

If you fixed pre-existing failures, note that in `docs/build-log.md` as well.
