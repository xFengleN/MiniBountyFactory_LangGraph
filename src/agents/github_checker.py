import os
import re
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

import requests

from ..core.config import config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class GitHubIssueChecker:
    def __init__(self, token: str = None):
        self.token = token or os.getenv('GITHUB_TOKEN')
        self.base_url = 'https://api.github.com'

        if self.token and self.token != 'YOUR_GITHUB_TOKEN':
            self.headers = {
                'Accept': 'application/vnd.github.v3+json',
                'Authorization': f'token {self.token}'
            }
        else:
            self.headers = {'Accept': 'application/vnd.github.v3+json'}

    def check_issue(self, issue_url: str) -> Dict[str, Any]:
        owner, repo, number = self._parse_issue_url(issue_url)
        if not owner or not repo or not number:
            return {'valid': False, 'error': 'Invalid issue URL'}

        result = {
            'valid': True,
            'owner': owner,
            'repo': repo,
            'number': number,
            'is_assigned': False,
            'assignees': [],
            'recent_claims': [],
            'has_contributing': False,
            'contributing_rules': '',
            'warnings': [],
        }

        issue_data = self._fetch_issue(owner, repo, number)
        if not issue_data:
            result['valid'] = False
            result['error'] = 'Failed to fetch issue'
            return result

        assignees = issue_data.get('assignees', [])
        if assignees:
            result['is_assigned'] = True
            result['assignees'] = [a.get('login', '') for a in assignees]
            result['warnings'].append(
                f'Issue is assigned to: {", ".join(result["assignees"])}'
            )

        comments = self._fetch_comments(owner, repo, number)
        recent_claims = self._detect_claims(comments)
        if recent_claims:
            result['recent_claims'] = recent_claims
            result['warnings'].append(
                f'Recent claim detected: {recent_claims[0]["user"]} ({recent_claims[0]["time"]})'
            )

        contributing = self._fetch_contributing(owner, repo)
        if contributing:
            result['has_contributing'] = True
            result['contributing_rules'] = self._extract_bounty_rules(contributing)

        return result

    def _parse_issue_url(self, url: str) -> tuple:
        match = re.search(r'github\.com/([^/]+)/([^/]+)/issues/(\d+)', url)
        if match:
            return match.group(1), match.group(2), int(match.group(3))
        return None, None, None

    def _fetch_issue(self, owner: str, repo: str, number: int) -> Optional[Dict]:
        try:
            resp = requests.get(
                f'{self.base_url}/repos/{owner}/{repo}/issues/{number}',
                headers=self.headers,
                timeout=15
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f'Issue fetch failed: {resp.status_code}')
        except Exception as e:
            logger.error(f'Issue fetch error: {e}')
        return None

    def _fetch_comments(self, owner: str, repo: str, number: int) -> List[Dict]:
        try:
            resp = requests.get(
                f'{self.base_url}/repos/{owner}/{repo}/issues/{number}/comments',
                headers=self.headers,
                params={'per_page': 20, 'sort': 'created', 'direction': 'desc'},
                timeout=15
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f'Comments fetch error: {e}')
        return []

    def _detect_claims(self, comments: List[Dict]) -> List[Dict]:
        claim_patterns = [
            r'i.?m working on',
            r'i.?ll work on',
            r'i.?ll take this',
            r'assign this to me',
            r'can i work on',
            r'i.?d like to work',
            r'let me try',
            r'working on a fix',
            r'pr incoming',
        ]
        pattern = re.compile('|'.join(claim_patterns), re.IGNORECASE)

        claims = []
        now = datetime.utcnow()
        for comment in comments:
            body = comment.get('body', '')
            if pattern.search(body):
                created = datetime.fromisoformat(comment.get('created_at', '').replace('Z', '+00:00')).replace(tzinfo=None)
                hours_ago = (now - created).total_seconds() / 3600
                if hours_ago < 48:
                    claims.append({
                        'user': comment.get('user', {}).get('login', 'unknown'),
                        'time': f'{int(hours_ago)}h ago',
                        'body': body[:100],
                    })
        return claims

    def _fetch_contributing(self, owner: str, repo: str) -> Optional[str]:
        for path in ['CONTRIBUTING.md', 'contributing.md', '.github/CONTRIBUTING.md']:
            try:
                resp = requests.get(
                    f'{self.base_url}/repos/{owner}/{repo}/contents/{path}',
                    headers=self.headers,
                    timeout=15
                )
                if resp.status_code == 200:
                    data = resp.json()
                    import base64
                    content = base64.b64decode(data.get('content', '')).decode('utf-8')
                    return content
            except Exception:
                continue
        return None

    def _extract_bounty_rules(self, content: str) -> str:
        lines = content.split('\n')
        bounty_section = []
        in_section = False

        for line in lines:
            lower = line.lower()
            if any(kw in lower for kw in ['bounty', 'reward', 'assignment', 'claim']):
                in_section = True
            if in_section:
                bounty_section.append(line)
                if line.strip() == '' and len(bounty_section) > 3:
                    break

        return '\n'.join(bounty_section[:10]) if bounty_section else ''
