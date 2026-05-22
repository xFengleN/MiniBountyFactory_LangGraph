import shutil
import subprocess as _subprocess
from pathlib import Path
from typing import Dict, Any, List

from .state import BountyState
from ..agents.dispatcher import Dispatcher
from ..agents.github_checker import GitHubIssueChecker
from ..agents.comment_generator import CommentGenerator
from ..agents.simple_coder import SimpleCoder
from ..agents.super_coder import SuperCoder
from ..agents.cicd_specialist import CicdSpecialist
from .config import config
from .database import db
from .sandbox import run_sandbox_task, validate_in_container
from ..utils.logger import get_logger

logger = get_logger(__name__)

_dispatcher = Dispatcher()
_github_checker = GitHubIssueChecker(config.git.get('token'))
_comment_generator = CommentGenerator()
_simple_coder = SimpleCoder()
_super_coder = SuperCoder()
_cicd_specialist = CicdSpecialist()


def _run_git(repo_path: str, args: List[str]) -> tuple:
    """Run a git command in repo_path. Returns (stdout, returncode)."""
    try:
        r = _subprocess.run(['git'] + args, cwd=repo_path,
                            capture_output=True, text=True, timeout=30)
        return r.stdout.strip(), r.returncode
    except Exception as e:
        logger.error(f"git {' '.join(args)} failed: {e}")
        return '', -1


def _topological_sort(subtasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Kahn's algorithm — sort subtasks by depends_on."""
    by_id = {s['id']: s for s in subtasks}
    in_degree = {s['id']: 0 for s in subtasks}
    for s in subtasks:
        for dep in s.get('depends_on', []):
            if dep in by_id:
                in_degree[s['id']] = in_degree.get(s['id'], 0) + 1

    queue = [s['id'] for s in subtasks if in_degree.get(s['id'], 0) == 0]
    result = []
    while queue:
        nid = queue.pop(0)
        result.append(by_id[nid])
        for s in subtasks:
            if nid in s.get('depends_on', []):
                in_degree[s['id']] -= 1
                if in_degree[s['id']] == 0:
                    queue.append(s['id'])
    return result


def precheck_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]
    issue_url = bounty.get("issue_url", "")

    logger.info(f"Node precheck: bounty {bounty_id}")
    db.log_processing(bounty_id, "precheck", "start", "processing")

    check_result = None
    suggested_comment = ""

    if issue_url:
        check_result = _github_checker.check_issue(issue_url)
        if check_result.get("valid"):
            suggested_comment = _comment_generator.generate_intent_comment(bounty, check_result)

        if check_result.get("warnings"):
            for warning in check_result["warnings"]:
                logger.warning(f"Bounty {bounty_id}: {warning}")
                db.log_processing(bounty_id, "checker", warning, "warning")

    db.log_processing(bounty_id, "precheck", "complete", "processing")

    return {
        "precheck_result": check_result,
        "suggested_comment": suggested_comment,
    }


def dispatcher_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]

    logger.info(f"Node dispatcher: bounty {bounty_id}")
    db.log_processing(bounty_id, "dispatcher", "start", "processing")

    dispatch_result = _dispatcher.dispatch(bounty)
    raw = dispatch_result.classification.lower()
    if raw in ('simple', 'simple_coder'):
        classification = 'simple'
    elif raw in ('complex', 'complex_coder', 'complex_agent'):
        classification = 'complex'
    else:
        classification = 'simple'

    logger.info(f"Dispatch: mode={dispatch_result.mode}, classification={classification}, confidence={dispatch_result.confidence:.2f}")
    db.log_processing(bounty_id, "dispatcher", f"{classification} ({dispatch_result.confidence:.2f})", "processing")

    stats = _dispatcher.last_token_stats
    if stats.get('total_tokens', 0) > 0:
        db.log_processing(bounty_id, "dispatcher",
            f"Prompt: {stats.get('prompt_tokens', '?')} | Completion: {stats.get('completion_tokens', '?')} | Total: {stats.get('total_tokens', '?')}",
            "processing")

    subtasks = [s.model_dump() for s in dispatch_result.subtasks] if dispatch_result.subtasks else []

    if subtasks:
        role_counts = {}
        for s in subtasks:
            role = s.get('role', 'unknown')
            role_counts[role] = role_counts.get(role, 0) + 1
        summary = ', '.join(f'{v} {k}' for k, v in sorted(role_counts.items()))
        db.log_processing(bounty_id, "dispatcher", f"decomposed into {len(subtasks)} subtasks: {summary}", "processing")

    return {
        "classification": classification,
        "confidence": dispatch_result.confidence,
        "dispatch_mode": dispatch_result.mode,
        "subtasks": subtasks,
        "dispatch_reasoning": dispatch_result.reasoning,
    }


def coder_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]
    classification = state.get("classification", "simple")
    subtasks = state.get("subtasks", [])
    last_errors = state.get("last_validation_errors", [])

    retry_count = state.get("retry_count", 0) + 1

    logger.info(f"Node coder: bounty {bounty_id}, classification={classification}, retry={retry_count}")

    if last_errors:
        bounty = dict(bounty)
        context = "\n".join(f"- {e}" for e in last_errors[:10])
        bounty["description"] = (bounty.get("description", "") +
            f"\n\n[Previous attempt failed with these validation errors:\n{context}\n"
            "The workspace already has some changes applied. Fix the underlying issues, "
            "not just the test failures.]")

    if classification == "simple" or not subtasks:
        db.log_processing(bounty_id, "coder", "simple mode", "processing")
        model = config.agents.get('roles', {}).get('simple_coder', 'qwen2.5:0.5b')
        result = run_sandbox_task(bounty, agent_type="simple", model=model)

        if result is None:
            logger.info("Sandbox unavailable, falling back to local simple coder")
            result = _simple_coder.process(bounty)

        if not result:
            logger.warning(f"Simple coder returned no result for bounty {bounty_id}")
            db.log_processing(bounty_id, "coder", "no_result", "failed")
            return {"error": "Simple coder failed to generate fix", "status": "failed"}

        if not result.get("success", True):
            error = result.get("error", "Unknown error")
            logger.warning(f"Simple coder failed for bounty {bounty_id}: {error}")
            db.log_processing(bounty_id, "coder", f"failed: {error}", "failed")
            return {"error": error, "status": "failed"}

        if result.get("model_used"):
            db.log_processing(bounty_id, "coder", f"Model: {result['model_used']}", "processing")
        db.log_processing(bounty_id, "coder", "fix generated", "processing")

        return {
            "agent_type": "simple_coder",
            "repo_path": result.get("repo_path", ""),
            "branch_name": result.get("branch_name", ""),
            "commit_sha": result.get("commit_sha", ""),
            "diff_content": result.get("diff_content", ""),
            "files_changed": result.get("files_changed", []),
            "model_used": result.get("model_used", ""),
            "token_stats": result.get("token_stats", {}),
            "duration": result.get("duration", 0),
            "validation": result.get("validation", {}),
            "retry_count": retry_count,
        }

    db.log_processing(bounty_id, "coder", f"complex mode: {len(subtasks)} subtasks", "processing")

    # ---- shared workspace: single clone, isolated branches per subtask ----
    workspace_base = config.get('workspace.base_path')
    if not workspace_base:
        workspace_base = str(Path(__file__).parent.parent.parent / 'bounty_workspaces')
    workspace_path = Path(workspace_base) / f'bounty_{bounty_id}'

    repo_url = bounty.get('repository_url', '')
    if not repo_url:
        return {"error": "No repository URL", "status": "failed"}

    if workspace_path.exists():
        shutil.rmtree(workspace_path)
    workspace_path.mkdir(parents=True, exist_ok=True)

    from .sandbox import _clone_repo as _sandbox_clone
    github_token = config.git.get('token')
    if not _sandbox_clone(repo_url, workspace_path, github_token):
        return {"error": "Git clone failed", "status": "failed"}

    branch_name = f"bounty-fix-{bounty_id}"
    _run_git(str(workspace_path), ["checkout", "-b", branch_name])

    sorted_subtasks = _topological_sort(subtasks)

    subtask_branches = []
    all_files = []
    total_prompt = 0
    total_completion = 0
    total_duration = 0.0
    model_used = ""

    for subtask in sorted_subtasks:
        sub_branch = f"bounty-fix-{bounty_id}-sub-{subtask['id']}"
        role = subtask.get('role', 'simple_coder')
        logger.info(f"Coder: subtask {subtask['id']} ({role}) on branch {sub_branch}")
        db.log_processing(bounty_id, "coder", f"subtask {subtask['id']} ({role})", "processing")

        _run_git(str(workspace_path), ["checkout", branch_name])
        _run_git(str(workspace_path), ["checkout", "-b", sub_branch])

        if role == 'simple_coder':
            result = _simple_coder.process(
                bounty,
                subtask_description=subtask.get('description', ''),
                repo_path=str(workspace_path),
                subtask_branch=sub_branch,
            )
        elif role == 'super_coder':
            result = _super_coder.process(
                bounty,
                [subtask],
                repo_path=str(workspace_path),
                subtask_branch=sub_branch,
            )
        else:
            logger.warning(f"Unknown subtask role: {role}, dropping branch")
            _run_git(str(workspace_path), ["checkout", branch_name])
            _run_git(str(workspace_path), ["branch", "-D", sub_branch])
            continue

        if result:
            all_files.extend(result.get('files_changed', []))
            subtask_branches.append(sub_branch)
            ts = result.get('token_stats', {})
            total_prompt += ts.get('prompt_tokens', 0)
            total_completion += ts.get('completion_tokens', 0)
            total_duration += result.get('duration', 0)
            if result.get('model_used'):
                model_used = result['model_used']
            logger.info(f"Coder: subtask {subtask['id']} completed")
        else:
            logger.warning(f"Coder: subtask {subtask['id']} failed, dropping branch")
            _run_git(str(workspace_path), ["checkout", branch_name])
            _run_git(str(workspace_path), ["branch", "-D", sub_branch])

    _run_git(str(workspace_path), ["checkout", branch_name])

    if not all_files:
        logger.warning(f"No fix generated for complex bounty {bounty_id}")
        db.log_processing(bounty_id, "coder", "no_result", "failed")
        return {"error": "No subtask produced a fix", "status": "failed"}

    # Build combined diff across all subtask branches
    combined_diff_parts = []
    for branch in subtask_branches:
        out, rc = _run_git(str(workspace_path), ["diff", f"{branch_name}..." + branch])
        if out:
            combined_diff_parts.append(f"# {branch}\n{out}")
    final_diff = "\n\n".join(combined_diff_parts)

    _run_git(str(workspace_path), ["checkout", branch_name])
    head_sha, _ = _run_git(str(workspace_path), ["rev-parse", "HEAD"])

    db.log_processing(bounty_id, "coder",
                      f"complete: {len(all_files)} files across {len(subtask_branches)} subtask branches",
                      "processing")

    return {
        "agent_type": "coder",
        "repo_path": str(workspace_path),
        "branch_name": branch_name,
        "subtask_branches": subtask_branches,
        "commit_sha": head_sha,
        "diff_content": final_diff,
        "files_changed": all_files,
        "model_used": model_used,
        "token_stats": {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
        },
        "duration": total_duration,
        "subtasks_completed": len(subtask_branches),
        "retry_count": retry_count,
    }


def cicd_specialist_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]
    repo_path = state.get("repo_path", "")
    diff_content = state.get("diff_content", "")
    subtask_branches = state.get("subtask_branches", [])
    branch_name = state.get("branch_name", f"bounty-fix-{bounty_id}")

    logger.info(f"Node cicd_specialist: bounty {bounty_id}")
    db.log_processing(bounty_id, "cicd", "start", "processing")

    # === Gatekeeper: merge subtask branches one by one, test after each ===
    if repo_path and subtask_branches and Path(repo_path).exists():
        _run_git(repo_path, ["checkout", branch_name])
        initial_head, _ = _run_git(repo_path, ["rev-parse", "HEAD"])
        merged = []
        for branch in subtask_branches:
            out, rc = _run_git(repo_path, ["merge", "--no-edit", branch])
            if rc != 0:
                _run_git(repo_path, ["merge", "--abort"])
                logger.warning(f"Gatekeeper: merge conflict on {branch}, dropping")
                db.log_processing(bounty_id, "cicd", f"gatekeeper: {branch} merge conflict, dropped", "warning")
                continue

            validation = validate_in_container(bounty_id, repo_path)
            if not validation.get('overall', False):
                _run_git(repo_path, ["reset", "--hard", "HEAD~1"])
                logger.warning(f"Gatekeeper: tests failed after merging {branch}, dropping")
                db.log_processing(bounty_id, "cicd", f"gatekeeper: {branch} tests failed, dropped", "warning")
                continue

            merged.append(branch)
            logger.info(f"Gatekeeper: {branch} merged and validated")
            db.log_processing(bounty_id, "cicd", f"gatekeeper: {branch} merged", "processing")

        if not merged:
            logger.warning("Gatekeeper: no subtask branches survived, nothing to review")
            return {
                "validation_passed": False,
                "validation": {"overall": False, "failures": ["All subtask branches failed gatekeeping"]},
                "review_approved": False,
                "review_score": 0,
                "last_validation_errors": ["All subtask branches failed gatekeeping"],
                "error": "All subtask branches failed gatekeeping",
            }

        # Update diff to reflect the merged state
        out, rc = _run_git(repo_path, ["diff", initial_head, "HEAD"])
        if out:
            diff_content = out

    try:
        result = _cicd_specialist.process(bounty, diff_content, repo_path)

        if result.get("model_used"):
            db.log_processing(bounty_id, "cicd", f"Model: {result['model_used']}", "processing")

        approved = result.get("review_approved", False)
        score = result.get("review_score", 0)
        validation_passed = result.get("validation_passed", False)

        if result.get("fix_cycles", 0) > 0:
            db.log_processing(bounty_id, "cicd", f"test-fix cycles: {result['fix_cycles']}", "processing")

        if not validation_passed:
            failures = result.get("validation", {}).get("failures", [])
            failure_detail = "; ".join(failures[:3]) if failures else "validation failed"
            db.log_processing(bounty_id, "cicd", f"validation failed: {failure_detail}", "validation_failed")
        else:
            db.log_processing(bounty_id, "cicd", "validation passed", "processing")

        if not approved:
            notes = result.get("review_result", {}).get("notes", "")
            logger.warning(f"Code review rejected for bounty {bounty_id}: {notes}")
            db.log_processing(bounty_id, "cicd", f"review rejected (score: {score})", "review_failed")
        else:
            logger.info(f"Code review passed for bounty {bounty_id}, score: {score}")
            db.log_processing(bounty_id, "cicd", f"review approved (score: {score})", "processing")

        cicd_stats = result.get("token_stats", {})

        ret = {
            "validation_passed": validation_passed,
            "validation": result.get("validation", {}),
            "review_approved": approved,
            "review_score": score,
            "review_result": result.get("review_result", {}),
            "fix_cycles": result.get("fix_cycles", 0),
            "last_validation_errors": result.get("last_validation_errors", []),
            "diff_content": result.get("diff_content", state.get("diff_content", "")),
            "commit_sha": result.get("commit_sha", state.get("commit_sha", "")),
        }
        if cicd_stats.get("total_tokens", 0) > 0:
            ret["token_stats"] = cicd_stats

        if not validation_passed:
            failures = result.get("last_validation_errors", []) or result.get("validation", {}).get("failures", [])
            if failures:
                ret["error"] = f"Validation failed after {result.get('fix_cycles', 0)} fix cycles: {'; '.join(failures[:3])}"
            else:
                ret["error"] = "Validation failed after all fix cycles"
        return ret

    except Exception as e:
        logger.error(f"CI/CD Specialist failed for bounty {bounty_id}: {e}")
        db.log_processing(bounty_id, "cicd", f"error: {e}", "error")
        return {
            "validation_passed": False,
            "validation": {"overall": False, "failures": [str(e)]},
            "review_approved": False,
            "review_score": 0,
            "review_result": {"approved": False, "issues": [], "score": 0, "notes": f"CI/CD failed: {e}"},
            "last_validation_errors": [str(e)],
        }


def enqueue_review_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]

    logger.info(f"Node enqueue_review: bounty {bounty_id}")

    raw_confidence = state.get("confidence", 0.5)
    raw_score = state.get("review_score", 50)

    raw_confidence = min(1.0, max(0.0, raw_confidence))
    raw_score = min(100.0, max(0.0, raw_score))

    confidence_score = (raw_confidence * raw_score) / 100

    review_data = {
        "bounty_id": bounty_id,
        "branch_name": state.get("branch_name", ""),
        "commit_sha": state.get("commit_sha", ""),
        "diff_content": state.get("diff_content", ""),
        "agent_type": state.get("agent_type", "unknown"),
        "confidence_score": confidence_score,
        "validation_passed": state.get("validation_passed", False),
        "test_output": state.get("validation", {}).get("failures", []),
        "suggested_comment": state.get("suggested_comment", ""),
        "issue_url": bounty.get("issue_url", ""),
        "workspace_path": state.get("repo_path", ""),
    }

    db.add_to_review_queue(review_data)
    db.update_bounty_status(bounty_id, "queued_for_review")
    db.log_processing(bounty_id, "orchestrator", "queued_for_review", "queued_for_review")

    logger.info(f"Bounty {bounty_id} added to review queue")

    return {"status": "queued_for_review"}
