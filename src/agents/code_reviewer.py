from typing import Dict, Any, Optional, List
from pydantic import BaseModel

from langchain_ollama import ChatOllama

from ..core.config import config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class ReviewIssue(BaseModel):
    severity: str
    description: str
    location: str


class ReviewOutput(BaseModel):
    approved: bool
    issues: List[ReviewIssue]
    score: int
    notes: str


class CodeReviewAgent:
    def __init__(self):
        ollama_config = config.ollama
        self.model_name = ollama_config.get('models.code_reviewer', 'llama3.2:3b')
        self.llm = ChatOllama(
            model=self.model_name,
            base_url=ollama_config.get('base_url', 'http://localhost:11434'),
            temperature=0.2,
            num_predict=2048,
        ).with_structured_output(ReviewOutput)

    def review(self, diff_content: str, bounty: Dict[str, Any]) -> Dict[str, Any]:
        title = bounty.get('title', '')
        description = bounty.get('description', '')
        repo_url = bounty.get('repository_url', '')

        prompt = f"""You are a code review agent. Review generated code changes for correctness, quality, and safety.

Review Checklist:
1. SYNTAX - Does the code compile/parse correctly?
2. LOGIC - Does the fix actually solve the issue?
3. STYLE - Does it follow the codebase conventions?
4. SECURITY - Any security vulnerabilities?
5. TESTS - Are there adequate tests?
6. EDGE CASES - What about boundary conditions?

Original Issue: {title}
Description: {description}
Repository: {repo_url}

Code Diff:
{diff_content[:4000]}

Perform a thorough review."""

        try:
            result: ReviewOutput = self.llm.invoke(prompt)

            logger.info(f"Code review complete: approved={result.approved}, score={result.score}")

            return {
                'approved': result.approved,
                'issues': [issue.model_dump() for issue in result.issues],
                'score': result.score,
                'notes': result.notes,
                'model_used': self.model_name,
                'token_stats': {},
                'duration': 0,
            }

        except Exception as e:
            logger.error(f"Code review failed: {e}")
            return {
                'approved': False,
                'issues': [{'severity': 'major', 'description': f'Review failed: {e}', 'location': 'unknown'}],
                'score': 0,
                'notes': 'Review process failed'
            }

    def is_available(self) -> bool:
        try:
            from ..utils.ollama_client import OllamaClient
            ollama_config = config.ollama
            client = OllamaClient(base_url=ollama_config.get('base_url', 'http://localhost:11434'))
            return client.is_available()
        except Exception:
            return False
