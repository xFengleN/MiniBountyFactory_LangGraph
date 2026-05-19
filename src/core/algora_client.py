import urllib.parse
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime

from .database import db
from ..utils.logger import get_logger

logger = get_logger(__name__)


class AlgoraClient:
    def __init__(self):
        self.base_url = 'https://console.algora.io'
        self.trpc_url = f'{self.base_url}/api/trpc'

    def fetch_bounties(
        self,
        limit: int = 50,
        status: str = 'active'
    ) -> List[Dict[str, Any]]:
        try:
            params = {'json': {'limit': limit, 'status': status}}
            encoded = urllib.parse.quote(str(params).replace("'", '"'))
            url = f'{self.trpc_url}/bounty.list?input={encoded}'

            response = requests.get(url, timeout=30)

            if response.status_code != 200:
                logger.error(f'Algora tRPC error: {response.status_code}')
                return []

            data = response.json()
            result = data[0].get('result', {}) if isinstance(data, list) else data.get('result', {})
            items = result.get('data', {}).get('json', {}).get('items', [])

            bounties = []
            seen_urls = set()
            for item in items:
                task = item.get('task', {})
                org = item.get('org', {})
                reward = item.get('reward_formatted', '')

                issue_url = task.get('url') or ''

                # Skip items with no URL
                if not issue_url:
                    repo_owner = task.get('repo_owner') or ''
                    repo_name = task.get('repo_name') or ''
                    issue_number = task.get('number')
                    if repo_owner and repo_name and issue_number:
                        issue_url = f'https://github.com/{repo_owner}/{repo_name}/issues/{issue_number}'
                    else:
                        logger.debug(f'Skipping Algora item {item.get("id")}: no URL or repo info')
                        continue

                # Skip duplicates
                if issue_url in seen_urls:
                    continue
                seen_urls.add(issue_url)

                # Extract repo URL from issue URL
                # e.g. https://github.com/org/repo/issues/123 -> https://github.com/org/repo
                repo_url = issue_url
                if 'github.com' in issue_url:
                    parts = issue_url.rstrip('/').split('/')
                    if len(parts) >= 5:
                        repo_url = '/'.join(parts[:5])

                bounty = {
                    'id': f'algora-{item.get("id")}',
                    'title': task.get('title', ''),
                    'description': task.get('body', '') or '',
                    'price': self._parse_price(reward),
                    'currency': 'USD',
                    'difficulty': self._estimate_difficulty(task),
                    'repository_url': repo_url,
                    'repository_name': task.get('repo_name') or '',
                    'issue_url': issue_url,
                    'tags': ','.join(task.get('tech') or []),
                    'created_at': item.get('created_at'),
                    'github_score': 0,
                    'is_bounty': 1,
                }

                bounties.append(bounty)

            logger.info(f'Fetched {len(bounties)} bounties from Algora')
            return bounties

        except Exception as e:
            logger.error(f'Algora fetch failed: {e}')
            return []

    def _parse_price(self, reward: str) -> Optional[float]:
        if not reward:
            return None
        import re
        match = re.search(r'\$?([\d,]+)', reward)
        if match:
            return float(match.group(1).replace(',', ''))
        return None

    def _estimate_difficulty(self, task: dict) -> str:
        title = (task.get('title', '') or '').lower()
        body = (task.get('body', '') or '').lower()
        text = f'{title} {body}'

        if any(word in text for word in ['typo', 'docs', 'readme', 'minor']):
            return 'easy-1'
        if any(word in text for word in ['fix', 'update', 'add', 'simple']):
            return 'easy-2'
        if any(word in text for word in ['implement', 'feature', 'new']):
            return 'medium-2'
        if any(word in text for word in ['refactor', 'optimize', 'security']):
            return 'medium-3'
        return 'medium-2'

    def fetch_and_store_bounties(self) -> int:
        bounties = self.fetch_bounties()
        count = 0

        for bounty in bounties:
            try:
                db.add_bounty(bounty)
                count += 1
            except Exception as e:
                logger.error(f'Failed to store bounty: {e}')

        logger.info(f'Stored {count} new bounties from Algora')
        return count
