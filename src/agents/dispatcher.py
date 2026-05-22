from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from langchain_ollama import ChatOllama

from ..core.config import config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class Subtask(BaseModel):
    id: int
    description: str
    role: str
    depends_on: List[int]
    estimated_complexity: str


class DispatchOutput(BaseModel):
    mode: str
    classification: str
    confidence: float
    subtasks: Optional[List[Subtask]] = None
    reasoning: str


class Dispatcher:
    ROLES = ['simple_coder', 'super_coder']

    def __init__(self):
        self._llm = None
        self._last_model = ''
        self._last_base_url = ''

    @property
    def model_name(self):
        return config.agents.get('roles', {}).get('dispatcher', 'qwen2.5:0.5b')

    def _get_llm(self):
        model = self.model_name
        base_url = config.ollama.get('base_url', 'http://localhost:11434')
        if self._llm is None or model != self._last_model or base_url != self._last_base_url:
            self._last_model = model
            self._last_base_url = base_url
            self._llm = ChatOllama(
                model=model,
                base_url=base_url,
                temperature=0.3,
                num_predict=2048,
            ).with_structured_output(DispatchOutput)
        return self._llm

    def dispatch(self, bounty: Dict[str, Any]) -> DispatchOutput:
        title = bounty.get('title', '')
        description = bounty.get('description', '')
        repo_url = bounty.get('repository_url', '')

        prompt = f"""You are a dispatcher. Analyze the task and decide how to route it.

Guidelines:
1. Determine if the task is SIMPLE or COMPLEX
2. SIMPLE tasks: one-off file changes, boilerplate, bug fixes, dependency updates, config changes. Set mode="delegate".
3. COMPLEX tasks: multi-file architecture, cross-cutting concerns, new features, algorithmic work. Set mode="decompose".
4. For COMPLEX tasks, break into subtasks assigned to 'simple_coder' or 'super_coder':
   - simple_coder: focused file edits, unit tests, refactoring, scripts
   - super_coder: architectural changes, multi-file coordination, complex algorithms, performance work
5. Each subtask should be independently solvable. Identify dependencies.

Task:
Title: {title}
Description: {description[:2000]}
Repository: {repo_url}

Output your decision."""

        try:
            result: DispatchOutput = self._get_llm().invoke(prompt)

            role_counts = {}
            if result.subtasks:
                for s in result.subtasks:
                    role_counts[s.role] = role_counts.get(s.role, 0) + 1
                summary = ', '.join(f'{v} {k}' for k, v in sorted(role_counts.items()))
                logger.info(f"Dispatch: mode={result.mode}, classification={result.classification}, subtasks: {summary}")

            return result

        except Exception as e:
            logger.error(f"Dispatch failed: {e}")
            return DispatchOutput(
                mode='delegate',
                classification='simple',
                confidence=0.5,
                subtasks=None,
                reasoning=f'Dispatch fallback after error: {e}'
            )

    def is_available(self) -> bool:
        try:
            from ..utils.ollama_client import OllamaClient
            ollama_config = config.ollama
            client = OllamaClient(base_url=ollama_config.get('base_url', 'http://localhost:11434'))
            return client.is_available()
        except Exception:
            return False
