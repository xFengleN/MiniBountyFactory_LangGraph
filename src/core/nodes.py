from typing import Dict, Any

from .state import BountyState
from ..agents.dispatcher import Dispatcher
from ..agents.github_checker import GitHubIssueChecker
from ..agents.comment_generator import CommentGenerator
from ..agents.simple_coder import SimpleCoder
from ..agents.super_coder import SuperCoder
from ..agents.cicd_specialist import CicdSpecialist
from ..agents.repo_mapper import RepoMapper
from .config import config
from .database import db
from .sandbox import run_sandbox_task
from ..utils.logger import get_logger

logger = get_logger(__name__)

_dispatcher = Dispatcher()
_github_checker = GitHubIssueChecker(config.git.get('token'))
_comment_generator = CommentGenerator()
_simple_coder = SimpleCoder()
_super_coder = SuperCoder()
_cicd_specialist = CicdSpecialist()
_repo_mapper = RepoMapper()


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

    logger.info(f"Node coder: bounty {bounty_id}, classification={classification}")

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
        }

    db.log_processing(bounty_id, "coder", f"complex mode: {len(subtasks)} subtasks", "processing")

    simple_subtasks = [s for s in subtasks if s.get('role') == 'simple_coder']
    super_subtasks = [s for s in subtasks if s.get('role') == 'super_coder']

    all_files = []
    repo_path = None
    branch_name = f"bounty-fix-{bounty_id}"
    commit_sha = None
    final_diff = ""

    if simple_subtasks:
        for subtask in simple_subtasks:
            subtask_id = subtask.get('id')
            subtask_desc = subtask.get('description', '')
            logger.info(f"Coder running simple_coder subtask {subtask_id}")

            result = _simple_coder.process(bounty, subtask_description=subtask_desc)

            if result:
                all_files.extend(result.get('files_changed', []))
                repo_path = result.get('repo_path', repo_path)
                commit_sha = result.get('commit_sha', commit_sha)

    if super_subtasks:
        result = _super_coder.process(bounty, super_subtasks)
        if result:
            all_files.extend(result.get('files_changed', []))
            repo_path = result.get('repo_path', repo_path)
            commit_sha = result.get('commit_sha', commit_sha)

    if not all_files:
        logger.warning(f"No fix generated for complex bounty {bounty_id}")
        db.log_processing(bounty_id, "coder", "no_result", "failed")
        return {"error": "No subtask produced a fix", "status": "failed"}

    if repo_path:
        try:
            import subprocess
            diff_result = subprocess.run(
                ['git', 'diff', 'HEAD~1', 'HEAD'],
                cwd=repo_path,
                capture_output=True,
                text=True
            )
            final_diff = diff_result.stdout
        except Exception:
            pass

    role_counts = {}
    for s in subtasks:
        role_counts[s.get('role', 'unknown')] = role_counts.get(s.get('role', 'unknown'), 0) + 1

    db.log_processing(bounty_id, "coder", f"complete: {len(all_files)} files", "processing")

    return {
        "agent_type": "coder",
        "repo_path": repo_path or "",
        "branch_name": branch_name,
        "commit_sha": commit_sha or "",
        "diff_content": final_diff,
        "files_changed": all_files,
        "model_used": "",
        "token_stats": {},
        "duration": 0,
        "subtasks_completed": len(subtasks),
    }


def cicd_specialist_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]
    repo_path = state.get("repo_path", "")
    diff_content = state.get("diff_content", "")

    logger.info(f"Node cicd_specialist: bounty {bounty_id}")
    db.log_processing(bounty_id, "cicd", "start", "processing")

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

        return {
            "validation_passed": validation_passed,
            "validation": result.get("validation", {}),
            "review_approved": approved,
            "review_score": score,
            "review_result": result.get("review_result", {}),
            "fix_cycles": result.get("fix_cycles", 0),
        }

    except Exception as e:
        logger.error(f"CI/CD Specialist failed for bounty {bounty_id}: {e}")
        db.log_processing(bounty_id, "cicd", f"error: {e}", "error")
        return {
            "validation_passed": False,
            "validation": {"overall": False, "failures": [str(e)]},
            "review_approved": False,
            "review_score": 0,
            "review_result": {"approved": False, "issues": [], "score": 0, "notes": f"CI/CD failed: {e}"},
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
