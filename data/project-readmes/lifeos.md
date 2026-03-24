# LifeOS / LifeTrack — Project Overview

> **Phase 0: Foundation & Architecture — IN PROGRESS** (started 2026-03-24)

---

## Vision

LifeTrack is Adam's highest emotional priority project — an **AI-powered self-discovery platform** built on the principle that purpose is intrinsic and starts in the mind. The system creates a "Digital Twin" psychological model of the user using a multi-framework personality synthesis engine.

**The three-product ecosystem:**

| Product | Role | Status |
|---------|------|--------|
| **LifeOS** | Open-source orchestration layer — the "operating system for life" | Prototype (Aug 2025) |
| **LifeTrack** | AI coaching plugin — Digital Twin, fear mapping, assessments | Dormant (Aug 2025, 8 commits) |
| **LifeStack** | Modular micro-apps — habit tracker, journal, personal CRM | Dormant (Jun 2025, 17 commits) |

---

## LifeTrack — Codebase Audit (TASK-008)

**Tech stack:** Flutter/Dart · Firebase (Auth, Firestore, Storage) · Claude API · Provider state management

### Three Pillars (Mind / Body / Environment)
The core architecture organizes self-discovery across three domains:
- **Mind** — psychological frameworks, beliefs, fears, purpose
- **Body** — health, fitness, energy, physical optimization
- **Environment** — relationships, work, systems, context

### Psychological Framework Engine
LifeTrack synthesizes **five personality frameworks** into a unified Digital Twin:

| Framework | Data Points | LifeTrack Use |
|-----------|-------------|---------------|
| **Human Design** | Type, Authority, Profile, Centers | Core operating system |
| **MBTI** | 16 types | Cognitive function mapping |
| **Big Five (OCEAN)** | 5 dimensions | Trait baseline |
| **Enneagram** | 9 types + wings | Core motivation & vice mapping |
| **VIA Character Strengths** | 24 strengths | Positive psychology anchor |

### Key Features (Implemented vs Placeholder)
- ✅ **Onboarding flow** — framework assessments (Human Design, MBTI, etc.)
- ✅ **Firebase Auth** — Google Sign-In, user accounts
- ✅ **Claude AI integration** — coaching prompts via Anthropic API
- ⚠️ **Fear Mapping Tool** — UI exists, AI logic is placeholder
- ⚠️ **Digital Twin model** — data structure exists, synthesis algorithm not complete
- ⚠️ **Progress tracking** — schema designed, persistence incomplete
- ❌ **Wearable integration (LifeTrack Compass)** — future roadmap only

### Proprietary Algorithm Components
The core IP is the **cross-framework synthesis engine** — mapping a user's scores across all 5 frameworks into a coherent psychological profile. This is partially implemented with placeholder logic that needs to be completed.

---

## AevaOS Components Audit (TASK-009)

### Mission Control UI (Next.js / Vercel)
- **Stack:** Next.js 16.1.7 · TypeScript · Tailwind CSS v4 · React 19
- **Pages:** Agents, Analytics, Blockers, Briefing, Credits, Dispatch, Ideas, Login, Projects, Search, Tasks
- **Deployment:** Vercel (auto-deploys on `main` push)
- **Status:** ✅ Live and working — recently received full design system overhaul (glassmorphism, CSS custom properties, animations)
- **Auth:** JWT token in localStorage + cookie, `/api/login` endpoint, Next.js middleware redirect

### Mission Control API (Flask / Railway)
- **Stack:** Python · Flask · SQLAlchemy · PostgreSQL (Railway)
- **Storage:** Dual-mode — PostgreSQL DB (production) + JSON files (fallback/dev)
- **Endpoints:** 25+ routes covering tasks, ideas, projects, agents, dispatch, blockers, briefings, activity feed, credits, meeting room
- **Deployment:** Railway (auto-deploys on GitHub push via `railway.json`)
- **Status:** ✅ Live. PostgreSQL DB active on Railway.

### Data Structures

```
tasks.json       → Tasks (TASK-001 to TASK-016+)
ideas.json       → Captured ideas
projects.json    → Project definitions (6 projects)
agents-registry.json → Aeva, Clara, Pixel, Sage definitions
credit-status.json   → API credit balances
activity-feed.jsonl  → Streaming agent activity log
dispatches.jsonl     → AI dispatch history
```

### Current Gaps (Found in Audit)
1. **No task→project dependency graph** — blockers stored as text, not enforced
2. **tasks.json split** — lifeos tasks were in a separate key (fixed 2026-03-24)
3. **No README/spec per project** — just added via README tab on project pages
4. **No real-time collaboration** — SSE exists for activity feed only

---

## Phase 0 Roadmap

```
TASK-007 [EPIC]     Phase 0: Foundation & Architecture    ← IN PROGRESS
├── TASK-008        Audit LifeTrack codebase               ← IN PROGRESS
├── TASK-009        Audit AevaOS components                ← IN PROGRESS (done ↑)
├── TASK-010        Unified data model spec                ← BLOCKED on 008/009
├── TASK-011        LifeOS plugin architecture             ← BLOCKED on 010
├── TASK-012        LifeTrack as LifeOS plugin             ← BLOCKED on 011
├── TASK-013        Architecture diagrams + tech spec      ← BLOCKED on 010/011
├── TASK-014        Set up LifeOS repo structure           ← BLOCKED on 013
├── TASK-015        Migrate API Flask → Node.js/TS         ← LOW PRIORITY
└── TASK-016        Viktor Frankl / Logotherapy research   ← BACKLOG
```

---

## Design Decisions (Pending)

1. **LifeOS licensing** — MIT (open-source core) or source-available?
2. **Plugin interface** — REST API or event-driven (WebSocket/SSE)?
3. **Data residency** — user psychological data in Firebase or migrate to PostgreSQL?
4. **LifeTrack monetization** — freemium SaaS plugin or one-time purchase?
5. **Wearable roadmap** — when does LifeTrack Compass enter scope?

---

## Stack Decisions (Proposed)

| Layer | Current | Target |
|-------|---------|--------|
| LifeTrack mobile | Flutter + Firebase | Flutter + LifeOS API |
| LifeOS core | Hono/Node + SQLite | Node.js/TS + PostgreSQL |
| Mission Control API | Flask + PostgreSQL | Node.js/TS + PostgreSQL (TASK-015) |
| Auth | Firebase Auth | Firebase Auth → JWT bridge |
| AI coaching | Claude API direct | Via AevaOS dispatch mesh |

---

*Last updated: 2026-03-24 by Antigravity · Phase 0 active*
