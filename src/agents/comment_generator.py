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

        parts.append(f"Will open a PR once I've got something solid. Happy to adjust if there's a particular direction you'd prefer.")

        return '\n'.join(parts)

    def generate_status_comment(self, status: str, bounty: Dict[str, Any]) -> str:
        title = bounty.get('title', '')

        templates = {
            'pr_submitted': f"Just opened a PR for this. Please have a look when you get a chance!",
            'fix_failed': f"Ran into some trouble putting together a fix — might need a bit more context on this one.",
            'validation_failed': f"Got a fix together but it didn't pass the test suite. Might need another look at the requirements.",
        }

        return templates.get(status, f"Status update: {status}")
