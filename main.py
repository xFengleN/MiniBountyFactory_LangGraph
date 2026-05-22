#!/usr/bin/env python3
"""
Bounty Factory - Main Entry Point

Usage:
    python main.py                    # Start web UI
    python main.py --daemon          # Run as daemon
    python main.py --fetch           # Run single fetch cycle
    python main.py --status          # Show system status
    python main.py --setup-ollama     # Install Ollama models
"""

import argparse
import os
import sys
import signal
import time
from pathlib import Path

# Add parent directory to path so 'bounty_factory' module is found
sys.path.insert(0, str(Path(__file__).parent.parent))

from bounty_factory.src.core.orchestrator import BountyFactoryOrchestrator
from bounty_factory.src.core.config import config
from bounty_factory.src.utils.ollama_client import OllamaClient
from bounty_factory.src.utils.logger import get_logger

logger = get_logger(__name__)


def setup_ollama():
    print("Setting up Ollama models...")

    client = OllamaClient(base_url=config.ollama.get('base_url', 'http://localhost:11434'))

    roles = config.agents.get('roles', {})
    model_list = list(set(v for v in roles.values() if v))
    if not model_list:
        model_list = ['qwen2.5:0.5b', 'qwen2.5-coder:7b-instruct-q4_K_M']

    unique_models = list(set(model_list))

    for model in unique_models:
        print(f"Pulling model: {model}")
        success = client.pull_model(model)
        if success:
            print(f"  ✓ {model} installed")
        else:
            print(f"  ✗ {model} failed")

    print("\nOllama setup complete!")
    print(f"Available models: {client.list_models()}")


def run_daemon():
    orchestrator = BountyFactoryOrchestrator()

    def signal_handler(sig, frame):
        print("\nShutting down...")
        orchestrator.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    orchestrator.start()

    print("Bounty Factory running in daemon mode. Press Ctrl+C to stop.")

    try:
        while True:
            status = orchestrator.get_status()
            print(f"Status: {status}")
            import time
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        orchestrator.stop()


def run_fetch_cycle():
    from bounty_factory.src.core.orchestrator import BountyFactoryOrchestrator
    from bounty_factory.src.core.database import db

    print("Running single fetch cycle...")

    orchestrator = BountyFactoryOrchestrator()
    count = orchestrator.manual_scan(limit=10)

    print(f"Fetched {count} new tasks")

    pending = db.get_pending_bounties()
    print(f"Pending tasks: {len(pending)}")


def show_status():
    orchestrator = BountyFactoryOrchestrator()
    status = orchestrator.get_status()

    print("\n=== Bounty Factory Status ===")
    print(f"Running: {status['running']}")

    router = status['router_status']
    print("\nAgent Status:")
    print(f"  Task Classifier: {'✓' if router.get('classifier_available') else '✗'}")
    print(f"  Simple Agent: {'✓' if router.get('simple_agent_available') else '✗'}")
    print(f"  Complex Agent: {'✓' if router.get('complex_agent_available') else '✗'}")

    print(f"\nCode Reviewer: {'✓' if status.get('code_reviewer_available') else '✗'}")
    print(f"PR Creator: {'✓' if status.get('pr_creator_configured') else '✗'}")
    print(f"GitHub Scout: {'✓' if status.get('github_scout_available') else '✗'}")

    print(f"\nPending Reviews: {status.get('pending_reviews', 0)}")

    print("\n=== Configuration ===")
    print(f"Ollama: {config.get('ollama.base_url')}")


def run_web_ui(port: int = 5000):
    from bounty_factory.src.api.app import run_server
    from bounty_factory.src.core.orchestrator import BountyFactoryOrchestrator
    from bounty_factory.src.core.database import db
    from bounty_factory.src.core.sandbox import kill_running_containers

    orchestrator = None
    _shutting_down = False

    def shutdown_handler(sig, frame):
        nonlocal orchestrator, _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        print(f"\nReceived {signal.Signals(sig).name}, shutting down gracefully...")
        sys.stdout.flush()
        if orchestrator:
            orchestrator.stop()
            time.sleep(0.5)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    print(f"Starting Bounty Factory web UI on port {port}...")
    orchestrator = run_server(port=port, debug=False, return_app=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        if not _shutting_down:
            shutdown_handler(signal.SIGINT, None)


def main():
    parser = argparse.ArgumentParser(description='Bounty Factory')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon')
    parser.add_argument('--fetch', action='store_true', help='Run single fetch cycle')
    parser.add_argument('--status', action='store_true', help='Show system status')
    parser.add_argument('--setup-ollama', action='store_true', help='Install Ollama models')
    parser.add_argument('--port', type=int, default=8899, help='Web UI port')
    parser.add_argument('--web', action='store_true', help='Start web UI')

    args = parser.parse_args()

    if args.setup_ollama:
        setup_ollama()
    elif args.status:
        show_status()
    elif args.fetch:
        run_fetch_cycle()
    elif args.web or args.daemon or not any([args.status, args.fetch, args.setup_ollama]):
        if args.daemon:
            run_daemon()
        else:
            run_web_ui(port=args.port)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()