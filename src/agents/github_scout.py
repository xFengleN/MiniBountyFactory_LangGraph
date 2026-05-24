import os
import re
from typing import Dict, Any, List, Optional
from datetime import datetime

import requests
from ..core.database import db
from ..utils.logger import get_logger
from ..utils.http import retry

logger = get_logger(__name__)


class GitHubScout:
    def __init__(self, token: str = None):
        self.token = token or os.getenv('GITHUB_TOKEN')
        self.base_url = 'https://api.github.com'

        if not self.token or self.token.startswith('YOUR') or self.token == 'YOUR_GITHUB_TOKEN':
            logger.info("GitHub token not configured - using unauthenticated API (60 req/hr limit)")
            self.headers = {'Accept': 'application/vnd.github.v3+json'}
        else:
            self.headers = {
                'Accept': 'application/vnd.github.v3+json',
                'Authorization': f'token {self.token}'
            }

    @retry(max_retries=3, base_delay=2.0, backoff=2.0)
    def _search_github(self, **params) -> Optional[requests.Response]:
        return requests.get(
            f'{self.base_url}/search/issues',
            headers=self.headers,
            params=params,
            timeout=30
        )

    def _extract_price(self, title: str, body: str) -> Optional[float]:
        text = f"{title} {body or ''}"
        patterns = [
            r'\[(\d+)\s*USD\]',
            r'\$(\d+(?:\.\d+)?)\s*[kK]\b',
            r'\$(\d+)\b',
            r'(\d+)\s*USD\b',
            r'bounty[:\s]*\$(\d+)',
            r'reward[:\s]*\$(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                val = float(match.group(1))
                if 'k' in text[match.start():match.end()].lower():
                    val *= 1000
                return val
        return None

    def search_issues(
        self,
        query: str = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        if query is None:
            query = 'label:"good first issue" label:bug state:open'

        try:
            response = self._search_github(
                q=query,
                per_page=min(limit, 100),
                sort='created',
                order='desc'
            )

            if response is None or response.status_code != 200:
                logger.error(f"GitHub API error: {response.status_code if response else 'N/A'}")
                return []

            data = response.json()
            items = data.get('items', [])

            results = []
            for item in items[:limit]:
                score = self._calculate_score(item)
                repo = item.get('repository_url', '').split('/')[-1]
                repo_full = item.get('repository_url', '').replace(f'{self.base_url}/repos/', '')
                title = item.get('title', '')
                body = item.get('body', '')

                price = self._extract_price(title, body)
                labels = [l.get('name', '').lower() for l in item.get('labels', [])]
                is_bounty = any('bounty' in l or 'reward' in l for l in labels)

                bounty_data = {
                    'id': f'gh-{item.get("id")}',
                    'title': title,
                    'description': body or '',
                    'price': price,
                    'currency': 'USD',
                    'difficulty': self._estimate_difficulty(item),
                    'repository_url': f'https://github.com/{repo_full}',
                    'repository_name': repo_full,
                    'issue_url': item.get('html_url', ''),
                    'tags': ','.join([l.get('name', '') for l in item.get('labels', [])]),
                    'created_at': item.get('created_at'),
                    'github_score': score,
                    'repo_stars': 0,
                    'is_bounty': 1 if is_bounty else 0,
                }

                results.append(bounty_data)

            logger.info(f"GitHub scout found {len(results)} issues for query: {query[:50]}")
            return results

        except Exception as e:
            logger.error(f"GitHub search failed: {e}")
            return []

    def store_issues(self, issues: List[Dict[str, Any]]) -> int:
        count = 0

        for issue in issues:
            try:
                rowid = db.add_bounty(issue)
                if rowid:
                    count += 1
            except Exception as e:
                logger.error(f"Failed to store GitHub issue: {e}")

        logger.info(f"Stored {count} new GitHub issues (from {len(issues)} processed)")
        return count

    def fetch_and_store(self, query: str = None, limit: int = 20) -> int:
        issues = self.search_issues(query, limit)
        return self.store_issues(issues)

    def _calculate_score(self, item: dict) -> int:
        score = 0

        labels = [l.get('name', '').lower() for l in item.get('labels', [])]
        title = item.get('title', '').lower()

        if 'bug' in labels:
            score += 3

        if 'good first issue' in labels:
            score += 5

        if 'help wanted' in labels:
            score += 2

        if 'test' in title:
            score += 4

        if 'fix' in title:
            score += 2

        if 'typo' in title:
            score += 1

        return score

    def _estimate_difficulty(self, item: dict) -> str:
        labels = [l.get('name', '').lower() for l in item.get('labels', [])]
        title = item.get('title', '').lower()

        if 'good first issue' in labels or 'beginner' in labels:
            if 'typo' in title or 'readme' in title or 'docs' in title:
                return 'easy-1'
            if 'fix' in title or 'update' in title:
                return 'easy-2'
            return 'easy-3'

        if 'help wanted' in labels:
            if 'typo' in title or 'docs' in title:
                return 'medium-1'
            if 'fix' in title or 'refactor' in title:
                return 'medium-2'
            return 'medium-3'

        if any(word in title for word in ['typo', 'docs', 'readme']):
            return 'easy-1'

        if any(word in title for word in ['fix', 'update', 'add']):
            return 'easy-2'

        return 'medium-2'

    def is_available(self) -> bool:
        return True
