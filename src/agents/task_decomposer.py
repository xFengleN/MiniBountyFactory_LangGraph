from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from langchain_ollama import ChatOllama

from ..core.config import config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class Subtask(BaseModel):
    id: int
    description: str
    type: str
    depends_on: List[int]
    estimated_complexity: str


class DecompositionOutput(BaseModel):
    subtasks: List[Subtask]
    reasoning: str


class TaskDecomposer:
    def __init__(self):
        ollama_config = config.ollama
        self.model_name = ollama_config.get('models.complex_agent', 'qwen2.5-coder:7b-instruct-q4_K_M')
        self.llm = ChatOllama(
            model=self.model_name,
            base_url=ollama_config.get('base_url', 'http://localhost:11434'),
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
- Mark subtasks as 'local' (simple, can use local LLM) or 'cloud' (needs cloud model)

Task:
Title: {title}
Description: {description}

Break it into subtasks and mark each as 'local' or 'cloud'."""

        try:
            result: DecompositionOutput = self.llm.invoke(prompt)

            local_count = sum(1 for s in result.subtasks if s.type == 'local')
            cloud_count = sum(1 for s in result.subtasks if s.type == 'cloud')

            logger.info(f"Decomposed into {len(result.subtasks)} subtasks: {local_count} local, {cloud_count} cloud")

            return [s.model_dump() for s in result.subtasks]

        except Exception as e:
            logger.error(f"Decomposition failed: {e}")
            return []

    def can_solve_locally(self, subtask: Dict[str, Any]) -> bool:
        complexity = subtask.get('estimated_complexity', 'medium')
        return complexity in ['low', 'medium'] and subtask.get('type') == 'local'
