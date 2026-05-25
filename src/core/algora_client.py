import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .database import db
from ..utils.logger import get_logger
from ..utils.http import retry
from .config import config

logger = get_logger(__name__)


class AlgoraClient:
    BOUNTY_URL = 'https://algora.io/bounties'

    def __init__(self, session_cookie: Optional[str] = None):
        self._session_cookie = session_cookie or self._resolve_cookie()
        self._session: Optional[requests.Session] = None

    def _resolve_cookie(self) -> Optional[str]:
        cookie = config.get('algora.session_cookie') or os.environ.get('ALGORA_SESSION_COOKIE') or ''
        if cookie:
            return cookie
        browser = self._try_extract_from_browsers()
        if browser:
            logger.info(f'Extracted Algora session cookie from browser')
            return browser
        return None

    def _try_extract_from_browsers(self) -> Optional[str]:
        extractors = [
            self._try_firefox,
            self._try_chrome_macos,
            self._try_brave_macos,
            self._try_chrome_linux,
            self._try_chrome_windows,
        ]
        for fn in extractors:
            try:
                result = fn()
                if result:
                    return result
            except Exception as e:
                logger.debug(f'Cookie extraction failed for {fn.__name__}: {e}')
        return None

    def _try_firefox(self) -> Optional[str]:
        profiles = Path.home() / 'Library' / 'Application Support' / 'Firefox' / 'Profiles'
        if not profiles.exists():
            return None
        import sqlite3
        for profile in sorted(profiles.iterdir()):
            if not profile.is_dir():
                continue
            db_path = profile / 'cookies.sqlite'
            if not db_path.exists():
                continue
            try:
                dst = tempfile.mktemp(suffix='.sqlite')
                shutil.copy2(str(db_path), dst)
                conn = sqlite3.connect(dst)
                row = conn.execute(
                    "SELECT value FROM moz_cookies WHERE host LIKE '%algora%' AND name='_algora_key'"
                ).fetchone()
                conn.close()
                os.unlink(dst)
                if row:
                    return row[0]
            except Exception:
                continue
        return None

    def _try_chrome_macos(self) -> Optional[str]:
        return self._read_chrome_cookies(Path.home() / 'Library' / 'Application Support' / 'Google' / 'Chrome' / 'Default')

    def _try_brave_macos(self) -> Optional[str]:
        return self._read_chrome_cookies(Path.home() / 'Library' / 'Application Support' / 'BraveSoftware' / 'Brave-Browser' / 'Default')

    def _try_chrome_linux(self) -> Optional[str]:
        return self._read_chrome_cookies(Path.home() / '.config' / 'google-chrome' / 'Default')

    def _try_chrome_windows(self) -> Optional[str]:
        return self._read_chrome_cookies(Path(os.environ.get('LOCALAPPDATA', '')) / 'Google' / 'Chrome' / 'User Data' / 'Default')

    def _read_chrome_cookies(self, cookie_dir: Path) -> Optional[str]:
        if not cookie_dir.exists():
            return None
        db_path = cookie_dir / 'Cookies'
        if not db_path.exists():
            return None
        try:
            dst = tempfile.mktemp(suffix='.sqlite')
            shutil.copy2(str(db_path), dst)
            conn = __import__('sqlite3').connect(dst)
            row = conn.execute(
                "SELECT encrypted_value FROM cookies WHERE host_key LIKE '%algora%' AND name='_algora_key'"
            ).fetchone()
            conn.close()
            if not row:
                os.unlink(dst)
                return None
            encrypted = row[0]
            os.unlink(dst)
            return self._decrypt_chrome_cookie(encrypted)
        except Exception:
            if os.path.exists(dst):
                os.unlink(dst)
            return None

    def _decrypt_chrome_cookie(self, encrypted: bytes) -> Optional[str]:
        try:
            result = subprocess.run(
                ['security', 'find-generic-password', '-w', '-s', 'Chrome Safe Storage'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return None
            key_b64 = result.stdout.strip()
            if not key_b64:
                return None
            import base64
            key = base64.b64decode(key_b64)
            if len(encrypted) > 3 and encrypted[:3] == b'v10':
                nonce = encrypted[3:15]
                ciphertext = encrypted[15:-16]
                tag = encrypted[-16:]
            else:
                return None
            try:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                aesgcm = AESGCM(key)
                plain = aesgcm.decrypt(nonce, ciphertext + tag, None)
                return plain.decode('utf-8')
            except ImportError:
                return None
        except Exception as e:
            logger.debug(f'Chrome cookie decryption failed: {e}')
            return None

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        self._session = requests.Session()
        if self._session_cookie:
            self._session.cookies.set('_algora_key', self._session_cookie, domain='algora.io')
        return self._session

    def fetch_bounties(self, limit: int = 100, status: str = '') -> List[Dict[str, Any]]:
        try:
            session = self._ensure_session()
            resp = session.get(self.BOUNTY_URL, timeout=30)

            if resp.status_code != 200:
                logger.error(f'Algora page error: {resp.status_code}')
                return []

            if not self._session_cookie:
                logger.warning(
                    'No Algora session cookie configured. '
                    'Set algora.session_cookie in config.yaml or ALGORA_SESSION_COOKIE env.\n'
                    '  1. Login at https://algora.io via GitHub OAuth\n'
                    '  2. DevTools > Application > Cookies > algora.io\n'
                    '  3. Copy _algora_key value and paste into config.yaml'
                )
                return []

            soup = BeautifulSoup(resp.text, 'html.parser')
            bounties = self._parse_bounties(soup, limit)

            if len(bounties) < 30:
                try:
                    playwright_bounties = self._fetch_with_playwright(max(limit, 30))
                    existing_urls = {b['issue_url'] for b in bounties}
                    for b in playwright_bounties:
                        if b['issue_url'] not in existing_urls:
                            bounties.append(b)
                            existing_urls.add(b['issue_url'])
                except Exception as e:
                    logger.warning(f'Playwright fallback failed: {e}')

            logger.info(f'Scraped {len(bounties)} bounties from Algora')
            return bounties

        except Exception as e:
            logger.error(f'Algora fetch failed: {e}')
            return []

    def _fetch_with_playwright(self, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning('playwright not installed, cannot scroll Algora page')
            return []

        bounties = []
        seen_urls = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            if self._session_cookie:
                ctx.add_cookies([{
                    'name': '_algora_key',
                    'value': self._session_cookie,
                    'domain': 'algora.io',
                    'path': '/'
                }])
            page = ctx.new_page()
            page.goto('https://algora.io/bounties', wait_until='networkidle')

            for _ in range(10):
                if len(bounties) >= limit:
                    break
                links = page.locator('a[href*="github.com"][href*="/issues/"]').all()
                for link in links:
                    if len(bounties) >= limit:
                        break
                    href = link.get_attribute('href')
                    if not href or href in seen_urls:
                        continue
                    price_el = link.locator('[class*="tabular-nums"]').first
                    if not price_el:
                        continue
                    price_text = price_el.text_content()
                    price = self._parse_price(price_text or '')
                    if price is None:
                        continue
                    org_el = link.locator('.font-semibold').first
                    number_el = link.locator('.text-muted-foreground').first
                    title_el = link.locator('.text-foreground').first
                    org_name = org_el.text_content() or '' if org_el else ''
                    issue_number = number_el.text_content() or '' if number_el else ''
                    title = title_el.text_content() or '' if title_el else ''
                    repo_url = '/'.join(href.rstrip('/').split('/')[:5]) if 'github.com' in href else href
                    seen_urls.add(href)
                    bounties.append({
                        'id': f'algora-pw-{len(bounties) + 1}',
                        'title': title.strip(),
                        'description': '',
                        'price': price,
                        'currency': 'USD',
                        'difficulty': self._estimate_difficulty(title or ''),
                        'repository_url': repo_url,
                        'repository_name': org_name.strip(),
                        'issue_url': href,
                        'tags': '',
                        'created_at': datetime.utcnow().isoformat(),
                        'github_score': 0,
                        'is_bounty': 1,
                    })
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                page.wait_for_timeout(2000)

            browser.close()

        logger.info(f'Playwright scraped {len(bounties)} bounties from Algora')
        return bounties[:limit]

    def _parse_bounties(self, soup: BeautifulSoup, limit: int) -> List[Dict[str, Any]]:
        bounties = []
        seen_urls = set()

        issue_links = soup.select('a[href*="github.com"][href*="/issues/"]')
        idx = 0

        for link in issue_links:
            if len(bounties) >= limit:
                break

            href = link.get('href', '')
            if not href or href in seen_urls:
                continue

            price_el = link.select_one('[class*="tabular-nums"]')
            if not price_el:
                continue
            price_text = price_el.get_text(strip=True)
            price = self._parse_price(price_text)
            if price is None:
                continue

            org_el = link.select_one('.font-semibold')
            number_el = link.select_one('.text-muted-foreground')
            title_el = link.select_one('.text-foreground')

            org_name = org_el.get_text(strip=True) if org_el else ''
            issue_number = number_el.get_text(strip=True) if number_el else ''
            title = title_el.get_text(strip=True) if title_el else ''

            repo_url = '/'.join(href.rstrip('/').split('/')[:5]) if 'github.com' in href else href

            seen_urls.add(href)
            idx += 1

            bounties.append({
                'id': f'algora-{idx}',
                'title': title,
                'description': '',
                'price': price,
                'currency': 'USD',
                'difficulty': self._estimate_difficulty(title),
                'repository_url': repo_url,
                'repository_name': org_name,
                'issue_url': href,
                'tags': '',
                'created_at': datetime.utcnow().isoformat(),
                'github_score': 0,
                'is_bounty': 1,
            })

        return bounties

    def _parse_price(self, reward: str) -> Optional[float]:
        if not reward:
            return None
        match = re.search(r'\$(\d+(?:,\d{3})*(?:\.\d+)?)', reward)
        if match:
            return float(match.group(1).replace(',', ''))
        return None

    def _estimate_difficulty(self, title: str) -> str:
        text = title.lower()
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
