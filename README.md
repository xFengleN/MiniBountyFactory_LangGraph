# Bounty Factory

Autonomous bounty hunting system for Algora.io and GitHub issues. Finds open bounties, classifies them, generates fixes using local LLMs (Ollama) and cloud models (OpenCode GO) inside Podman sandboxes, validates fixes, runs code review, and queues for human review before PR submission.

## Features

- **Automatic Bounty Discovery** - Fetches open bounties via Algora tRPC API and GitHub issues with bounty labels
- **LangGraph Orchestration** - State graph with conditional routing, checkpointer, and 5 nodes (precheck, dispatcher, coder, cicd_specialist, enqueue)
- **Podman Sandboxed Execution** - Container-isolated LLM inference (2 CPU, 2GB RAM limits), host handles git/file I/O for speed
- **Role-Based Agent Model Selection** - Configure different models for each agent role via web UI dropdowns (Ollama + OpenCode GO)
- **4 Specialized Agents** - Dispatcher, Simple Coder, Super Coder, CI/CD Specialist
  - **Dispatcher** - Single LLM call classifies AND optionally decomposes tasks; routes simple tasks directly, breaks complex tasks into subtasks
  - **Simple Coder** - Handles simple fixes, boilerplate, unit tests, scripts, refactoring
  - **Super Coder** - Reserved for complex architecture, multi-file sync, algorithmic bottlenecks
  - **CI/CD Specialist** - Gatekeeper: merges subtask branches one-by-one with tests, then LLM review + test-fix cycles
- **Hybrid Sandbox Architecture** — single shared clone per bounty, isolated Git branches per subtask, CI/CD gatekeeper merges and validates
- **Repo Mapping** - Auto-detects framework, package manager, test/lint commands for JS, Python, Go, Rust, C#
- **Validation** - Runs install, tests, and lint checks before queuing for review
- **Code Review** - Self-review with local LLM before human approval
- **Pre-check System** - Checks issue assignments, recent claims, and CONTRIBUTING.md before processing
- **Human Review Queue** - Web UI with GitHub-style diff viewer, model stats, approve/reject/skip actions
- **PR Creation** - Pushes branch and creates GitHub PR after human approval
- **System Monitoring** - Live CPU/RAM/Disk stats, Ollama model info (size, GPU%, context), active agents
- **Graceful Shutdown** - Ctrl+C stops containers, resets interrupted bounties, cleans up workspaces
- **Workspace Management** - Persistent workspace storage, auto-cleanup of orphaned workspaces

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  GitHub     │     │  Algora tRPC │     │  Manual     │
│  Scout      │     │  Client      │     │  Scan       │
└──────┬──────┘     └──────┬───────┘     └──────┬──────┘
       └───────────────────┼───────────────────┘
                           ▼
                    ┌──────────────┐
                    │  Database    │
                    │  (SQLite)    │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ Orchestrator │
                    │ (LangGraph)  │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │  Dispatcher  │
                    │ classify +   │
                    │ decompose    │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │    Coder     │
                    │  Shared Clone│
                    │  + Branches  │
                    │  per subtask │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │CI/CD Gatekeeper
                    │ merge branch │
                    │ 1 → test,    │
                    │ merge branch │
                    │ 2 → test...  │
                    │ then review  │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ Review Queue │
                    │ (Human UI)   │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │  PR Creator  │
                    └──────────────┘

Hybrid Sandbox (one shared clone per bounty):
 bounty_{id}/
  ├── main (base branch)
  ├── bounty-fix-{id}-sub-1 ◀── simple_coder works here
  ├── bounty-fix-{id}-sub-2 ◀── super_coder works here
  │
  CI/CD merges each branch → tests → drop if fail
```

## Prerequisites

- Python 3.11+
- Ollama running locally (for local LLM tasks)
- Podman (rootless) for sandboxed execution
- Git configured

## Setup

### 1. Install Dependencies

```bash
cd bounty_factory
python -m venv .venv
source .venv/bin/activate  # On macOS/Linux
pip install -r requirements.txt
```

### 2. Configure Ollama

```bash
ollama pull qwen2.5:0.5b                         # Dispatcher (lightweight)
ollama pull qwen2.5-coder:7b-instruct-q4_K_M     # Simple coder / super coder / CI/CD
```

### 3. Configure Settings

Edit `config/config.yaml` or use the **Settings** modal in the web UI:

```yaml
ollama:
  base_url: "http://localhost:11434"

agents:
  roles:
    dispatcher: "qwen2.5:0.5b"
    simple_coder: "qwen2.5-coder:7b-instruct-q4_K_M"
    super_coder: "qwen2.5-coder:7b-instruct-q4_K_M"
    cicd_specialist: "qwen2.5-coder:7b-instruct-q4_K_M"

opencode:
  api_key: "YOUR_OPENCODE_API_KEY"   # Required for cloud models
  base_url: "https://api.opencode.ai"

git:
  username: "YOUR_GITHUB_USERNAME"
  token: "YOUR_GITHUB_TOKEN"        # Required for PR creation
  default_branch: "main"

workspace:
  base_path: "../bounty_workspaces" # Relative to project root
```

**Agent Roles:**
- **dispatcher** - Single LLM call classifies task AND optionally decomposes into subtasks
- **simple_coder** - Handles simple fixes, boilerplate, scripts, refactoring (was Simple Agent + Junior Coder)
- **super_coder** - Reserved for complex architecture, multi-file sync, algorithmic bottlenecks
- **cicd_specialist** - Test-fix loop + code review in one agent (was Reviewer + Tester)

Models can be selected from dropdowns in the web UI Settings, populated from your local Ollama instance and OpenCode GO API.

### 4. Build Sandbox Image

```bash
podman build -t bounty-sandbox:latest sandbox/
podman volume create bounty-pip-cache  # Persistent pip cache
```

## Usage

### Start Web UI (Recommended)

```bash
python main.py --web --port 8899
```

Then open `http://localhost:8899` in your browser.

### Web UI Features

- **Dashboard** - System status bar with Running/Start toggle, sandbox status, uptime, CPU/RAM/Disk stats, Ollama models (size, GPU%, context), active agents
- **Task Tabs** - New, Processing, Failed, Reviews, Logs (clickable with live counts, auto-refresh every 3s)
- **New Tasks** - Filter by difficulty, type, price range; sort by date, price, difficulty, score; bulk delete
- **Processing** - Live progress modal with elapsed time, model stats (tokens, duration) on completion
- **Failed** - Retry individual or all failed tasks; bulk delete
- **Reviews** - GitHub-style diff viewer with file-by-file breakdown, syntax highlighting, +N/-M stats; approve/reject/skip
- **Logs** - Full processing history with model stats (tokens, duration, tokens/sec)
- **Settings** - Role-based model selection (dropdowns from Ollama + OpenCode GO), Ollama base URL, OpenCode GO auth, Git credentials, workspace path

### Other Commands

```bash
# Check system status
python main.py --status

# Run single fetch cycle
python main.py --fetch

# Setup Ollama models
python main.py --setup-ollama

# Run as daemon (auto-processing)
python main.py --daemon
```

## How It Works

1. **Scan** — Fetch bounties from Algora tRPC API or GitHub issues with bounty labels
2. **Pre-check** — Validates issue availability, checks assignments, reads CONTRIBUTING.md
3. **Dispatch** — Dispatcher (Ollama) classifies simple vs complex; if complex, decomposes into subtasks with `depends_on` relationships
4. **Coder** — **Simple tasks**: sandbox clones, generates fix, applies, validates in Podman container. **Complex tasks**: `coder_node` clones repo **once**, creates isolated Git branches per subtask (sorted by dependency order), runs each coder on its branch, drops failed branches
5. **CI/CD Gatekeeper** — Merges subtask branches **one by one** into the base branch, running `test_runner.validate_fix()` after each merge. Branches that conflict or break tests are dropped (`git reset --hard`). Then runs LLM review + test-fix cycles on the fully merged result
6. **Queue** — Fix added to human review queue with combined diff, comment, and workspace path
7. **Human Review** — You review in the web UI with GitHub-style diff viewer, inspect workspace, approve/reject/trash
8. **Submit** — On approval, branch is pushed and PR is created on GitHub

### Dispatching & Task Decomposition

The Dispatcher handles both simple and complex tasks in a single LLM call. If the task is complex, it emits a decomposition plan with subtasks assigned to roles:
- **simple_coder** — Simple, routine changes
- **super_coder** — Complex, architectural changes

Each subtask includes a `depends_on` field listing prerequisite subtask IDs. The `coder_node` performs a topological sort (Kahn's algorithm) to guarantee execution order.

Processing logs show subtask distribution: `Dispatch: mode=decompose, classification=complex, subtasks: 6 simple_coder, 3 super_coder`

### Hybrid Sandbox Architecture

Complex task processing uses a **single shared clone** per bounty with **isolated Git branches**:

```
coder_node:
  1. Clone repo ONCE into workspace/bounty_{id}/
  2. Create base branch: bounty-fix-{id}
  3. For each subtask (in dependency order):
     a. Create branch: bounty-fix-{id}-sub-{n} from base
     b. Run coder on that branch (simple_coder or super_coder)
     c. If success → keep branch; if fail → git branch -D
  4. Return list of successful branch names to state

CI/CD Gatekeeper:
  1. Checkout base branch
  2. For each subtask branch:
     a. git merge --no-edit <branch>
     b. If conflict → git merge --abort, drop branch
     c. Run test_runner.validate_fix()
     d. If tests fail → git reset --hard HEAD~1, drop branch
     e. If pass → keep merge
  3. Proceed with LLM review + test-fix cycles
```

**Benefits:**
- **Atomic rollbacks** — Failed subtasks don't corrupt the base branch; just `git branch -D`
- **No file collisions** — Each subtask modifies its own isolated Git layer
- **Early integration testing** — CI/CD catches merge conflicts and test regressions after each branch merge

## Graceful Shutdown

Press `Ctrl+C` to stop the factory. The system will:
1. Stop the task processor (waits up to 60s for current task)
2. Kill any running sandbox containers
3. Reset interrupted bounties back to `new` status
4. Clean exit

## Configuration

### Test Mode vs Production

| Mode | Description |
|------|-------------|
| **Test** (`test_mode.enabled: true`) | Fetches GitHub "good first issue" labels, skips paid bounties |
| **Production** (`test_mode.enabled: false`) | Fetches from Algora tRPC + GitHub bounty labels, processes paid tasks |

### Agent Roles & Models

All model assignments are configured under `agents.roles` in `config/config.yaml` or via the web UI Settings modal. The Settings modal fetches available models from:
- **Ollama**: `GET /api/tags` on your local Ollama instance
- **OpenCode GO**: `GET /v1/models` on the configured cloud API (requires valid API key)

Models appear in dropdowns with source indicators (cloud models marked with "(cloud)").

### Difficulty Levels

Tasks are classified into 3 tiers with 3 sub-levels each:
- **Easy 1-3**: Trivial, Minor, Simple
- **Medium 1-3**: Moderate, Intermediate, Advanced
- **Hard 1-3**: Complex, Challenging, Expert

### Workspace

Repositories are cloned to `../bounty_workspaces/bounty_<id>/` relative to the project root. This path is configurable in `config/config.yaml`.

## System Requirements

- 16GB RAM (recommended for Ollama models)
- ~5GB disk for Ollama models (q4_K_M quantized)
- Podman installed and running
- Git installed and configured

## Memory Usage

- `qwen2.5:0.5b`: ~500MB (dispatcher)
- `qwen2.5-coder:7b-instruct-q4_K_M`: ~4GB (coder + CI/CD)
- Total with both running: ~5GB

## Project Structure

```
bounty_factory/
├── config/
│   └── config.yaml              # Configuration (agents.roles, ollama, opencode, git, etc.)
├── sandbox/
│   ├── Dockerfile               # Minimal sandbox image (Python + requests)
│   └── run_task.py              # Container entry point (LLM-only inference)
├── src/
│   ├── agents/                  # Agent modules
│   │   ├── dispatcher.py         # Task classification + decomposition (uses agents.roles.dispatcher)
│   │   ├── simple_coder.py       # Simple coding agent (uses agents.roles.simple_coder)
│   │   ├── super_coder.py        # Complex coding agent (uses agents.roles.super_coder)
│   │   ├── cicd_specialist.py    # Test-fix loop + code review (uses agents.roles.cicd_specialist)
│   │   ├── pr_creator.py         # PR creation
│   │   ├── github_scout.py       # GitHub issue discovery
│   │   ├── github_checker.py     # Pre-check (assignments, claims)
│   │   ├── comment_generator.py  # Suggested GitHub comments
│   │   ├── repo_mapper.py        # Framework/command detection
│   │   └── test_runner.py        # Validation (install/test/lint)
│   ├── core/                    # Core modules
│   │   ├── orchestrator.py      # LangGraph workflow orchestrator
│   │   ├── graph.py             # StateGraph definition + compilation
│   │   ├── nodes.py             # Graph node wrappers (gatekeeper merge, shared clone + branches)
│   │   ├── state.py             # BountyState TypedDict
│   │   ├── task_processor.py    # Async background task queue
│   │   ├── sandbox.py           # Podman sandbox orchestration
│   │   ├── algora_client.py     # Algora tRPC API client
│   │   ├── database.py          # SQLite database
│   │   └── config.py            # Config loader with path expansion
│   ├── utils/                   # Utilities
│   │   ├── ollama_client.py     # Ollama API wrapper with stats
│   │   └── logger.py            # Logging utility
│   └── api/
│       └── app.py               # Flask web server + UI (settings, diff viewer, model API)
├── data/                        # SQLite database
├── logs/                        # Log files
├── main.py                      # Entry point
└── requirements.txt             # Python dependencies
```

## Troubleshooting

### Ollama not running
```bash
ollama serve
```

### Podman not running (macOS)
```bash
podman machine start
```

### Sandbox connectivity issues
On macOS with Podman, containers use `host.containers.internal` to reach the host (not `localhost`).

### Port already in use
```bash
python main.py --web --port 5001
```

### Kill running factory
```bash
# If running in foreground: Ctrl+C
# If running in background:
pkill -f "python.*main.py --web"
```

## License

MIT
