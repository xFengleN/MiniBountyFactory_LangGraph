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


class DecompositionOutput(BaseModel):
    subtasks: List[Subtask]
    reasoning: str


class TaskDecomposer:
    ROLES = ['junior_coder', 'super_coder']

    def __init__(self):
        agents_config = config.agents
        roles = agents_config.get('roles', {})
        self.model_name = roles.get('complex_agent', 'qwen2.5-coder:7b-instruct-q4_K_M')
        self.llm = ChatOllama(
            model=self.model_name,
            base_url=config.ollama.get('base_url', 'http://localhost:11434'),
            temperature=0.3,
            num_predict=1024,
        ).with_structured_output(DecompositionOutput)

    def decompose(self, bounty: Dict[str, Any]) -> List[Dict[str, Any]]:
        title = bounty.get('title', '')
        description = bounty.get('description', '')

        prompt = f"""You are a task decomposer. Break down complex tasks into smaller, manageable subtasks.

Guidelines:
- Each subtask should be solvable independently
- Identify dependencies between subtasks
- Assign each subtask to a role: 'junior_coder' (simple, routine changes) or 'super_coder' (complex, architectural changes)

Task:
Title: {title}
Description: {description}

Break it into subtasks and assign each to 'junior_coder' or 'super_coder'."""

        try:
            result: DecompositionOutput = self.llm.invoke(prompt)

            role_counts = {}
            for s in result.subtasks:
                role_counts[s.role] = role_counts.get(s.role, 0) + 1
            summary = ', '.join(f'{v} {k}' for k, v in sorted(role_counts.items()))

            logger.info(f"Decomposed into {len(result.subtasks)} subtasks: {summary}")

            return [s.model_dump() for s in result.subtasks]

        except Exception as e:
            logger.error(f"Decomposition failed: {e}")
            return []

    def can_solve_locally(self, subtask: Dict[str, Any]) -> bool:
        complexity = subtask.get('estimated_complexity', 'medium')
        return complexity in ['low', 'medium']
