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
┌─────────────┐
│  Precheck   │ ← GitHub issue validation
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Classify   │ ← simple vs complex routing
└──────┬──────┘
       │
  ┌────┴────┐
  ▼         ▼
┌──────┐ ┌─────────┐
│Simple│ │ Complex │
│Agent │ │ Agent   │
└──┬───┘ └───┬─────┘
   └────┬────┘
        ▼
┌─────────────┐
│  Validate   │ ← install/test/lint
└──────┬──────┘
       │
       ▼
┌─────────────┐     Yes     ┌─────────────┐
│  Review     │ ──────────▶ │ Enqueue     │ ──▶ END (queued_for_review)
│  (auto)     │             │ Review      │
└─────────────┘             └─────────────┘
```

### 2.3 Sandbox Architecture

```
┌─────────────────────────────────────────┐
│  Host (macOS)                           │
│  - Clones repo (fast native SSD)        │
│  - Reads relevant files                 │
│  - Writes task config to temp file      │
│  - Mounts config into container         │
│  - Receives fix JSON from container     │
│  - Applies files, commits, validates    │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │  Podman Container (isolated)      │  │
│  │  - Read-only root FS              │  │
│  │  - 2 CPU, 2GB RAM limit           │  │
│  │  - Receives config via volume     │  │
│  │  - Calls Ollama (host.internal)   │  │
│  │  - Outputs fix JSON to stdout     │  │
│  │  - NO git, NO file I/O            │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

### 2.4 Agent Decision Tree

```
Bounty Task
     │
     ▼
┌─────────────────┐
│  Task Classifier│
│ (qwen2.5:0.5b)  │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
 Simple?   Complex?
    │         │
    ▼         ▼
┌───────┐ ┌────────────┐
│Simple │ │Complex     │
│Agent  │ │Agent       │
│Sandbox│ │(Decompose) │
└───┬───┘ └───┬────────┘
    │         │
    └────┬────┘
         ▼
┌────────────────┐
│ Pre-check      │
│ (assignee,     │
│  claims,       │
│  CONTRIBUTING) │
└────────┬───────┘
         ▼
┌────────────────┐
│ Repo Mapper    │
│ Test Runner    │
│ (validate)     │
└────────┬───────┘
         ▼
┌────────────────┐
│ Code Review    │
│ Agent          │
└────────┬───────┘
         ▼
┌────────────────┐
│Human Review    │
│    Queue       │
└────────┬───────┘
         ▼
┌────────────────┐
│PR Creator      │
│(After approve) │
└────────────────┘
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

### 3.4 Simple Task Agent (Podman Sandbox)

- **Model:** `qwen2.5-coder:7b-instruct-q4_K_M`
- **Purpose:** Single-file fixes, typos, small bugs
- **Capabilities:**
  - Host clones repository to workspace
  - Container receives task config via volume mount
  - Container calls Ollama on host, generates fix JSON
  - Host applies fix, creates branch and commit
  - Tracks token usage and duration

### 3.5 Complex Task Agent (Podman Sandbox)

- **Model:** `qwen2.5-coder:7b-instruct-q4_K_M`
- **Purpose:** Multi-file changes, architectural issues
- **Features:**
  - Task decomposition into subtasks (LangChain structured output)
  - Each subtask solved via sandboxed LLM
  - Fallback to direct fix generation if no subtasks

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

### 3.9 Code Review Agent

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
| `test_mode` | `enabled` | Toggle test (GitHub issues) vs production (Algora bounties) |
| `test_mode` | `github_queries` | Search queries for test mode |
| `test_mode` | `skip_paid` | Skip paid bounties in test mode |
| `ollama` | `base_url` | Ollama API endpoint |
| `ollama` | `models` | Model per task (classifier, simple_agent, code_reviewer) |
| `git` | `username` | GitHub username |
| `git` | `token` | GitHub personal access token |
| `workspace` | `base_path` | Workspace directory (relative to project root) |

## 6. Data Flow

1. **Scan** - Fetch from Algora tRPC + GitHub Scout → store in SQLite
2. **Process** - User clicks "Process" → pre-check → classify → route to agent
3. **Generate** - Host clones repo → container calls Ollama → host applies fix, commits
4. **Validate** - Repo mapper detects commands → test runner runs install/test/lint
5. **Review** - Code reviewer scores the fix
6. **Queue** - Fix added to review queue with diff, comment, workspace path
7. **Human Review** - User reviews in web UI, approves/rejects/skips
8. **Submit** - On approval, PR is created on GitHub

## 7. Workspace Management

- **Path:** `../bounty_workspaces/bounty_<id>/` (relative to project root)
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
