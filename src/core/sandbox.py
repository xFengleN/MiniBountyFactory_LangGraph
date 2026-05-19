import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Set

from .config import config
from .database import db
from ..utils.logger import get_logger

logger = get_logger(__name__)

SANDBOX_IMAGE = "bounty-sandbox:latest"
SANDBOX_TIMEOUT = 600
SANDBOX_MEMORY = "2g"
SANDBOX_CPUS = "2"

_build_lock = threading.Lock()
_image_built = False

_container_registry: Dict[int, subprocess.Popen] = {}
_registry_lock = threading.Lock()
_shutdown_requested = False


def _log(bounty_id, agent, action, status, details=None):
    try:
        db.log_processing(bounty_id, agent, action, status, details)
    except Exception:
        pass


def _ensure_image(bounty_id=None):
    global _image_built
    if _image_built:
        return True

    runtime = _detect_container_runtime()
    if not runtime:
        return False

    with _build_lock:
        if _image_built:
            return True

        sandbox_dir = Path(__file__).parent.parent.parent / "sandbox"
        if not (sandbox_dir / "Dockerfile").exists():
            logger.error("Sandbox Dockerfile not found")
            if bounty_id:
                _log(bounty_id, "sandbox", "Dockerfile not found", "error")
            return False

        logger.info(f"Building sandbox image with {runtime}...")
        if bounty_id:
            _log(bounty_id, "sandbox", f"Building image with {runtime}", "processing")

        result = subprocess.run(
            [runtime, "build", "-t", SANDBOX_IMAGE, str(sandbox_dir)],
            capture_output=True, text=True, timeout=300
        )

        if result.returncode != 0:
            logger.error(f"Sandbox image build failed: {result.stderr}")
            if bounty_id:
                _log(bounty_id, "sandbox", "Image build failed", "error", result.stderr[:200])
            return False

        _image_built = True
        logger.info(f"Sandbox image built successfully with {runtime}")
        if bounty_id:
            _log(bounty_id, "sandbox", f"Image built ({runtime})", "processing")
        return True


def _detect_container_runtime():
    for runtime in ["docker", "podman"]:
        try:
            result = subprocess.run([runtime, "info"], capture_output=True, timeout=10)
            if result.returncode == 0:
                return runtime
        except Exception:
            continue
    return None


def _is_docker_available():
    return _detect_container_runtime() is not None


def is_shutdown_requested() -> bool:
    return _shutdown_requested


def register_container(bounty_id: int, proc: subprocess.Popen):
    with _registry_lock:
        _container_registry[bounty_id] = proc


def unregister_container(bounty_id: int):
    with _registry_lock:
        _container_registry.pop(bounty_id, None)


def kill_running_containers() -> int:
    killed = 0
    with _registry_lock:
        snapshot = dict(_container_registry)
    for bounty_id, proc in snapshot.items():
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                logger.info(f"Killed container for bounty {bounty_id}")
                killed += 1
        except Exception as e:
            logger.warning(f"Failed to kill container for bounty {bounty_id}: {e}")
    with _registry_lock:
        _container_registry.clear()
    return killed


def _ensure_pip_cache_volume(runtime):
    try:
        result = subprocess.run(
            [runtime, "volume", "inspect", "bounty-pip-cache"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            subprocess.run(
                [runtime, "volume", "create", "bounty-pip-cache"],
                capture_output=True, timeout=10
            )
            logger.info("Created persistent pip cache volume")
    except Exception as e:
        logger.warning(f"Failed to setup pip cache volume: {e}")


def _clone_repo(repo_url, sandbox_dir, token=None):
    if token and "github.com" in repo_url:
        if repo_url.startswith("https://github.com/"):
            repo_url = repo_url.replace("https://github.com/", f"https://{token}@github.com/")
        elif repo_url.startswith("http://github.com/"):
            repo_url = repo_url.replace("http://github.com/", f"https://{token}@github.com/")

    result = subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(sandbox_dir)],
        capture_output=True, text=True, timeout=180
    )
    return result.returncode == 0


def _read_repo_files(sandbox_dir, max_files=20):
    extensions = ("*.py", "*.js", "*.ts", "*.go", "*.rs")
    files = []
    for ext in extensions:
        files.extend(sandbox_dir.rglob(ext))

    content_parts = []
    for f in files[:max_files]:
        try:
            rel = f.relative_to(sandbox_dir)
            text = f.read_text(errors="ignore")
            content_parts.append(f"=== {rel} ===\n{text[:2000]}")
        except Exception:
            pass

    return "\n\n".join(content_parts)


def run_sandbox_task(
    bounty: Dict[str, Any],
    agent_type: str,
    model: str = None,
    subtasks: list = None,
) -> Optional[Dict[str, Any]]:
    """
    Host clones repo (fast native SSD), reads files, passes to container.
    Container calls Ollama, generates fix JSON.
    Host applies fix, commits, validates.
    """
    bounty_id = bounty.get("id")
    repo_url = bounty.get("repository_url", "")
    title = bounty.get("title", "")
    description = bounty.get("description", "")
    issue_url = bounty.get("issue_url", "")

    sandbox_cfg = config.get("sandbox", {})
    if not sandbox_cfg.get("enabled", True):
        logger.info("Sandbox disabled in config, using local agent")
        _log(bounty_id, "sandbox", "Sandbox disabled, using local agent", "warning")
        return None

    runtime = _detect_container_runtime()
    if not runtime:
        logger.warning("No container runtime available (docker/podman), falling back to local execution")
        _log(bounty_id, "sandbox", "No container runtime, using local agent", "warning")
        return None

    if not _ensure_image(bounty_id):
        logger.warning("Sandbox image not available, falling back to local execution")
        _log(bounty_id, "sandbox", "Image not ready, using local agent", "warning")
        return None

    _ensure_pip_cache_volume(runtime)

    workspace_base = config.get("workspace.base_path")
    sandbox_dir = Path(workspace_base) / f"bounty_{bounty_id}"

    # Step 1: Clone on host (fast native SSD)
    if sandbox_dir.exists() and (sandbox_dir / ".git").exists():
        _log(bounty_id, "sandbox", "Workspace already exists, skipping clone", "processing")
    else:
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir)
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        _log(bounty_id, "sandbox", "Cloning repo on host", "processing")
        github_token = config.git.get("token")
        if not _clone_repo(repo_url, sandbox_dir, github_token):
            _log(bounty_id, "sandbox", "Git clone failed", "error")
            return {"success": False, "error": "Git clone failed"}
        _log(bounty_id, "sandbox", "Repo cloned on host", "processing")

    # Step 2: Read relevant files
    repo_files = _read_repo_files(sandbox_dir)

    # Step 3: Run container (isolated LLM call, no file I/O)
    task_config = {
        "bounty": bounty,
        "agent_type": agent_type,
        "model": model or config.ollama.get("models.simple_agent", "qwen2.5-coder:7b-instruct-q4_K_M"),
        "repo_files": repo_files,
        "subtasks": subtasks or [],
    }

    # Write config to temp file to avoid env var size/escaping issues
    import tempfile
    config_fd, config_path = tempfile.mkstemp(suffix=".json", prefix=f"sandbox_{bounty_id}_")
    try:
        with os.fdopen(config_fd, 'w') as f:
            json.dump(task_config, f)

        # With --net=host, container shares host network.
        # Podman on macOS: host.containers.internal reaches the Mac.
        ollama_url = config.ollama.get("base_url", "http://localhost:11434")
        if "localhost" in ollama_url or "127.0.0.1" in ollama_url:
            ollama_url = ollama_url.replace("localhost", "host.containers.internal").replace("127.0.0.1", "host.containers.internal")

        container_cmd = [
            runtime, "run", "--rm",
            "--memory", SANDBOX_MEMORY,
            "--cpus", SANDBOX_CPUS,
            "--network", "host",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
            "-v", f"{config_path}:/sandbox/task_config.json:ro",
            "-v", "bounty-pip-cache:/root/.cache/pip",
            "-e", f"OLLAMA_BASE_URL={ollama_url}",
            SANDBOX_IMAGE,
        ]

        _log(bounty_id, "sandbox", f"Running container ({runtime}, {agent_type})", "processing")

        try:
            proc = subprocess.Popen(
                container_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            register_container(bounty_id, proc)

            try:
                stdout, stderr = proc.communicate(timeout=SANDBOX_TIMEOUT)
                result = subprocess.CompletedProcess(container_cmd, proc.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate(timeout=10)
                result = subprocess.CompletedProcess(container_cmd, -1, stdout, stderr)
                raise subprocess.TimeoutExpired(container_cmd, SANDBOX_TIMEOUT)
            finally:
                unregister_container(bounty_id)

            if result.returncode != 0:
                stderr_preview = result.stderr[:200] if result.stderr else ""
                _log(bounty_id, "sandbox", f"Container exited with code {result.returncode}", "error", stderr_preview)
                return {"success": False, "error": f"Container failed (exit {result.returncode})"}

            output = result.stdout.strip()
            if not output:
                _log(bounty_id, "sandbox", "Container produced no output", "error")
                return {"success": False, "error": "Container produced no output"}

            parsed = json.loads(output)
            if not parsed.get("success"):
                _log(bounty_id, "sandbox", f"Container failed: {parsed.get('error')}", "error")
                return parsed

            # Step 4: Apply fix on host (fast native SSD)
            _log(bounty_id, "sandbox", "Applying fix on host", "processing")
            files_changed = parsed.get("files_changed", [])
            applied = _apply_files(sandbox_dir, files_changed)

            if not applied:
                _log(bounty_id, "sandbox", "No files applied", "error")
                return {"success": False, "error": "No files applied"}

            # Step 5: Commit
            branch_name = f"bounty-fix-{bounty_id}"
            _run_git(sandbox_dir, ["checkout", "-b", branch_name])
            _run_git(sandbox_dir, ["add", "."])
            _run_git(sandbox_dir, ["commit", "-m", f"Fix bounty #{bounty_id}"])

            commit_sha = _run_git(sandbox_dir, ["rev-parse", "HEAD"]).strip()
            diff_content = _run_git(sandbox_dir, ["diff", "HEAD~1", "HEAD"])

            # Step 6: Validate
            validation = _validate_fix(sandbox_dir)

            parsed["repo_path"] = str(sandbox_dir)
            parsed["branch_name"] = branch_name
            parsed["commit_sha"] = commit_sha
            parsed["diff_content"] = diff_content
            parsed["files_changed"] = files_changed
            parsed["validation"] = validation
            _log(bounty_id, "sandbox", "Container completed successfully", "processing")
            return parsed

        except subprocess.TimeoutExpired:
            _log(bounty_id, "sandbox", f"Container timed out after {SANDBOX_TIMEOUT}s", "error")
            return {"success": False, "error": "Container timed out"}
        except json.JSONDecodeError as e:
            _log(bounty_id, "sandbox", "Invalid JSON output", "error", str(e))
            return {"success": False, "error": f"Invalid container output: {e}"}
        except Exception as e:
            _log(bounty_id, "sandbox", "Execution failed", "error", str(e))
            return {"success": False, "error": str(e)}
    finally:
        try:
            os.unlink(config_path)
        except Exception:
            pass


def _run_git(cwd, args):
    result = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=30
    )
    return result.stdout


def _apply_files(sandbox_dir, files_changed):
    skip_dirs = {"home", "Users", "var", "tmp", "etc", "usr", "opt"}
    applied = False

    for f in files_changed:
        raw_path = f.get("path", "").strip()
        rel_path = raw_path.lstrip("/").replace("\\", "/")
        first_part = rel_path.split("/")[0].lower()
        if first_part in skip_dirs:
            continue

        file_path = sandbox_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f.get("content", ""))
        applied = True

    return applied


def _validate_fix(sandbox_dir):
    validation = {"install_ok": True, "tests_ok": True, "lint_ok": True, "failures": [], "overall": True}

    install_cmd = _detect_install_command(sandbox_dir)
    if install_cmd:
        r = _run_cmd(install_cmd, sandbox_dir, timeout=120)
        validation["install_ok"] = r["exit_code"] == 0
        if not validation["install_ok"]:
            validation["failures"].append(f"Install failed: {r['stderr'][:200]}")
            validation["overall"] = False
            return validation

    test_cmd = _detect_test_command(sandbox_dir)
    if test_cmd:
        r = _run_cmd(test_cmd, sandbox_dir, timeout=120)
        validation["tests_ok"] = r["exit_code"] == 0
        if not validation["tests_ok"]:
            for line in (r["stdout"] + r["stderr"]).splitlines():
                lower = line.lower()
                if any(kw in lower for kw in ["error", "failed", "expect", "exception", "assert"]):
                    validation["failures"].append(line.strip())
                    if len(validation["failures"]) >= 10:
                        break
            validation["overall"] = False

    validation["overall"] = validation["install_ok"] and validation["tests_ok"]
    return validation


def _detect_install_command(sandbox_dir):
    sp = sandbox_dir
    if (sp / "package.json").exists():
        if (sp / "pnpm-lock.yaml").exists():
            return "pnpm install"
        if (sp / "yarn.lock").exists():
            return "yarn install"
        if (sp / "bun.lockb").exists() or (sp / "bun.lock").exists():
            return "bun install"
        return "npm install"
    if (sp / "pyproject.toml").exists() or (sp / "setup.py").exists():
        return "pip install -e ."
    if (sp / "requirements.txt").exists():
        return "pip install -r requirements.txt"
    if (sp / "go.mod").exists():
        return "go mod download"
    return None


def _detect_test_command(sandbox_dir):
    sp = sandbox_dir
    if (sp / "package.json").exists():
        try:
            import json as j
            pkg = j.loads((sp / "package.json").read_text())
            scripts = pkg.get("scripts", {})
            for name in scripts:
                if "test" in name:
                    pm = "pnpm" if (sp / "pnpm-lock.yaml").exists() else \
                         "yarn" if (sp / "yarn.lock").exists() else \
                         "bun" if (sp / "bun.lockb").exists() else "npm"
                    return f"{pm} run {name}"
        except:
            pass
        return None
    if (sp / "pytest.ini").exists() or (sp / "pyproject.toml").exists():
        return "pytest"
    if (sp / "tox.ini").exists():
        return "tox"
    if (sp / "go.mod").exists():
        return "go test ./..."
    return None


def _run_cmd(cmd, cwd, timeout=120):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=True)
        return {"exit_code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": "Command timed out"}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": str(e)}


def cleanup_workspace(bounty_id: int) -> bool:
    workspace_base = config.get("workspace.base_path")
    sandbox_dir = Path(workspace_base) / f"bounty_{bounty_id}"

    if sandbox_dir.exists():
        try:
            shutil.rmtree(sandbox_dir)
            logger.info(f"Cleaned up workspace for bounty {bounty_id}")
            _log(bounty_id, "sandbox", "Workspace deleted", "processing")
            return True
        except Exception as e:
            logger.error(f"Failed to cleanup workspace for bounty {bounty_id}: {e}")
            _log(bounty_id, "sandbox", "Workspace cleanup failed", "error", str(e))
            return False
    return False


def cleanup_orphaned_workspaces(max_age_days: int = 7) -> int:
    workspace_base = config.get("workspace.base_path")
    base = Path(workspace_base)
    if not base.exists():
        return 0

    deleted = 0
    import time
    cutoff = time.time() - (max_age_days * 86400)

    for d in base.iterdir():
        if not d.is_dir() or not d.name.startswith("bounty_"):
            continue
        if d.stat().st_mtime > cutoff:
            continue
        try:
            bounty_id = int(d.name.split("_")[1])
            if not db.has_pending_review_for(bounty_id):
                shutil.rmtree(d)
                deleted += 1
                logger.info(f"Cleaned up orphaned workspace: {d.name}")
        except (ValueError, IndexError):
            pass

    return deleted
