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
            'algora_status': None,
            'algora_assignee': None,
            'active_prs': [],
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

        algora_status = self._check_algora_exclusivity(comments)
        result['algora_status'] = algora_status['status']
        result['algora_assignee'] = algora_status['assignee']
        if algora_status['status'] == 'locked':
            result['warnings'].append(
                f'Algora exclusive bounty assigned to @{algora_status["assignee"]}'
            )

        active_prs = self._check_existing_prs(owner, repo, number)
        result['active_prs'] = active_prs
        if active_prs:
            pr_summary = ', '.join(
                f'#{p["number"]} ({p["state"]})' for p in active_prs
            )
            result['warnings'].append(
                f'Active PRs found linking to this issue: {pr_summary}'
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
            r'/attempt\b',
            r'/claim\b',
        ]
        pattern = re.compile('|'.join(claim_patterns), re.IGNORECASE)

        claims = []
        now = datetime.utcnow()
        for comment in comments:
            body = comment.get('body', '')
            if pattern.search(body):
                created = datetime.fromisoformat(comment.get('created_at', '').replace('Z', '+00:00')).replace(tzinfo=None)
                hours_ago = (now - created).total_seconds() / 3600
                claims.append({
                    'user': comment.get('user', {}).get('login', 'unknown'),
                    'time': f'{int(hours_ago)}h ago',
                    'body': body[:100],
                })
        return claims

    def _check_algora_exclusivity(self, comments: List[Dict]) -> Dict[str, Any]:
        exclusive_pattern = re.compile(
            r'(?:bounty assigned to|exclusive bounty created for|exclusive to)\s+@([\w-]+)',
            re.IGNORECASE
        )
        release_pattern = re.compile(
            r'(?:bounty unassigned|exclusive cancelled|opened to all)',
            re.IGNORECASE
        )

        is_locked = False
        current_assignee: Optional[str] = None

        for comment in reversed(comments):
            user = comment.get('user', {}).get('login', '')
            body = comment.get('body', '')

            if 'algora' not in user.lower():
                continue

            match = exclusive_pattern.search(body)
            if match:
                is_locked = True
                current_assignee = match.group(1)

            if release_pattern.search(body):
                is_locked = False
                current_assignee = None

        if is_locked:
            return {'status': 'locked', 'assignee': current_assignee}
        return {'status': 'open', 'assignee': None}

    def _check_existing_prs(self, owner: str, repo: str, issue_number: int) -> List[Dict]:
        try:
            resp = requests.get(
                f'{self.base_url}/repos/{owner}/{repo}/pulls',
                headers=self.headers,
                params={
                    'state': 'open',
                    'sort': 'updated',
                    'direction': 'desc',
                    'per_page': 10,
                },
                timeout=15
            )
            if resp.status_code != 200:
                return []

            prs = resp.json()
            linked = []
            for pr in prs:
                body = pr.get('body', '') or ''
                if f'#{issue_number}' in body:
                    linked.append({
                        'number': pr['number'],
                        'title': pr.get('title', ''),
                        'state': pr.get('state', ''),
                        'draft': pr.get('draft', False),
                        'user': pr.get('user', {}).get('login', ''),
                        'created_at': pr.get('created_at', ''),
                        'ci_passing': self._check_pr_checks(owner, repo, pr['number']),
                    })
            return linked
        except Exception as e:
            logger.error(f'PR check error for {owner}/{repo}#{issue_number}: {e}')
            return []

    def _check_pr_checks(self, owner: str, repo: str, pr_number: int) -> bool:
        try:
            resp = requests.get(
                f'{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}',
                headers=self.headers,
                timeout=10
            )
            if resp.status_code != 200:
                return False
            pr_data = resp.json()
            head_sha = pr_data.get('head', {}).get('sha')
            if not head_sha:
                return False

            status_resp = requests.get(
                f'{self.base_url}/repos/{owner}/{repo}/commits/{head_sha}/status',
                headers=self.headers,
                timeout=10
            )
            if status_resp.status_code == 200:
                state = status_resp.json().get('state', '')
                return state == 'success'
        except Exception as e:
            logger.error(f'PR checks error for {owner}/{repo}#{pr_number}: {e}')
        return False

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
