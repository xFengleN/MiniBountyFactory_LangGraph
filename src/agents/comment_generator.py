import re
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

        parts.append(f"Hi! I'd like to work on this — looks like a good fit for my skills and interests.")
        parts.append("")

        if check_result and check_result.get('contributing_rules'):
            parts.append(f"I've gone through CONTRIBUTING.md and will follow the guidelines.")
            parts.append("")

        parts.append(f"I'll dig into the codebase, put together a fix, and make sure it passes tests before opening a PR.")
        parts.append("")

        if check_result and check_result.get('is_assigned'):
            assignees = check_result.get('assignees', [])
            parts.append(f"⚠️ Looks like this is assigned to {', '.join(assignees)}.")
            parts.append(f"If that's still active, no worries — just let me know.")
            parts.append("")

        if check_result and check_result.get('recent_claims'):
            claim = check_result['recent_claims'][0]
            parts.append(f"⚠️ I see @{claim['user']} picked this up {claim['time']}.")
            parts.append(f"If it's still up for grabs, I'll get started.")
            parts.append("")

        bot_comment = check_result.get('algora_bot_comment') if check_result else None
        if bot_comment:
            attempt_match = re.search(r'/attempt\s+#\d+', bot_comment)
            if attempt_match:
                parts.append(f"I'll start by commenting `{attempt_match.group()}` with my implementation plan as noted in the bounty.")
                parts.append("")
            claim_match = re.search(r'/claim\s+#\d+', bot_comment)
            if claim_match:
                parts.append(f"I'll include `{claim_match.group()}` in the PR body to claim the bounty upon submission.")
                parts.append("")

        parts.append(f"Will open a PR once I've got something solid. Happy to adjust if there's a particular direction you'd prefer.")

        return '\n'.join(parts)

    def generate_attempt_comment(
        self,
        issue_number: int,
        title: str = '',
        description: str = '',
        repo_url: str = '',
        bot_comment: str = '',
        check_result: Optional[Dict[str, Any]] = None,
    ) -> str:
        parts = []
        parts.append(f"/attempt #{issue_number}")
        parts.append("")
        parts.append("Implementation plan:")
        parts.append("")

        desc_short = (description or '')[:500]
        if desc_short:
            parts.append(f"- Investigate the reported issue: {desc_short}")
        else:
            parts.append(f"- Reproduce the issue locally and trace the failure path")

        parts.append(f"- Understand the existing codebase architecture and relevant components")
        parts.append(f"- Implement a minimal fix aligned with existing conventions and coding style")
        parts.append(f"- Add test coverage for the fix and validate against CI suite")
        parts.append("")

        if check_result and check_result.get('contributing_rules'):
            parts.append(f"I've reviewed CONTRIBUTING.md and will follow the guidelines.")
            parts.append("")

        parts.append(f"I'll keep the implementation scoped to this issue. Happy to adjust based on feedback.")

        return '\n'.join(parts)

    def generate_status_comment(self, status: str, bounty: Dict[str, Any]) -> str:
        title = bounty.get('title', '')

        templates = {
            'pr_submitted': f"Just opened a PR for this. Please have a look when you get a chance!",
            'fix_failed': f"Ran into some trouble putting together a fix — might need a bit more context on this one.",
            'validation_failed': f"Got a fix together but it didn't pass the test suite. Might need another look at the requirements.",
        }

        return templates.get(status, f"Status update: {status}")
