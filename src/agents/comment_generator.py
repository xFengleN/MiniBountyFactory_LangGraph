from typing import Dict, Any, Optional
from datetime import datetime

from ..utils.logger import get_logger

logger = get_logger(__name__)


class CommentGenerator:
    def generate_intent_comment(
        self,
        bounty: Dict[str, Any],
        check_result: Optional[Dict[str, Any]] = None
    ) -> str:
        title = bounty.get('title', '')
        issue_url = bounty.get('issue_url', '')
        repo = bounty.get('repository_name', '')

        parts = []

        parts.append(f"Hi! I'm working on this issue via an automated bounty system.")
        parts.append("")

        if check_result and check_result.get('contributing_rules'):
            parts.append(f"I've reviewed your CONTRIBUTING.md and will follow the guidelines.")
            parts.append("")

        parts.append(f"- **Approach**: I'll analyze the codebase and generate a fix")
        parts.append(f"- **Validation**: The fix will be tested before submission")
        parts.append(f"- **Timeline**: PR expected within a few hours")
        parts.append("")

        if check_result and check_result.get('is_assigned'):
            assignees = check_result.get('assignees', [])
            parts.append(f"⚠️ I see this issue is assigned to {', '.join(assignees)}.")
            parts.append(f"If this assignment is outdated, please let me know and I'll proceed.")
            parts.append("")

        if check_result and check_result.get('recent_claims'):
            claim = check_result['recent_claims'][0]
            parts.append(f"⚠️ I see @{claim['user']} claimed this {claim['time']}.")
            parts.append(f"If the issue is still available, I'll proceed with the fix.")
            parts.append("")

        parts.append(f"I'll submit a PR once the fix is validated. Please let me know if there are specific requirements I should follow.")

        return '\n'.join(parts)

    def generate_status_comment(self, status: str, bounty: Dict[str, Any]) -> str:
        title = bounty.get('title', '')

        templates = {
            'pr_submitted': f"PR has been submitted for this issue. Please review when you have a moment.",
            'fix_failed': f"I attempted to generate a fix but encountered issues. The issue may need more clarification.",
            'validation_failed': f"A fix was generated but failed validation tests. Further investigation needed.",
        }

        return templates.get(status, f"Status update: {status}")
