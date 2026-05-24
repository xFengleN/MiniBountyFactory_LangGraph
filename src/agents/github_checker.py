import os
import re
import time
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime, timedelta

import requests

from ..core.config import config
from ..utils.logger import get_logger
from ..utils.http import retry

logger = get_logger(__name__)


def _rate_limited(method: Callable) -> Callable:
    return retry(max_retries=3, base_delay=2.0, backoff=2.0)(method)


class GitHubIssueChecker:
    def __init__(self, token: str = None):
        self.token = token or os.getenv('GITHUB_TOKEN')
        self.base_url = 'https://api.github.com'
        self._cache: Dict[str, Any] = {}
        self._cache_ttl: float = 60.0

        if self.token and self.token != 'YOUR_GITHUB_TOKEN':
            self.headers = {
                'Accept': 'application/vnd.github.v3+json',
                'Authorization': f'token {self.token}'
            }
        else:
            self.headers = {'Accept': 'application/vnd.github.v3+json'}

    def _cache_get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry and time.time() - entry['ts'] < self._cache_ttl:
            return entry['val']
        return None

    def _cache_set(self, key: str, val: Any):
        self._cache[key] = {'val': val, 'ts': time.time()}

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
            'algora_bot_comment': None,
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

        bot_comment = next((c for c in comments if c.get('user', {}).get('login') == 'algora-pbc[bot]'), None)
        if bot_comment:
            result['algora_bot_comment'] = bot_comment.get('body', '')

        active_prs = self._check_existing_prs(owner, repo, number)
        if not active_prs:
            active_prs = self._find_prs_in_comments(comments)
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

    @_rate_limited
    def _do_get(self, url: str, **kwargs) -> Optional[requests.Response]:
        return requests.get(url, headers=self.headers, timeout=15, **kwargs)

    def _fetch_issue(self, owner: str, repo: str, number: int) -> Optional[Dict]:
        cache_key = f'issue:{owner}/{repo}#{number}'
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        try:
            resp = self._do_get(
                f'{self.base_url}/repos/{owner}/{repo}/issues/{number}'
            )
            if resp and resp.status_code == 200:
                data = resp.json()
                self._cache_set(cache_key, data)
                return data
            logger.warning(f'Issue fetch failed: {resp.status_code if resp else "N/A"}')
        except Exception as e:
            logger.error(f'Issue fetch error: {e}')
        return None

    def _fetch_comments(self, owner: str, repo: str, number: int) -> List[Dict]:
        cache_key = f'comments:{owner}/{repo}#{number}'
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        try:
            resp = self._do_get(
                f'{self.base_url}/repos/{owner}/{repo}/issues/{number}/comments',
                params={'per_page': 20, 'sort': 'created', 'direction': 'desc'}
            )
            if resp and resp.status_code == 200:
                data = resp.json()
                self._cache_set(cache_key, data)
                return data
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

        bot_users = {'algora-pbc[bot]', 'github-actions[bot]', 'github-actions'}

        claims = []
        now = datetime.utcnow()
        for comment in comments:
            user = comment.get('user', {}).get('login', '')
            if user in bot_users:
                continue
            body = comment.get('body', '')
            if pattern.search(body):
                created = datetime.fromisoformat(comment.get('created_at', '').replace('Z', '+00:00')).replace(tzinfo=None)
                hours_ago = (now - created).total_seconds() / 3600
                claims.append({
                    'user': user,
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
        cache_key = f'prs:{owner}/{repo}'
        cached = self._cache_get(cache_key)
        try:
            if not cached:
                resp = self._do_get(
                    f'{self.base_url}/repos/{owner}/{repo}/pulls',
                    params={
                        'state': 'open',
                        'sort': 'updated',
                        'direction': 'desc',
                        'per_page': 100,
                    }
                )
                if not resp or resp.status_code != 200:
                    return []
                cached = resp.json()
                self._cache_set(cache_key, cached)

            issue_ref = f'#{issue_number}'
            linked = []
            for pr in cached:
                body = pr.get('body', '') or ''
                title = pr.get('title', '') or ''
                if issue_ref in body or issue_ref in title:
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
        cache_key = f'prchecks:{owner}/{repo}#{pr_number}'
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            resp = self._do_get(
                f'{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}'
            )
            if not resp or resp.status_code != 200:
                return False
            pr_data = resp.json()
            head_sha = pr_data.get('head', {}).get('sha')
            if not head_sha:
                return False

            status_resp = self._do_get(
                f'{self.base_url}/repos/{owner}/{repo}/commits/{head_sha}/status'
            )
            if status_resp and status_resp.status_code == 200:
                state = status_resp.json().get('state', '')
                return state == 'success'
        except Exception as e:
            logger.error(f'PR checks error for {owner}/{repo}#{pr_number}: {e}')
        return False

    def _find_prs_in_comments(self, comments: List[Dict]) -> List[Dict]:
        pr_pattern = re.compile(r'(?:PR|pull request|#)\s*(\d+)', re.IGNORECASE)
        seen = set()
        found = []
        for comment in comments:
            body = comment.get('body', '')
            for match in pr_pattern.finditer(body):
                pr_num = int(match.group(1))
                if pr_num not in seen and pr_num < 100000:
                    seen.add(pr_num)
                    found.append({
                        'number': pr_num,
                        'title': f'Referenced in comment by {comment.get("user", {}).get("login", "?")}',
                        'state': 'mentioned',
                        'draft': False,
                        'user': comment.get('user', {}).get('login', ''),
                        'created_at': comment.get('created_at', ''),
                        'ci_passing': False,
                    })
        return found

    def post_comment(self, issue_url: str, body: str) -> bool:
        owner, repo, number = self._parse_issue_url(issue_url)
        if not owner or not repo or not number:
            logger.error(f'Cannot post comment: invalid issue URL {issue_url}')
            return False
        try:
            resp = requests.post(
                f'{self.base_url}/repos/{owner}/{repo}/issues/{number}/comments',
                headers=self.headers,
                json={'body': body},
                timeout=15
            )
            if resp.status_code in (200, 201):
                logger.info(f'Comment posted to {issue_url}')
                return True
            logger.error(f'Failed to post comment: {resp.status_code} {resp.text[:200]}')
            return False
        except Exception as e:
            logger.error(f'Post comment error: {e}')
            return False

    @_rate_limited
    def _fetch_contributing_raw(self, url: str) -> Optional[requests.Response]:
        return requests.get(url, headers=self.headers, timeout=15)

    def _fetch_contributing(self, owner: str, repo: str) -> Optional[str]:
        for path in ['CONTRIBUTING.md', 'contributing.md', '.github/CONTRIBUTING.md']:
            try:
                resp = self._fetch_contributing_raw(
                    f'{self.base_url}/repos/{owner}/{repo}/contents/{path}'
                )
                if resp and resp.status_code == 200:
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
