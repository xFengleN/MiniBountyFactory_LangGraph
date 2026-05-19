from typing import Dict, Any

from .state import BountyState
from ..agents.task_classifier import TaskClassifier
from ..agents.github_checker import GitHubIssueChecker
from ..agents.comment_generator import CommentGenerator
from ..agents.simple_agent import SimpleTaskAgent
from ..agents.complex_agent import ComplexTaskAgent
from ..agents.repo_mapper import RepoMapper
from ..agents.test_runner import TestRunner
from ..agents.code_reviewer import CodeReviewAgent
from .config import config
from .database import db
from .sandbox import run_sandbox_task, cleanup_workspace
from ..utils.logger import get_logger

logger = get_logger(__name__)

_classifier = TaskClassifier()
_github_checker = GitHubIssueChecker(config.git.get('token'))
_comment_generator = CommentGenerator()
_simple_agent = SimpleTaskAgent()
_complex_agent = ComplexTaskAgent()
_repo_mapper = RepoMapper()
_test_runner = TestRunner()
_code_reviewer = CodeReviewAgent()


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


def classify_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]

    logger.info(f"Node classify: bounty {bounty_id}")
    db.log_processing(bounty_id, "classifier", "start", "processing")

    classification, confidence = _classifier.classify(bounty)
    classification = classification.lower()

    logger.info(f"Classification: {classification} (confidence: {confidence:.2f})")
    db.log_processing(bounty_id, "classifier", f"{classification} ({confidence:.2f})", "processing")

    return {
        "classification": classification,
        "confidence": confidence,
    }


def simple_agent_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]

    logger.info(f"Node simple_agent: bounty {bounty_id}")
    db.log_processing(bounty_id, "simple_agent", "start", "processing")

    model = config.ollama.get("models.simple_agent", "qwen2.5-coder:7b-instruct-q4_K_M")
    result = run_sandbox_task(bounty, agent_type="simple", model=model)

    if result is None:
        logger.info("Sandbox unavailable, falling back to local simple agent")
        result = _simple_agent.process_bounty(bounty)

    if not result:
        logger.warning(f"Simple agent returned no result for bounty {bounty_id}")
        db.log_processing(bounty_id, "simple_agent", "no_result", "failed")
        return {"error": "Simple agent failed to generate fix", "status": "failed"}

    if not result.get("success", True):
        error = result.get("error", "Unknown error")
        logger.warning(f"Simple agent failed for bounty {bounty_id}: {error}")
        db.log_processing(bounty_id, "simple_agent", f"failed: {error}", "failed")
        return {"error": error, "status": "failed"}

    if result.get("model_used"):
        db.log_processing(bounty_id, "simple_agent", f"Model: {result['model_used']}", "processing")
    if result.get("validation"):
        db.log_processing(bounty_id, "simple_agent", "fix generated", "processing")

    db.log_processing(bounty_id, "simple_agent", "complete", "processing")

    return {
        "agent_type": "simple",
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


def complex_agent_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]

    logger.info(f"Node complex_agent: bounty {bounty_id}")
    db.log_processing(bounty_id, "complex_agent", "start", "processing")

    model = config.ollama.get("models.complex_agent", "qwen2.5-coder:7b-instruct-q4_K_M")
    subtasks = _complex_agent.decomposer.decompose(bounty)

    local_count = sum(1 for s in subtasks if s.get('type') == 'local')
    cloud_count = sum(1 for s in subtasks if s.get('type') == 'cloud')
    db.log_processing(bounty_id, "complex_agent", "decompose", "processing",
                      f"Decomposed into {len(subtasks)} subtasks: {local_count} local, {cloud_count} cloud")

    result = run_sandbox_task(
        bounty,
        agent_type="complex",
        model=model,
        subtasks=subtasks,
    )

    if result is None:
        logger.info("Sandbox unavailable, falling back to local complex agent")
        result = _complex_agent.process_bounty(bounty)

    if not result:
        logger.warning(f"Complex agent returned no result for bounty {bounty_id}")
        db.log_processing(bounty_id, "complex_agent", "no_result", "failed")
        return {"error": "Complex agent failed to generate fix", "status": "failed"}

    if not result.get("success", True):
        error = result.get("error", "Unknown error")
        logger.warning(f"Complex agent failed for bounty {bounty_id}: {error}")
        db.log_processing(bounty_id, "complex_agent", f"failed: {error}", "failed")
        return {"error": error, "status": "failed"}

    db.log_processing(bounty_id, "complex_agent", "complete", "processing")

    return {
        "agent_type": result.get("agent_type", "complex"),
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


def validate_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]
    repo_path = state.get("repo_path", "")

    if not repo_path:
        logger.warning(f"No repo_path for validation, bounty {bounty_id}")
        return {"validation_passed": True, "repo_map": {}, "validation": {"overall": True}}

    logger.info(f"Node validate: bounty {bounty_id}")
    db.log_processing(bounty_id, "validator", "start", "processing")

    repo_map = _repo_mapper.map(repo_path)

    if not repo_map:
        logger.warning(f"Repo mapping failed for bounty {bounty_id}")
        db.log_processing(bounty_id, "validator", "mapping_failed", "warning")
        return {"validation_passed": True, "repo_map": {}, "validation": {"overall": True}}

    validation = state.get("validation", {})
    if validation and validation.get("overall") is not None:
        db.log_processing(bounty_id, "validator", "passed (from sandbox)", "processing")
        return {
            "repo_map": repo_map,
            "validation": validation,
            "validation_passed": validation.get("overall", False),
        }

    fix_result = {
        "files": state.get("files_changed", []),
        "diff_content": state.get("diff_content", ""),
    }
    validation = _test_runner.validate_fix(repo_path, repo_map, fix_result)

    if not validation.get("overall", False):
        failures = validation.get("failures", ["Unknown validation error"])
        failure_detail = "; ".join(failures) if isinstance(failures, list) else str(failures)
        logger.warning(f"Validation failed for bounty {bounty_id}: {failure_detail}")
        db.log_processing(bounty_id, "validator", "failed", "validation_failed", failure_detail)
    else:
        db.log_processing(bounty_id, "validator", "passed", "processing")

    return {
        "repo_map": repo_map,
        "validation": validation,
        "validation_passed": validation.get("overall", False),
    }


def review_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]
    diff_content = state.get("diff_content", "")

    logger.info(f"Node review: bounty {bounty_id}")
    db.log_processing(bounty_id, "reviewer", "start", "processing")

    review_result = _code_reviewer.review(diff_content, bounty)

    if review_result.get("model_used"):
        db.log_processing(bounty_id, "reviewer", f"Model: {review_result['model_used']}", "processing")
    if review_result.get("token_stats"):
        stats = review_result["token_stats"]
        db.log_processing(bounty_id, "reviewer",
            f"Tokens - Prompt: {stats.get('prompt_tokens', 0)} | Completion: {stats.get('completion_tokens', 0)} | Total: {stats.get('total_tokens', 0)}", "processing")
    if review_result.get("duration"):
        db.log_processing(bounty_id, "reviewer", f"Review time: {review_result['duration']:.1f}s", "processing")

    approved = review_result.get("approved", False)
    score = review_result.get("score", 0)

    if not approved:
        logger.warning(f"Code review failed for bounty {bounty_id}: {review_result.get('notes')}")
        db.log_processing(bounty_id, "reviewer", "rejected", "review_failed")
    else:
        logger.info(f"Code review passed for bounty {bounty_id}, score: {score}")
        db.log_processing(bounty_id, "reviewer", f"approved (score: {score})", "processing")

    return {
        "review_result": review_result,
        "review_approved": approved,
        "review_score": score,
    }


def enqueue_review_node(state: BountyState) -> dict:
    bounty = state["bounty"]
    bounty_id = state["bounty_id"]

    logger.info(f"Node enqueue_review: bounty {bounty_id}")

    review_data = {
        "bounty_id": bounty_id,
        "branch_name": state.get("branch_name", ""),
        "commit_sha": state.get("commit_sha", ""),
        "diff_content": state.get("diff_content", ""),
        "agent_type": state.get("agent_type", "unknown"),
        "confidence_score": state.get("confidence", 0.5) * state.get("review_score", 50) / 100,
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
