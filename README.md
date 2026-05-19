# Bounty Factory

Autonomous bounty hunting system for Algora.io and GitHub issues. Finds open bounties, classifies them, generates fixes using local LLMs (Ollama) inside Podman sandboxes, validates fixes, runs code review, and queues for human review before PR submission.

## Features

- **Automatic Bounty Discovery** - Fetches open bounties via Algora tRPC API and GitHub issues with bounty labels
- **LangGraph Orchestration** - State graph with conditional routing, checkpointer, and 7 nodes (precheck, classify, simple/complex agent, validate, review, enqueue)
- **Podman Sandboxed Execution** - Container-isolated LLM inference (2 CPU, 2GB RAM limits), host handles git/file I/O for speed
- **Task Classification** - Uses local LLM (`qwen2.5:0.5b`) to route tasks by complexity (simple vs complex)
- **Dual Agent System**
  - **Simple Agent** (`qwen2.5-coder:7b-instruct-q4_K_M`) - Handles single-file fixes, typos, small bugs
  - **Complex Agent** - Decomposes tasks into subtasks, solves via sandboxed LLM
- **Repo Mapping** - Auto-detects framework, package manager, test/lint commands for JS, Python, Go, Rust, C#
- **Validation** - Runs install, tests, and lint checks before queuing for review
- **Code Review** - Self-review with local LLM before human approval
- **Pre-check System** - Checks issue assignments, recent claims, and CONTRIBUTING.md before processing
- **Human Review Queue** - Web UI to review diffs, view model stats, approve/reject/skip fixes
- **PR Creation** - Pushes branch and creates GitHub PR after human approval
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
              ┌────────────────────────┐
              │     Task Classifier    │
              │   (qwen2.5:0.5b)       │
              └───────────┬────────────┘
                          ▼
            ┌─────────────┴─────────────┐
            ▼                           ▼
   ┌────────────────┐        ┌──────────────────┐
   │  Simple Agent  │        │  Complex Agent   │
   │ (Podman sandbox│        │ (Podman sandbox  │
   │  + qwen-coder) │        │  + decomposer)   │
   └────────┬───────┘        └────────┬─────────┘
            └────────────┬───────────┘
                         ▼
                ┌────────────────┐
                │  Repo Mapper   │
                │  Test Runner   │
                └────────┬───────┘
                         ▼
                ┌────────────────┐
                │ Code Reviewer  │
                │ (qwen-coder)   │
                └────────┬───────┘
                         ▼
                ┌────────────────┐
                │  Review Queue  │
                │  (Human UI)    │
                └────────┬───────┘
                         ▼
                ┌────────────────┐
                │  PR Creator    │
                └────────────────┘

Sandbox Architecture:
┌─────────────────────────────────────────┐
│  Host (macOS)                           │
│  - Clones repo (fast native SSD)        │
│  - Reads files, passes to container     │
│  - Applies fix, commits, validates      │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │  Podman Container (isolated)      │  │
│  │  - Receives task config via mount │  │
│  │  - Calls Ollama on host           │  │
│  │  - Outputs fix JSON to stdout     │  │
│  │  - NO git, NO file I/O            │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
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

### 2. Configure Ollama Models

```bash
ollama pull qwen2.5:0.5b                         # Task classifier (lightweight)
ollama pull qwen2.5-coder:7b-instruct-q4_K_M     # Agent & reviewer (faster on Mac)
```

### 3. Configure Settings

Edit `config/config.yaml`:

```yaml
test_mode:
  enabled: true          # Set false for production (paid bounties)
  skip_paid: true        # Skip paid bounties in test mode

ollama:
  base_url: "http://localhost:11434"
  models:
    classifier: "qwen2.5:0.5b"
    simple_agent: "qwen2.5-coder:7b-instruct-q4_K_M"
    code_reviewer: "qwen2.5-coder:7b-instruct-q4_K_M"

git:
  username: "YOUR_GITHUB_USERNAME"
  token: "YOUR_GITHUB_TOKEN"        # Required for PR creation
  default_branch: "main"

workspace:
  base_path: "../bounty_workspaces" # Relative to project root
```

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

- **Dashboard** - System status, Ollama models, sandbox status, today's stats, database info, quick navigation
- **Task Tabs** - New, Processing, Review, Failed (clickable with live counts)
- **Pending Reviews** - Diff viewer, model stats, approve/reject/skip actions
- **Logs** - Full processing history with model stats (tokens, duration, tokens/sec)

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

1. **Scan** - Fetch bounties from Algora tRPC API or GitHub issues with bounty labels
2. **Classify** - Task classifier (Ollama `qwen2.5:0.5b`) determines simple vs complex
3. **Pre-check** - Validates issue availability, checks assignments, reads CONTRIBUTING.md
4. **Process** - Agent runs inside Podman sandbox: clones repo on host, calls Ollama, host applies fix
5. **Validate** - Repo mapper detects framework/commands, test runner runs install/tests/lint
6. **Review** - Code review agent validates quality
7. **Queue** - Fix added to human review queue with diff, comment, and workspace path
8. **Human Review** - You review in the web UI, view model stats, inspect workspace, approve/reject
9. **Submit** - On approval, branch is pushed and PR is created on GitHub

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

- `qwen2.5:0.5b`: ~500MB (classifier)
- `qwen2.5-coder:7b-instruct-q4_K_M`: ~4GB (agent + reviewer)
- Total with both running: ~5GB

## Project Structure

```
bounty_factory/
├── config/
│   └── config.yaml              # Configuration
├── sandbox/
│   ├── Dockerfile               # Minimal sandbox image (Python + requests)
│   └── run_task.py              # Container entry point (LLM-only inference)
├── src/
│   ├── agents/                  # Agent modules
│   │   ├── task_classifier.py   # Task complexity classifier
│   │   ├── simple_agent.py      # Local LLM agent (LangChain structured output)
│   │   ├── complex_agent.py     # Agent with task decomposition
│   │   ├── code_reviewer.py     # Code review agent
│   │   ├── task_decomposer.py   # Complex task decomposition (LangChain)
│   │   ├── router.py            # Agent routing logic
│   │   ├── pr_creator.py        # PR creation
│   │   ├── github_scout.py      # GitHub issue discovery
│   │   ├── github_checker.py    # Pre-check (assignments, claims)
│   │   ├── comment_generator.py # Suggested GitHub comments
│   │   ├── repo_mapper.py       # Framework/command detection
│   │   └── test_runner.py       # Validation (install/test/lint)
│   ├── core/                    # Core modules
│   │   ├── orchestrator.py      # LangGraph workflow orchestrator
│   │   ├── graph.py             # StateGraph definition + compilation
│   │   ├── nodes.py             # Graph node wrappers
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
│       └── app.py               # Flask web server + UI
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
