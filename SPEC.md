# Bounty Factory - Specification

## 1. Project Overview

**Project Name:** Bounty Factory
**Type:** Autonomous Bounty Hunting System
**Core Functionality:** Automatically discover open bounties from Algora.io and GitHub issues, classify difficulty, assign to appropriate agents (local LLM via Podman sandbox), validate fixes, run code review, and queue for human review before PR submission.
**Target Users:** Developers seeking passive income from bug bounties

## 2. System Architecture

### 2.1 Components

```
┌─────────────────────────────────────────────────────────────────┐
│                    BOUNTY FACTORY ORCHESTRATOR                  │
│                     (Main Python Process)                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │  GitHub      │  │  Algora tRPC │  │  Manual      │          │
│  │  Scout       │  │  Client      │  │  Scan        │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│         └─────────────────┼─────────────────┘                   │
│                           ▼                                     │
│                    ┌──────────────┐                             │
│                    │  Database    │                             │
│                    │  (SQLite)    │                             │
│                    └──────┬───────┘                             │
│                           ▼                                     │
│                    ┌──────────────┐                             │
│                    │  Task        │                             │
│                    │  Processor   │                             │
│                    │  (Queue)     │                             │
│                    └──────┬───────┘                             │
│                           ▼                                     │
│              ┌────────────────────────┐                         │
│              │     Task Classifier    │                         │
│              │   (qwen2.5:0.5b)       │                         │
│              └───────────┬────────────┘                         │
│                          ▼                                      │
│            ┌─────────────┴─────────────┐                        │
│            ▼                           ▼                        │
│   ┌────────────────┐        ┌──────────────────┐                │
│   │  Simple Agent  │        │  Complex Agent   │                │
│   │ (Podman sandbox│        │ (Podman sandbox  │                │
│   │  + qwen-coder) │        │  + decomposer)   │                │
│   └────────┬───────┘        └────────┬─────────┘                │
│            └────────────┬───────────┘                           │
│                         ▼                                       │
│                ┌────────────────┐                               │
│                │  Pre-check     │                               │
│                │  (GitHub API)  │                               │
│                └────────┬───────┘                               │
│                         ▼                                       │
│                ┌────────────────┐                               │
│                │ Repo Mapper    │                               │
│                │ Test Runner    │                               │
│                └────────┬───────┘                               │
│                         ▼                                       │
│                ┌────────────────┐                               │
│                │ Code Review    │                               │
│                │ Agent          │                               │
│                └────────┬───────┘                               │
│                         ▼                                       │
│                ┌────────────────┐                               │
│                │Human Review    │◀── Web UI                     │
│                │    Queue       │                               │
│                └────────┬───────┘                               │
│                         ▼                                       │
│                ┌────────────────┐                               │
│                │PR Creator      │                               │
│                │(After approve) │                               │
│                └────────────────┘                               │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 LangGraph Workflow

```
 START
   │
   ▼
 ┌──────────────┐
 │  Precheck    │ ← GitHub issue validation
 └──────┬───────┘
        │
        ▼
 ┌──────────────┐
 │  Dispatcher  │ ← classify (simple vs complex)
 │              │    decompose into subtasks (complex only)
 └──────┬───────┘
        │
   ┌────┴────┐
   ▼         ▼
 ┌──────┐  ┌──────────┐
 │Simple│  │ Complex  │
 │(sand│  │(single   │
 │box)  │  │ clone +  │
 │      │  │ branch/  │
 │      │  │ subtask) │
 └──┬───┘  └──┬───────┘
    └────┬────┘
         ▼
 ┌──────────────┐
 │ CI/CD        │ ← Gatekeeper: merge branches one
 │ Specialist   │   by one, test after each merge,
 │              │   then LLM review + test-fix loop
 └──────┬───────┘
    ┌────┴────┐
    ▼         ▼
 ┌─────────┐  ┌──────────┐
 │ Enqueue │  │ Retry    │ ← back to coder
 │ Review  │  │ (max_    │    with context
 └────┬────┘  │ send_back│
      │       └──────────┘
      ▼
    END (queued_for_review / failed)
```

### 2.3 Hybrid Sandbox Architecture

The factory uses a **Hybrid Sandbox Architecture** — a single shared Git workspace with
isolated feature branches per agent, gated by a CI/CD Gatekeeper.

```
┌──────────────────────────────────────────────────────────┐
│  HOST (macOS) — Single Workspace per Bounty              │
│                                                          │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Shared Clone  ../bounty_workspaces/bounty_{id}/    │ │
│  │                                                     │ │
│  │  ┌───────────────────────────────────────────────┐  │ │
│  │  │  main (or bounty-fix-{id})                    │  │ │
│  │  │   ▲        ▲        ▲                         │  │ │
│  │  │   │merge   │merge   │merge                    │  │ │
│  │  │   │        │        │                         │  │ │
│  │  │  ┌─┴──┐  ┌─┴──┐  ┌─┴──┐                      │  │ │
│  │  │  │sub │  │sub │  │sub │                      │  │ │
│  │  │  │   1│  │   2│  │   3│                      │  │ │
│  │  │  └────┘  └────┘  └────┘                      │  │ │
│  │  │   coder    coder    coder                     │  │ │
│  │  └───────────────────────────────────────────────┘  │ │
│  │                                                     │ │
│  │  CI/CD Gatekeeper:                                  │ │
│  │    1. merge branch → run tests                      │ │
│  │    2. if fail → drop branch (git reset --hard)      │ │
│  │    3. if pass → keep merge, next branch             │ │
│  │    4. then run LLM review + test-fix cycles         │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌───────────────────────────────────────────────────┐   │
│  │  Podman Container (isolated validation)           │   │
│  │  - Runs install + test commands                   │   │
│  │  - 2 CPU, 2GB RAM, no network                     │   │
│  │  - NO git access, receives workspace via podman cp │   │
│  └───────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

### 2.4 Agent Decision Tree

```
Bounty Task
     │
     ▼
┌──────────────────┐
│   Dispatcher     │
│ classifies +     │
│ decomposes       │
│ (single LLM call)│
└──────┬───────────┘
       │
  ┌────┴────┐
  │         │
 Simple   Complex
  │         │
  ▼         ▼
┌───────┐ ┌───────────────────────────┐
│Simple │ │ Shared Clone + Branches   │
│Agent  │ │ (coder_node orchestrates) │
│(sand- │ │                           │
│box)   │ │ subtask_1 ─ simple_coder  │
│       │ │ subtask_2 ─ super_coder   │
│       │ │ subtask_3 ─ simple_coder  │
└───┬───┘ └───────────┬───────────────┘
    │                 │
    └────────┬────────┘
             ▼
┌────────────────────┐
│  CI/CD Gatekeeper  │
│  merge branch 1    │
│  → run tests       │
│  → if pass, keep   │
│  merge branch 2    │
│  → run tests       │
│  → if fail, drop   │
│  ...then LLM review│
│  + test-fix loop   │
└────────┬───────────┘
         │
    ┌────┴────┐
    ▼         ▼
 Enqueue   Retry to
 Review    coder (if
           max_send_back
           not exceeded)
```

## 3. Functionality Specification

### 3.1 Bounty Discovery

- **Algora tRPC Client** - Fetches bounties via public tRPC endpoint (no API key needed)
- **GitHub Scout** - Searches GitHub issues with labels: `good first issue`, `help wanted`, `bug bounty`, `bounty`
- **Price Extraction** - Regex parsing of issue titles/descriptions for bounty amounts
- **Deduplication** - SQLite prevents duplicate entries
- **Auto-cleanup** - Removes untouched tasks after 30 days

### 3.2 LangGraph Orchestration

- **StateGraph** - TypedDict-based state (`BountyState`) with 7 nodes
- **Conditional Edges** - Routes based on classification (simple → simple_agent, complex → complex_agent)
- **MemorySaver** - In-memory checkpoint for graph state
- **Nodes**: precheck, classify, simple_agent, complex_agent, validate, review, enqueue_review

### 3.3 Task Classifier Module

- **Model:** Ollama `qwen2.5:0.5b` (lightweight)
- **Decision Criteria:**
  - Lines of code to change
  - Number of files affected
  - Complexity of issue description
- **Output:** Classification (simple/complex) + confidence score
- **Difficulty Levels:** 3 tiers × 3 sub-levels (Easy 1-3, Medium 1-3, Hard 1-3)

### 3.4 Simple Task Agent (Sandbox)

- **Model:** `qwen2.5-coder:7b-instruct-q4_K_M`
- **Purpose:** Single-file fixes, typos, small bugs
- **Capabilities:**
  - `sandbox.run_sandbox_task()` clones repo, generates fix via host Ollama, applies files, commits, validates in Podman container
  - Tracks token usage and duration
  - Container validation: install + test inside isolated Podman container

### 3.5 Complex Task Agent (Shared Workspace + Branches)

- **Role:** `coder_node` in `nodes.py`
- **Purpose:** Multi-file changes, architectural issues across multiple subtasks
- **Architecture:** Hybrid Sandbox — single shared clone with isolated Git branches
- **Flow:**
  1. `coder_node` clones the repository **once**
  2. Subtasks are sorted by `depends_on` (topological order)
  3. For each subtask, a dedicated branch is created: `bounty-fix-{id}-sub-{n}`
  4. Appropriate coder (simple_coder or super_coder) runs on that branch
  5. If a subtask fails, its branch is dropped (`git branch -D`)
  6. Successful subtask branches are recorded in state
- **Subtask model:**
  - `id` — unique subtask identifier
  - `description` — natural language instructions
  - `role` — `simple_coder` or `super_coder`
  - `depends_on` — list of subtask IDs that must complete first
  - `estimated_complexity` — human-readable estimate

### 3.6 Pre-check System

- **GitHub Checker** - Validates issue before processing:
  - Checks if issue is assigned
  - Detects recent claims (within 24h)
  - Fetches and parses CONTRIBUTING.md
- **Comment Generator** - Creates suggested GitHub comment for claiming

### 3.7 Repo Mapper

- **Auto-detection:** Language, framework, package manager, test/lint commands
- **Supported:** JavaScript/TypeScript, Python, Go, Rust, C#
- **Framework Detection:** Next.js, React, Vue, SvelteKit, Express, Fastify
- **Package Manager:** npm, pnpm, yarn, bun

### 3.8 Test Runner

- **Validation Pipeline:** Install → Test → Lint
- **ANSI Stripping** - Clean terminal output for parsing
- **Failure Extraction** - Identifies error/failed/expect/exception lines
- **Execution Report** - Saves `execution_report.json` in workspace

### 3.9 CI/CD Gatekeeper (Branch Merge + Validation)

Before the LLM review, the CI/CD Specialist acts as a **Gatekeeper** to merge and validate subtask branches:

1. **Checkout** the base branch
2. For each subtask branch (in order):
   - `git merge --no-edit <branch>`
   - If merge conflict → `git merge --abort`, drop branch
   - Run `test_runner.validate_fix()` on merged result
   - If tests fail → `git reset --hard HEAD~1`, drop branch
   - If pass → keep merge, proceed to next branch
3. If **all** branches dropped → return error
4. Update `diff_content` to reflect merged state
5. Proceed with LLM review + test-fix cycles on the fully merged result

### 3.10 Code Review Agent

- **Model:** `qwen2.5-coder:7b-instruct-q4_K_M`
- **Checks:** Syntax correctness, style consistency, edge cases, security
- **Output:** Score (0-100), approval status, notes

### 3.10 Human Review Queue

- **Storage:** SQLite database
- **Web UI Features:**
  - Diff viewer with syntax highlighting
  - Model stats (tokens, duration, tokens/sec)
  - Suggested comment preview
  - "Show in Finder" button to inspect workspace
  - Approve / Reject / Skip actions
  - Full processing logs with stats panel

### 3.11 PR Creator

- **Trigger:** Manual approval from human review
- **Actions:**
  - Push branch to remote
  - Create PR with description linking to bounty
  - Update database with PR URL

### 3.12 Task Processor

- **Async Queue** - Background thread processes tasks one at a time
- **Progress Tracking** - Real-time status updates (queued → processing → complete/error)
- **In-memory Logs** - Live log streaming to web UI
- **DB Persistence** - All logs saved to SQLite
- **Graceful Shutdown** - Waits up to 60s for current task, then abandons thread

### 3.13 Sandbox Container Registry

- Tracks running containers by bounty ID
- On shutdown: terminates all running containers (SIGTERM → 10s → SIGKILL)
- Resets interrupted bounties back to `new` status

## 4. Technical Stack

- **Language:** Python 3.11+
- **Local LLM:** Ollama (`qwen2.5:0.5b` classifier, `qwen2.5-coder:7b-instruct-q4_K_M` agent/reviewer)
- **Orchestration:** LangGraph (StateGraph, MemorySaver, conditional edges)
- **Structured Output:** LangChain `with_structured_output()` + Pydantic models
- **Sandbox:** Podman (rootless), container limits: 2 CPU, 2GB RAM
- **Database:** SQLite (bounties, review queue, processing logs)
- **Web UI:** Flask + Tailwind CSS + Font Awesome
- **API Client:** requests (standard library)
- **Config:** YAML via pyyaml

## 5. Configuration

All config via `config/config.yaml`:

| Section | Key | Description |
|---------|-----|-------------|
| `test_mode` | `github_queries` | Default GitHub query set used by scan when no custom labels are selected |
| `test_mode` | `skip_paid` | For free scans, drop paid/bounty-tagged items from GitHub results |
| `scan` (UI behavior) | `Free/Paid toggle` | Free sets `0-0` price range; Paid defaults to `5-150` and includes Algora + paid-label discovery |
| `ollama` | `base_url` | Ollama API endpoint |
| `ollama` | `models` | Model per task (classifier, simple_agent, code_reviewer) |
| `git` | `username` | GitHub username |
| `git` | `token` | GitHub personal access token |
| `workspace` | `base_path` | Workspace directory (relative to project root) |

## 6. Data Flow

1. **Scan** — Fetch from Algora tRPC + GitHub Scout → store in SQLite
2. **Process** — User clicks "Process" → precheck → dispatcher → route to coder
3. **Simple path** — `run_sandbox_task()`: host clones repo, calls Ollama, applies fix, commits, validates in Podman container
4. **Complex path** — `coder_node`:
   4a. Clones repo **once** into shared workspace
   4b. Sorts subtasks by `depends_on` (topological order)
   4c. For each subtask: creates isolated branch → runs coder (simple/super) → if success, records branch; if fails, deletes branch
5. **CI/CD Gatekeeper** — merges subtask branches one by one, running `test_runner.validate_fix()` after each merge; drops branches that conflict or fail
6. **CI/CD Review** — LLM code review + test-fix cycles on the fully merged result
7. **Queue** — Fix added to human review queue with combined diff, comment, workspace path
8. **Human Review** — User reviews in web UI, approves/rejects/trashes
9. **Submit** — On approval, PR is created on GitHub

## 7. Workspace Management

- **Path:** `../bounty_workspaces/bounty_<id>/` (relative to project root)
- **Single shared clone:** One repo clone per bounty, used by all agents
- **Branch isolation:** Each subtask works on its own branch (`bounty-fix-{id}-sub-{n}`) — no file collisions
- **Atomic rollback:** CI/CD drops branches via `git branch -D` on conflict/failure; base branch remains clean
- **Cumulative diffs:** CI/CD merges branches into base one by one with validation after each merge
- **Persistence:** Repos survive restarts, can be inspected via "Show in Finder"
- **Cleanup:** Auto-delete untouched tasks after 30 days
- **Manual:** "Clear All Untouched" button in UI, individual task deletion
- **Orphan Cleanup:** Daily cron removes workspaces older than 7 days with no pending review

## 8. Graceful Shutdown

Triggered by `Ctrl+C` or `SIGTERM`:

1. Signal handler catches SIGINT/SIGTERM
2. Task processor stops (waits up to 60s for current task)
3. All running sandbox containers are killed
4. Interrupted bounties reset to `new` status in database
5. Clean process exit

## 9. Web UI Dashboard

| Block | Content |
|-------|---------|
| **System Status** | Running/Stopped + uptime |
| **Ollama** | Running status + loaded model names |
| **Sandbox** | Podman/Docker status + image built |
| **Today's Stats** | Tasks processed, success rate, avg duration |
| **Database** | Total bounties, reviews, DB size |
| **Quick View** | Clickable counts: New, Failed, Review (navigates to tab) |

## 10. Log Stats

When viewing logs for a specific bounty, a stats panel shows:
- Total duration
- Total tokens (prompt + completion)
- Per-model breakdown: tokens, duration, tokens/sec speed
