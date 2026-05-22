import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

from .config import config
from .database import db
from ..utils.logger import get_logger

logger = get_logger(__name__)

SANDBOX_IMAGE = "bounty-sandbox:latest"
SANDBOX_TIMEOUT = 300
SANDBOX_MEMORY = "2g"
SANDBOX_CPUS = "2"

_build_lock = threading.Lock()
_image_built = False
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


def kill_running_containers() -> int:
    """No-op: validation containers are short-lived and auto-removed (--rm)."""
    return 0


def kill_containers_for_bounty(bounty_id: int) -> int:
    """Kill any running containers associated with a specific bounty."""
    runtime = _detect_container_runtime()
    if not runtime:
        return 0

    try:
        result = subprocess.run(
            [runtime, "ps", "--filter", f"name=bounty-{bounty_id}", "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=10,
        )
        container_ids = [c.strip() for c in result.stdout.strip().split("\n") if c.strip()]
        for cid in container_ids:
            subprocess.run(
                [runtime, "kill", cid],
                capture_output=True, timeout=10,
            )
            logger.info(f"Killed container {cid} for bounty {bounty_id}")
        return len(container_ids)
    except Exception as e:
        logger.warning(f"Failed to kill containers for bounty {bounty_id}: {e}")
        return 0


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
    if result.returncode != 0:
        logger.error(f"Git clone failed for {repo_url}: {result.stderr.strip()}")
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


def _generate_fix_on_host(
    bounty: Dict[str, Any],
    agent_type: str,
    model: str,
    repo_files: str,
    subtasks: list = None,
) -> Optional[Dict[str, Any]]:
    """Generate fix using Ollama directly on the host (native speed)."""
    from langchain_ollama import ChatOllama
    from pydantic import BaseModel
    from typing import List as TypingList

    class FileChange(BaseModel):
        path: str
        content: str
        action: str

    class FixOutput(BaseModel):
        files: TypingList[FileChange]
        confidence: float = 0.8
        reasoning: str = ""

    title = bounty.get("title", "")
    description = bounty.get("description", "")
    issue_url = bounty.get("issue_url", "")
    ollama_url = config.ollama.get("base_url", "http://localhost:11434")

    llm_raw = ChatOllama(
        model=model,
        base_url=ollama_url,
        temperature=0.3,
        num_predict=16384,
    )

    llm_structured = llm_raw.with_structured_output(FixOutput)

    all_files = []
    total_tokens = {"prompt": 0, "completion": 0}

    def _invoke_with_fallback(prompt: str) -> Optional[TypingList[Dict]]:
        nonlocal total_tokens
        try:
            fix_result: FixOutput = llm_structured.invoke(prompt)
            meta = getattr(fix_result, 'response_metadata', {}) if hasattr(fix_result, 'response_metadata') else {}
            total_tokens["prompt"] += meta.get("prompt_eval_count", 0)
            total_tokens["completion"] += meta.get("eval_count", 0)
            return [f.model_dump() for f in fix_result.files]
        except Exception as e:
            logger.warning(f"Structured output failed, trying raw JSON parse: {e}")
            try:
                raw_response = llm_raw.invoke(prompt)
                meta = getattr(raw_response, 'response_metadata', {})
                total_tokens["prompt"] += meta.get("prompt_eval_count", 0)
                total_tokens["completion"] += meta.get("eval_count", 0)
                content = raw_response.content if hasattr(raw_response, 'content') else str(raw_response)
                import re
                json_match = re.search(r'\{[\s\S]*"files"[\s\S]*\}', content)
                if json_match:
                    parsed = json.loads(json_match.group())
                    if "files" in parsed:
                        return parsed["files"]
                logger.error("Could not extract files from raw response")
                return None
            except Exception as e2:
                logger.error(f"Raw fallback also failed: {e2}")
                return None

    if agent_type == "simple":
        system_prompt = """You are a code generation assistant. Fix bugs or implement small features.
Guidelines:
1. Only modify necessary files
2. Make minimal, focused changes
3. Follow existing code style
4. Do NOT add unnecessary features"""

        user_prompt = f"""Issue Title: {title}

Issue Description:
{description}

Issue URL: {issue_url}

Repository Files (sample):
{repo_files}

Generate the fix."""

        try:
            files_result = _invoke_with_fallback(user_prompt)
            if files_result:
                all_files = files_result
        except Exception as e:
            logger.error(f"LLM fix generation failed: {e}")
            return None

    else:
        if subtasks:
            for subtask in subtasks:
                subtask_desc = subtask.get("description", "")
                subtask_prompt = f"""Subtask: {subtask_desc}
Original issue: {title}
Description: {description[:500]}

Repository Files (sample):
{repo_files}

Solve this subtask."""

                try:
                    files_result = _invoke_with_fallback(subtask_prompt)
                    if files_result:
                        all_files.extend(files_result)
                except Exception as e:
                    logger.error(f"Subtask LLM failed: {e}")
                    continue
        else:
            complex_prompt = f"""Issue Title: {title}

Issue Description:
{description}

Issue URL: {issue_url}

Repository Files (sample):
{repo_files}

Generate the fix."""

            try:
                files_result = _invoke_with_fallback(complex_prompt)
                if files_result:
                    all_files = files_result
            except Exception as e:
                logger.error(f"Complex LLM failed: {e}")
                return None

    if not all_files:
        return None

    return {
        "success": True,
        "agent_type": agent_type,
        "files_changed": all_files,
        "model_used": model,
        "token_stats": {
            "prompt_tokens": total_tokens["prompt"],
            "completion_tokens": total_tokens["completion"],
            "total_tokens": total_tokens["prompt"] + total_tokens["completion"],
        },
    }


def _run_validation_in_container(
    runtime: str,
    sandbox_dir: Path,
    bounty_id: int,
) -> Dict[str, Any]:
    """Run install + test commands inside a sandboxed container using podman cp."""
    install_cmd = _detect_install_command(sandbox_dir)
    test_cmd = _detect_test_command(sandbox_dir)

    if not install_cmd and not test_cmd:
        return {"install_ok": True, "tests_ok": True, "overall": True, "failures": []}

    container_cmds = []
    if install_cmd:
        container_cmds.append(f"cd /workspace && {install_cmd}")
    if test_cmd:
        container_cmds.append(f"cd /workspace && {test_cmd}")

    script = " && ".join(container_cmds)

    # Create container without volume mounts (Podman VM can't see /Volumes on macOS)
    container_cmd_create = [
        runtime, "create",
        "--name", f"bounty-validate-{bounty_id}",
        "--memory", SANDBOX_MEMORY,
        "--cpus", SANDBOX_CPUS,
        "--network", "none",
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        SANDBOX_IMAGE,
        "bash", "-c", script,
    ]

    _log(bounty_id, "sandbox", f"Creating validation container ({runtime})", "processing")

    container_id = None
    try:
        # Create container
        create_result = subprocess.run(
            container_cmd_create,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if create_result.returncode != 0:
            raise Exception(f"Container create failed: {create_result.stderr}")
        container_id = create_result.stdout.strip()

        # Copy workspace into container
        _log(bounty_id, "sandbox", "Copying workspace into container", "processing")
        cp_result = subprocess.run(
            [runtime, "cp", str(sandbox_dir), f"{container_id}:/workspace"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if cp_result.returncode != 0:
            raise Exception(f"Container cp failed: {cp_result.stderr}")

        # Start container
        _log(bounty_id, "sandbox", f"Running validation in container ({runtime})", "processing")
        start_result = subprocess.run(
            [runtime, "start", "-a", container_id],
            capture_output=True,
            text=True,
            timeout=SANDBOX_TIMEOUT,
        )

        validation = {
            "install_ok": True,
            "tests_ok": True,
            "overall": True,
            "failures": [],
            "stdout": start_result.stdout[:2000] if start_result.stdout else "",
            "stderr": start_result.stderr[:2000] if start_result.stderr else "",
            "exit_code": start_result.returncode,
        }

        if install_cmd and start_result.returncode != 0:
            validation["install_ok"] = False
            validation["overall"] = False
            validation["failures"].append(f"Install failed (exit {start_result.returncode})")
            return validation

        if test_cmd:
            if start_result.returncode != 0:
                validation["tests_ok"] = False
                validation["overall"] = False
                for line in (start_result.stdout + start_result.stderr).splitlines():
                    lower = line.lower()
                    if any(kw in lower for kw in ["error", "failed", "expect", "exception", "assert"]):
                        validation["failures"].append(line.strip())
                        if len(validation["failures"]) >= 10:
                            break
            else:
                validation["tests_ok"] = True

        return validation

    except subprocess.TimeoutExpired:
        _log(bounty_id, "sandbox", f"Validation timed out after {SANDBOX_TIMEOUT}s", "error")
        return {
            "install_ok": False,
            "tests_ok": False,
            "overall": False,
            "failures": ["Validation timed out"],
            "exit_code": -1,
        }
    except Exception as e:
        logger.error(f"Validation container failed: {e}")
        return {
            "install_ok": False,
            "tests_ok": False,
            "overall": False,
            "failures": [str(e)],
            "exit_code": -1,
        }
    finally:
        # Clean up container
        if container_id:
            subprocess.run(
                [runtime, "rm", "-f", container_id],
                capture_output=True,
                timeout=10,
            )


def run_sandbox_task(
    bounty: Dict[str, Any],
    agent_type: str,
    model: str = None,
    subtasks: list = None,
) -> Optional[Dict[str, Any]]:
    """
    New architecture:
    1. Host clones repo (fast native SSD)
    2. Host generates fix via Ollama directly (native speed, no VM hop)
    3. Host applies fix, commits
    4. Container runs validation (install + tests) in sandbox
    5. Host parses results
    """
    bounty_id = bounty.get("id")
    repo_url = bounty.get("repository_url", "")

    sandbox_cfg = config.get("sandbox", {})
    if not sandbox_cfg.get("enabled", True):
        logger.info("Sandbox disabled, using local agent")
        _log(bounty_id, "sandbox", "Sandbox disabled, using local agent", "warning")
        return None

    runtime = _detect_container_runtime()
    if not runtime:
        logger.warning("No container runtime available, falling back to local execution")
        _log(bounty_id, "sandbox", "No container runtime, using local agent", "warning")
        return None

    if not _ensure_image(bounty_id):
        logger.warning("Sandbox image not available, falling back to local execution")
        _log(bounty_id, "sandbox", "Image not ready, using local agent", "warning")
        return None

    _ensure_pip_cache_volume(runtime)

    workspace_base = config.get("workspace.base_path")
    sandbox_dir = Path(workspace_base) / f"bounty_{bounty_id}"

    model = model or config.agents.get('roles', {}).get('simple_coder', "qwen2.5:0.5b")

    # Step 1: Clone on host
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

    # Step 3: Generate fix on host (native Ollama, no VM hop)
    _log(bounty_id, "sandbox", f"Generating fix on host ({model})", "processing")
    start_time = time.time()

    fix_result = _generate_fix_on_host(bounty, agent_type, model, repo_files, subtasks)

    gen_duration = time.time() - start_time
    _log(bounty_id, "sandbox", f"Fix generated in {gen_duration:.1f}s", "processing")

    if not fix_result:
        _log(bounty_id, "sandbox", "LLM returned no valid fix", "error")
        return {"success": False, "error": "LLM returned no valid fix"}

    # Step 4: Apply fix on host
    _log(bounty_id, "sandbox", "Applying fix on host", "processing")
    files_changed = fix_result.get("files_changed", [])
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

    # Step 6: Validate in sandbox container
    validation = _run_validation_in_container(runtime, sandbox_dir, bounty_id)

    fix_result["repo_path"] = str(sandbox_dir)
    fix_result["branch_name"] = branch_name
    fix_result["commit_sha"] = commit_sha
    fix_result["diff_content"] = diff_content
    fix_result["files_changed"] = files_changed
    fix_result["validation"] = validation
    fix_result["duration"] = gen_duration

    if validation.get("overall"):
        _log(bounty_id, "sandbox", "Fix generated and validated successfully", "processing")
    else:
        failures = validation.get("failures", [])
        _log(bounty_id, "sandbox", f"Validation failed: {'; '.join(failures[:3])}", "warning")

    return fix_result


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
