from typing import Dict, Any, Optional

from .task_classifier import TaskClassifier
from .simple_agent import SimpleTaskAgent
from .complex_agent import ComplexTaskAgent
from ..utils.logger import get_logger

logger = get_logger(__name__)


class AgentRouter:
    def __init__(self):
        self.classifier = TaskClassifier()
        self.simple_agent = SimpleTaskAgent()
        self.complex_agent = ComplexTaskAgent()

    def route_and_process(self, bounty: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        bounty_id = bounty.get('id')
        title = bounty.get('title', '')

        logger.info(f"Routing bounty {bounty_id}: {title}")

        classification, confidence = self.classifier.classify(bounty)
        logger.info(f"Bounty {bounty_id} classified as {classification} (confidence: {confidence:.2f})")

        if classification == 'simple':
            if not self.simple_agent.is_available():
                logger.warning("Simple agent not available, trying complex agent")
                return self.complex_agent.process_bounty(bounty)

            logger.info(f"Processing bounty {bounty_id} with Simple Agent")
            result = self.simple_agent.process_bounty(bounty)

            if result:
                result['agent_type'] = 'simple'
                return result
            else:
                logger.warning(f"Simple agent failed, falling back to complex")
                return self.complex_agent.process_bounty(bounty)
        else:
            if not self.complex_agent.is_available():
                logger.error("Complex agent not available")
                return None

            logger.info(f"Processing bounty {bounty_id} with Complex Agent")
            result = self.complex_agent.process_bounty(bounty)

            if result:
                result['agent_type'] = 'complex'
                return result

            logger.warning(f"Complex agent failed, trying simple agent")
            result = self.simple_agent.process_bounty(bounty)
            if result:
                result['agent_type'] = 'simple_fallback'
                return result

            return None

    def get_status(self) -> Dict[str, Any]:
        return {
            'classifier_available': self.classifier.is_available(),
            'simple_agent_available': self.simple_agent.is_available(),
            'complex_agent_available': self.complex_agent.is_available()
        }