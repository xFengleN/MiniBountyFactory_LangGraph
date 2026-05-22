from typing import TypedDict, Optional, List, Dict, Any


class BountyState(TypedDict, total=False):
    bounty_id: int
    bounty: Dict[str, Any]

    precheck_result: Optional[Dict[str, Any]]
    suggested_comment: str

    classification: str
    confidence: float

    agent_type: str
    repo_path: str
    branch_name: str
    commit_sha: str
    diff_content: str
    files_changed: List[Dict[str, Any]]
    model_used: str
    token_stats: Dict[str, Any]
    duration: float

    repo_map: Dict[str, Any]
    validation: Dict[str, Any]
    validation_passed: bool

    review_result: Dict[str, Any]
    review_approved: bool
    review_score: float

    retry_count: int
    last_validation_errors: List[str]
    subtask_branches: List[str]

    error: str
    status: str
