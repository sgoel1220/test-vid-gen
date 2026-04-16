# Content Pipeline Implementation - Beads Overview

> Generated: 2026-04-16
> Epic: `Chatterbox-TTS-Server-fme`

## Quick Reference

| Phase | Stories | Est. Duration | Status |
|-------|---------|---------------|--------|
| **Phase 1**: Service Scaffold | 5 | 1 week | Ready |
| **Phase 2**: Hatchet + GPU Provider | 6 | 1-2 weeks | Blocked |
| **Phase 3**: Content Pipeline | 8 | 1-2 weeks | Blocked |
| **Phase 4**: Web UI | 7 | 1-2 weeks | Blocked |
| **Phase 5**: Production Hardening | 7 | 1 week | Blocked |
| **Total** | 33 tasks | ~5-7 weeks | |

---

## Phase 1: Service Scaffold & Migration

**Goal**: Create creepy-brain service with existing functionality.

| ID | Title | Depends On | Priority |
|----|-------|------------|----------|
| `4sz` | [P1] Create creepy-brain service scaffold | - | P1 |
| `4me` | [P1] Set up SQLAlchemy models and Alembic migrations | 4sz | P1 |
| `d7c` | [P1] Migrate metadata-server code | 4me | P1 |
| `nzd` | [P1] Migrate story-engine code | 4me | P1 |
| `u2o` | [P1] Verify feature parity with existing services | d7c, nzd | P1 |

**Start with**: `4sz` - Create creepy-brain service scaffold

---

## Phase 2: Hatchet Integration & GPU Provider

**Goal**: Add workflow orchestration with Hatchet and GPU provider abstraction.

| ID | Title | Depends On | Priority |
|----|-------|------------|----------|
| `104` | [P2] Add Hatchet engine to Docker Compose | u2o | P1 |
| `9r1` | [P2] Install hatchet-sdk and configure worker | 104 | P1 |
| `x7k` | [P2] Create GPU provider abstraction (base class) | u2o | P1 |
| `8dn` | [P2] Implement RunPod GPU provider | x7k | P1 |
| `w4n` | [P2] Implement Local GPU provider (dev mode) | x7k | P1 |
| `vxb` | [P2] Create simple test workflow | 9r1 | P1 |

**Parallel tracks**:
- Hatchet: `104` → `9r1` → `vxb`
- GPU: `x7k` → `8dn`, `w4n`

---

## Phase 3: Content Pipeline Workflow

**Goal**: Implement the full content pipeline with all steps.

| ID | Title | Depends On | Priority |
|----|-------|------------|----------|
| `cmz` | [P3] Create ContentPipeline workflow definition | vxb, w4n | P1 |
| `bjx` | [P3] Implement generate_story step | cmz | P1 |
| `duy` | [P3] Implement tts_synthesis step with polling | bjx | P1 |
| `83y` | [P3] Implement image_generation step | duy | P1 |
| `ea6` | [P3] Implement stitch_final step | 83y | P1 |
| `z9p` | [P3] Add on_failure cleanup hook | cmz | P1 |
| `lm4` | [P3] Create recon cron job for orphaned pods | cmz | P1 |
| `4yj` | [P3] Add workflow API endpoints | ea6 | P1 |

**Critical path**: `cmz` → `bjx` → `duy` → `83y` → `ea6` → `4yj`

---

## Phase 4: Web UI Dashboard

**Goal**: Build user-facing dashboard for monitoring and control.

| ID | Title | Depends On | Priority |
|----|-------|------------|----------|
| `g1n` | [P4] Set up Next.js + shadcn scaffold | 4yj | P2 |
| `46d` | [P4] Create dashboard home page (workflow list) | g1n | P2 |
| `8zk` | [P4] Create workflow detail page | 46d | P2 |
| `at3` | [P4] Create GPU pods page (cost tracking) | g1n | P2 |
| `mxm` | [P4] Add SWR polling for live updates | 46d | P2 |
| `m78` | [P4] Create settings page | g1n | P2 |
| `ysz` | [P4] Create new workflow dialog | 46d | P2 |

**Parallel tracks**:
- Main flow: `g1n` → `46d` → `8zk`, `mxm`, `ysz`
- Side pages: `g1n` → `at3`, `m78`

---

## Phase 5: Production Hardening

**Goal**: Make the system production-ready.

| ID | Title | Depends On | Priority |
|----|-------|------------|----------|
| `1o1` | [P5] Add structured logging with structlog | 4sz | P2 |
| `7tt` | [P5] Add Prometheus metrics endpoint | 4sz | P2 |
| `d72` | [P5] Configure Slack/Discord alerts | 4sz | P2 |
| `vqu` | [P5] Add workflow-level timeout | cmz | P2 |
| `8gi` | [P5] Implement cost tracking and alerts | 4yj | P2 |
| `9pe` | [P5] Write comprehensive tests | 4yj | P2 |
| `dyy` | [P5] Write documentation and runbooks | 9pe | P2 |

**Note**: Some P5 tasks can be done in parallel with earlier phases (logging, metrics, alerts).

---

## Dependency Graph

```
Phase 1 (Foundation)
┌──────┐
│ 4sz  │ Create scaffold
└──┬───┘
   │
┌──▼───┐
│ 4me  │ SQLAlchemy models
└──┬───┘
   │
┌──▼───┐────────┐
│ d7c  │        │ nzd  │ Migrate code
└──┬───┘        └──┬───┘
   │               │
   └───────┬───────┘
           │
       ┌───▼───┐
       │ u2o   │ Verify parity
       └───┬───┘
           │
Phase 2 (Hatchet + GPU)
   ┌───────┼───────┐
   │       │       │
┌──▼──┐ ┌──▼──┐    │
│ 104 │ │ x7k │    │
└──┬──┘ └──┬──┘    │
   │       │       │
┌──▼──┐ ┌──┴──┐────┤
│ 9r1 │ │ 8dn │ w4n│
└──┬──┘ └─────┘──┬─┘
   │             │
┌──▼──┐          │
│ vxb │──────────┘
└──┬──┘
   │
Phase 3 (Workflow)
┌──▼──┐
│ cmz │ Content pipeline definition
└──┬──┘
   │
┌──▼──┐
│ bjx │ Story step
└──┬──┘
   │
┌──▼──┐
│ duy │ TTS step
└──┬──┘
   │
┌──▼──┐
│ 83y │ Image step
└──┬──┘
   │
┌──▼──┐
│ ea6 │ Stitch step
└──┬──┘
   │
┌──▼──┐
│ 4yj │ API endpoints
└──┬──┘
   │
Phase 4 (UI)
┌──▼──┐
│ g1n │ Next.js scaffold
└──┬──┘
   │
┌──▼──┐
│ 46d │ Dashboard
└──┬──┘
   │
   └─────► More UI pages...
```

---

## How to Pick Up Work

1. Run `bd ready` to see available tasks
2. Claim a task: `bd claim <issue-id>`
3. Work on the task
4. Close when done: `bd close <issue-id>`

### First Task to Start

```bash
bd claim Chatterbox-TTS-Server-4sz
```

This creates the `services/creepy-brain/` folder scaffold.

---

## Key Documents

- **Full Plan**: `docs/CONTENT_PIPELINE_ORCHESTRATION.md`
- **This File**: `docs/IMPLEMENTATION_BEADS.md`
- **Project Instructions**: `AGENTS.md`

---

## Notes

- All work goes in `services/creepy-brain/` (NEW folder)
- Original services remain unchanged until migration is verified
- Each story has acceptance criteria in the description
- Phase 5 tasks (logging, metrics) can start early once scaffold exists
