---
name: audit-packet
description: Review the current packet's work with extended thinking. Find issues, fix them, document everything.
---

# Audit Current Packet

You are auditing the work just completed. Be thorough and critical.

## Step 1 — Identify What Was Built

Read `BUILD_PLAN.md` to find the current in-progress packet. Read all files that were created or modified as part of this packet.

## Step 2 — Deep Review (Use Extended Thinking)

Use ultrathink/extended thinking to critically review every change:

- **Correctness:** Does the code do what the packet specified? Are there logic errors?
- **Completeness:** Is everything the packet required actually built? Is anything missing?
- **Wired in:** Is everything connected and working? If not, why not?
- **Tests:** Do tests exist for the new code? Do they pass?
- **Integration:** Does the new code work with existing code? Any conflicts?
- **Error handling:** Are errors handled properly? Can things fail silently?
- **Documentation:** Is the code understandable? Are there comments where the logic is non-obvious?

## Step 3 — Run Tests

Run the full test suite. If ANYTHING fails — even pre-existing failures — fix it now.

## Step 4 — Fix Issues

For every issue found:
1. Fix it
2. Run tests again to verify the fix
3. Keep a list of what you fixed and why

## Step 5 — Document

Add an entry to `docs/build-log.md` with:
- What was built (with full file paths)
- What the audit found
- What was fixed and why
- Whether the architecture changed

## Step 6 — Architecture Check

If any of the work in this packet added, removed, or changed components, dependencies, data flows, or structural elements — update `docs/architecture.json` now.

## Step 7 — Report

Tell the user:
- Summary of what was audited
- Issues found and fixed (or "No issues found")
- Whether architecture was updated
- Whether all tests pass
