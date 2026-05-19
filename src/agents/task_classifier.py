from typing import Dict, Any, Tuple
from pydantic import BaseModel

from langchain_ollama import ChatOllama

from ..core.config import config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class ClassificationOutput(BaseModel):
    classification: str
    confidence: float
    reasoning: str
    estimated_files: int
    estimated_loc: int


class TaskClassifier:
    def __init__(self):
        ollama_config = config.ollama
        self.model_name = ollama_config.get('models.classifier', 'qwen2.5:0.5b')
        self.llm = ChatOllama(
            model=self.model_name,
            base_url=ollama_config.get('base_url', 'http://localhost:11434'),
            temperature=0.3,
            num_predict=512,
        ).with_structured_output(ClassificationOutput)

        self.simple_max_loc = config.get('agents.simple_task_max_loc', 50)
        self.simple_max_files = config.get('agents.simple_task_max_files', 3)

    def classify(self, bounty: Dict[str, Any]) -> Tuple[str, float]:
        title = bounty.get('title', '')
        description = bounty.get('description', '')
        repository = bounty.get('repository_name', '')
        price = bounty.get('price', 0)

        prompt = f"""You are a task classifier for a bounty hunting system.
Determine if a bounty task is SIMPLE or COMPLEX.

Classification Guidelines:
- SIMPLE: Bug fixes, typos, small features, documentation fixes, minor refactoring
  Usually 1-3 files, under 50 LOC, clear issue, no architecture changes
- COMPLEX: New features, major refactoring, architectural changes, multiple subsystems
  Many files, complex logic, unclear requirements, significant testing

Task:
Title: {title}
Description: {description}
Repository: {repository}
Price: ${price}

Classify this task."""

        try:
            result: ClassificationOutput = self.llm.invoke(prompt)

            logger.info(f"Classification result: {result.classification} ({result.confidence:.2f} confidence)")

            classification = result.classification
            confidence = result.confidence

            if classification == 'simple':
                if result.estimated_files > self.simple_max_files or result.estimated_loc > self.simple_max_loc * 2:
                    logger.info(f"Overriding to complex (files: {result.estimated_files}, loc: {result.estimated_loc})")
                    classification = 'complex'
                    confidence = max(0.3, confidence - 0.2)

            return classification, confidence

        except Exception as e:
            logger.error(f"Classification failed: {e}")
            return 'complex', 0.3

    def is_available(self) -> bool:
        try:
            from ..utils.ollama_client import OllamaClient
            ollama_config = config.ollama
            client = OllamaClient(base_url=ollama_config.get('base_url', 'http://localhost:11434'))
            return client.is_available()
        except Exception:
            return False
