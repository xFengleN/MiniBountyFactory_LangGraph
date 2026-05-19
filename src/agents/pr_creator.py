import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Dict, Any

from ..core.config import config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class PRCreator:
    def __init__(self):
        self.git_config = config.git
        self.username = self.git_config.get('username')
        self.token = self.git_config.get('token')
        self.default_branch = self.git_config.get('default_branch', 'main')

    def create_pr(
        self,
        repo_url: str,
        branch_name: str,
        bounty: Dict[str, Any],
        commit_sha: str,
        workspace_path: str = None,
    ) -> Optional[str]:
        bounty_id = bounty.get('id')
        title = bounty.get('title', '')
        description = bounty.get('description', '')

        logger.info(f"Creating PR for bounty {bounty_id}, branch: {branch_name}")

        if workspace_path and Path(workspace_path).exists():
            push_ok = self._push_from_workspace(workspace_path, branch_name)
        else:
            logger.warning(f"No valid workspace at {workspace_path}, falling back to bare clone")
            push_ok = self._push_from_bare_clone(repo_url, branch_name, commit_sha)

        if not push_ok:
            return None

        pr_url = self._create_github_pr(
            repo_url,
            branch_name,
            title,
            description,
            bounty_id
        )

        if pr_url:
            logger.info(f"PR created: {pr_url}")
        return pr_url

    def _push_from_workspace(self, workspace_path: str, branch_name: str) -> bool:
        try:
            remote_url = self._format_remote_url_for_git(
                subprocess.run(
                    ['git', 'remote', 'get-url', 'origin'],
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=10
                ).stdout.strip()
            )

            result = subprocess.run(
                ['git', 'push', remote_url, branch_name],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode != 0:
                logger.error(f"Push from workspace failed: {result.stderr}")
                return False

            logger.info(f"Pushed branch {branch_name} from workspace")
            return True

        except Exception as e:
            logger.error(f"Workspace push failed: {e}")
            return False

    def _push_from_bare_clone(
        self, repo_url: str, branch_name: str, commit_sha: str
    ) -> bool:
        temp_dir = tempfile.mkdtemp(prefix='pr_create_')
        try:
            remote_url = self._format_remote_url(repo_url)

            result = subprocess.run(
                ['git', 'clone', '--bare', remote_url, temp_dir],
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode != 0:
                logger.error(f"Failed to clone repo: {result.stderr}")
                return False

            result = subprocess.run(
                ['git', 'push', 'origin', f'{commit_sha}:refs/heads/{branch_name}'],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                env={**os.environ, 'GIT_ASKPASS': '/bin/echo'}
            )

            if result.returncode != 0:
                logger.error(f"Failed to push branch: {result.stderr}")
                return False

            return True

        except Exception as e:
            logger.error(f"Bare clone push failed: {e}")
            return False
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _format_remote_url(self, repo_url: str) -> str:
        if not self.token or self.token == 'YOUR_GITHUB_TOKEN':
            return repo_url

        if 'github.com/' in repo_url:
            return repo_url.replace(
                'github.com/',
                f'github.com/{self.token}@github.com/'
            )
        elif repo_url.startswith('git@github.com:'):
            return f'https://{self.token}@github.com/{repo_url.replace("git@github.com:", "")}'

        return repo_url

    def _format_remote_url_for_git(self, remote_url: str) -> str:
        if not self.token or self.token == 'YOUR_GITHUB_TOKEN':
            return remote_url

        if remote_url.startswith('https://github.com/'):
            return remote_url.replace(
                'https://github.com/',
                f'https://{self.token}@github.com/'
            )
        elif remote_url.startswith('git@github.com:'):
            return f'https://{self.token}@github.com/{remote_url.replace("git@github.com:", "")}'

        return remote_url

    def _create_github_pr(
        self,
        repo_url: str,
        branch_name: str,
        title: str,
        description: str,
        bounty_id: int
    ) -> Optional[str]:
        if not self.token or self.token == 'YOUR_GITHUB_TOKEN':
            logger.error("GitHub token not configured")
            return None

        try:
            from github import Github

            g = Github(self.token)

            repo_path = repo_url.rstrip('/').replace('https://github.com/', '').replace('http://github.com/', '')
            repo = g.get_repo(repo_path)

            pr = repo.create_pull(
                title=f"Bounty #{bounty_id}: {title[:50]}",
                body=f"""## Bounty Fix #{bounty_id}

{description[:1000]}

---
*This PR was automatically generated by Bounty Factory*

Labels: bounty, automated-fix
""",
                head=branch_name,
                base=self.default_branch
            )

            return pr.html_url

        except ImportError:
            logger.error("PyGithub not installed")
            return self._fallback_pr_url(repo_url, branch_name)
        except Exception as e:
            logger.error(f"GitHub API error: {e}")
            return self._fallback_pr_url(repo_url, branch_name)

    def _fallback_pr_url(self, repo_url: str, branch_name: str) -> str:
        repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        return f"{repo_url.rstrip('/')}/compare/main...{branch_name}?quick_pull=1"

    def is_configured(self) -> bool:
        return bool(self.token and self.token != 'YOUR_GITHUB_TOKEN' and self.username)
