import os
import subprocess
import tempfile
import shutil
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from pydantic import BaseModel

from langchain_ollama import ChatOllama

from ..core.config import config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class FileChange(BaseModel):
    path: str
    content: str
    action: str


class FixOutput(BaseModel):
    files: List[FileChange]
    confidence: float
    reasoning: str


class SimpleTaskAgent:
    def __init__(self):
        ollama_config = config.ollama
        self.model_name = ollama_config.get('models.simple_agent', 'llama3.2:3b')
        self.llm = ChatOllama(
            model=self.model_name,
            base_url=ollama_config.get('base_url', 'http://localhost:11434'),
            temperature=0.4,
            num_predict=4096,
        ).with_structured_output(FixOutput)

        self.git_config = config.git
        self.max_retries = config.get('agents.max_retries', 3)

    def process_bounty(self, bounty: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        bounty_id = bounty.get('id')
        title = bounty.get('title', '')
        description = bounty.get('description', '')
        repo_url = bounty.get('repository_url', '')
        issue_url = bounty.get('issue_url', '')

        if not repo_url:
            logger.error(f"No repository URL for bounty {bounty_id}")
            return None

        logger.info(f"Processing bounty {bounty_id}: {title}")

        try:
            repo_path = self._clone_repository(repo_url, bounty_id)
            if not repo_path:
                return None

            fix_result = self._generate_fix(repo_path, title, description, issue_url)

            if not fix_result or not fix_result.files:
                logger.warning(f"No fix generated for bounty {bounty_id}")
                return None

            branch_name = f"bounty-fix-{bounty_id}"
            commit_sha = self._create_commit(repo_path, branch_name, fix_result, bounty_id)

            if not commit_sha:
                return None

            diff_content = self._get_diff(repo_path)

            return {
                'bounty_id': bounty_id,
                'branch_name': branch_name,
                'commit_sha': commit_sha,
                'diff_content': diff_content,
                'files_changed': [f.model_dump() for f in fix_result.files],
                'confidence': fix_result.confidence,
                'repo_path': repo_path,
                'agent_type': 'simple',
                'model_used': self.model_name,
                'token_stats': {},
                'duration': 0,
            }

        except Exception as e:
            logger.error(f"Failed to process bounty {bounty_id}: {e}")
            return None

    def _clone_repository(self, repo_url: str, bounty_id: int) -> Optional[str]:
        workspace_base = config.get('workspace.base_path')
        if not workspace_base:
            workspace_base = str(Path(__file__).parent.parent.parent / 'bounty_workspaces')

        task_dir = Path(workspace_base) / f'bounty_{bounty_id}'
        task_dir.mkdir(parents=True, exist_ok=True)

        if (task_dir / '.git').exists():
            logger.info(f"Workspace already exists for bounty {bounty_id}, skipping clone")
            return str(task_dir)

        try:
            if 'github.com' in repo_url:
                token = self.git_config.get('token')
                if token and token != 'YOUR_GITHUB_TOKEN':
                    if repo_url.startswith('https://github.com/'):
                        repo_url = repo_url.replace('https://github.com/', f'https://{token}@github.com/')
                    elif repo_url.startswith('http://github.com/'):
                        repo_url = repo_url.replace('http://github.com/', f'https://{token}@github.com/')

            result = subprocess.run(
                ['git', 'clone', '--depth', '1', repo_url, str(task_dir)],
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode != 0:
                logger.error(f"Git clone failed: {result.stderr}")
                shutil.rmtree(task_dir, ignore_errors=True)
                return None

            return str(task_dir)

        except subprocess.TimeoutExpired:
            logger.error("Git clone timed out")
            shutil.rmtree(task_dir, ignore_errors=True)
            return None
        except Exception as e:
            logger.error(f"Clone failed: {e}")
            shutil.rmtree(task_dir, ignore_errors=True)
            return None

    def _generate_fix(
        self,
        repo_path: str,
        title: str,
        description: str,
        issue_url: str
    ) -> Optional[FixOutput]:
        files_info = self._get_repo_files(repo_path)

        prompt = f"""You are a code generation assistant. Fix bugs or implement small features based on issue descriptions.

Guidelines:
1. Only modify necessary files
2. Make minimal, focused changes
3. Follow existing code style
4. Ensure the fix compiles/runs correctly
5. Do NOT add unnecessary features or refactor unrelated code

Issue Title: {title}

Issue Description:
{description}

Issue URL: {issue_url}

Repository Files (sample):
{files_info}

Generate the fix for this issue."""

        try:
            fix_result: FixOutput = self.llm.invoke(prompt)
            logger.info(f"Fix generated: {len(fix_result.files)} files, confidence: {fix_result.confidence:.2f}")
            return fix_result

        except Exception as e:
            logger.error(f"Fix generation failed: {e}")
            return None

    def _get_repo_files(self, repo_path: str) -> str:
        try:
            result = subprocess.run(
                ['find', '.', '-type', 'f', '-name', '*.py', '-o', '-name', '*.js', '-o', '-name', '*.ts'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )

            files = result.stdout.strip().split('\n')[:20]
            return '\n'.join(files)

        except Exception:
            return "Unable to list files"

    def _create_commit(
        self,
        repo_path: str,
        branch_name: str,
        fix_result: FixOutput,
        bounty_id: int
    ) -> Optional[str]:
        try:
            result = subprocess.run(
                ['git', 'checkout', '-b', branch_name],
                cwd=repo_path, capture_output=True, text=True
            )
            if result.returncode != 0:
                subprocess.run(
                    ['git', 'checkout', branch_name],
                    cwd=repo_path, capture_output=True
                )

            _SKIP_DIRS = {'home', 'Users', 'var', 'tmp', 'etc', 'usr', 'opt'}
            for file_info in fix_result.files:
                raw_path = file_info.path.strip()
                rel_path = raw_path.lstrip('/').replace('\\', '/')
                first_part = rel_path.split('/')[0].lower()
                if first_part in _SKIP_DIRS:
                    logger.warning(f"Skipping hallucinated path: {raw_path}")
                    continue
                file_path = os.path.join(repo_path, rel_path)
                os.makedirs(os.path.dirname(file_path), exist_ok=True)

                with open(file_path, 'w') as f:
                    f.write(file_info.content)

            subprocess.run(['git', 'add', '.'], cwd=repo_path, capture_output=True)

            result = subprocess.run(
                ['git', 'commit', '-m', f'Fix bounty #{bounty_id}: Auto-generated fix'],
                cwd=repo_path,
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                logger.error(f"Git commit failed: {result.stderr}")
                return None

            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=repo_path,
                capture_output=True,
                text=True
            )

            return result.stdout.strip()

        except Exception as e:
            logger.error(f"Failed to create commit: {e}")
            return None

    def _get_diff(self, repo_path: str) -> str:
        try:
            result = subprocess.run(
                ['git', 'diff', 'HEAD~1', 'HEAD'],
                cwd=repo_path,
                capture_output=True,
                text=True
            )
            return result.stdout
        except Exception:
            return ""

    def is_available(self) -> bool:
        try:
            from ..utils.ollama_client import OllamaClient
            ollama_config = config.ollama
            client = OllamaClient(base_url=ollama_config.get('base_url', 'http://localhost:11434'))
            return client.is_available()
        except Exception:
            return False
