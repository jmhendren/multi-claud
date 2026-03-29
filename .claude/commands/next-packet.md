---
name: next-packet
description: Get a briefing on the next packet to build from the build plan.
---

# Next Packet Briefing

## Step 1 — Read the Build Plan

Read `BUILD_PLAN.md` and identify all packets and their statuses.

## Step 2 — Find the Next Packet

Find the first packet with status "Not Started" whose dependencies are all marked "Complete".

If no packets are available (all complete, or remaining packets are blocked by incomplete dependencies), tell the user.

## Step 3 — Read Context

- Read `CLAUDE.md` for project conventions and feedback loops
- Read `docs/architecture.json` for current architecture state
- Read `docs/build-log.md` for recent work history (last 2-3 entries)
- Read any files that the next packet will build on or modify

## Step 4 — Present the Briefing

Give the user a clear briefing:

```
## Next Up: Packet [N] — [Name]

**What we're building:** [Plain language description]

**What already exists that this builds on:**
- [List of existing components/files this packet touches]

**What this packet will create/change:**
- [List of expected deliverables]

**How we'll verify it works:**
- [Feedback loops / tests specific to this packet]

**Dependencies satisfied:**
- [List of completed packets this depends on]
```

## Step 5 — Ask to Proceed

Ask: "Ready to start this packet? If anything about the scope needs adjusting, let me know."

If the user says yes, update the packet status in `BUILD_PLAN.md` to "In Progress" and begin building.
