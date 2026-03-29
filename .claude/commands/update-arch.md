---
name: update-arch
description: Update the living architecture document to reflect current project state.
---

# Update Architecture Document

## Step 1 — Read Current State

Read `docs/architecture.json` to understand the current documented architecture.

## Step 2 — Identify Changes

Review recent work (check `docs/build-log.md` and recent file changes) to identify any architectural changes:

- **New components** added to the project
- **Removed components** no longer in use
- **Changed components** (renamed, moved, restructured)
- **New data flows** between components
- **New external services** or integrations
- **Infrastructure changes** (new storage, new runtime, etc.)
- **Dependency changes** between components

## Step 3 — Update the JSON

Update `docs/architecture.json` with all changes found:

- Add new components with correct id, name, description, type, path, status, and dependencies
- Remove components that no longer exist
- Update statuses (planned -> building -> live)
- Add new data flows
- Add new external services
- Update the `lastUpdated` field in the project section
- Add a changelog entry describing what changed and which packet caused it

## Step 4 — Verify

Read the updated `docs/architecture.json` back and confirm:
- All current components are represented
- All data flows are accurate
- Statuses reflect reality
- No orphaned references (flows pointing to deleted components)

## Step 5 — Report

Tell the user what was updated in plain language:
- Components added/removed/changed
- Data flows added/removed
- New external services
- The changelog entry that was added

Remind the user they can open `docs/architecture.html` in their browser to see the updated visual architecture.
