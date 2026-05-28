from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from langchain_ollama import ChatOllama

from ..core.config import config
from ..utils.logger import get_logger
from ..utils.ollama_client import extract_token_stats
from ..utils.prompts import load_prompt

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
    ROLES = ['repo_coder']

    def __init__(self):
        self._llm = None
        self._structured = None
        self._last_model = ''
        self._last_base_url = ''
        self.last_token_stats = {}

    @property
    def model_name(self):
        return config.agents.get('roles', {}).get('dispatcher', 'qwen2.5:0.5b')

    def _ensure_llm(self):
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
            )
            self._structured = self._llm.with_structured_output(DispatchOutput, include_raw=True)
        return self._llm

    def dispatch(self, bounty: Dict[str, Any]) -> DispatchOutput:
        title = bounty.get('title', '')
        description = bounty.get('description', '')
        repo_url = bounty.get('repository_url', '')

        template = load_prompt('dispatcher')
        if template is None:
            template = "You are a dispatcher. Analyze the task and decide how to route it.\n\nGuidelines:\n1. Determine if the task is SIMPLE or COMPLEX\n2. SIMPLE tasks: one-off file changes, boilerplate, bug fixes, dependency updates, config changes. Set mode=\"delegate\".\n3. COMPLEX tasks: multi-file architecture, cross-cutting concerns, new features, algorithmic work. Set mode=\"decompose\".\n4. For COMPLEX tasks, break into subtasks assigned to 'repo_coder' only.\n5. Each subtask should be independently solvable. Identify dependencies.\n\nTask:\nTitle: {title}\nDescription: {description}\nRepository: {repo_url}\n\nOutput your decision."

        desc_short = (description or '')[:2000]
        prompt = template.format(title=title, description=desc_short, repo_url=repo_url)

        try:
            self._ensure_llm()
            result = self._structured.invoke(prompt)
            ai_msg = result['raw']
            parsed = result['parsed']
            self.last_token_stats = extract_token_stats(ai_msg.response_metadata)

            role_counts = {}
            if parsed.subtasks:
                for s in parsed.subtasks:
                    role_counts[s.role] = role_counts.get(s.role, 0) + 1
                summary = ', '.join(f'{v} {k}' for k, v in sorted(role_counts.items()))
                logger.info(f"Dispatch: mode={parsed.mode}, classification={parsed.classification}, subtasks: {summary}")

            return parsed

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
