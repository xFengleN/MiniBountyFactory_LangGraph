import os
import subprocess
import shutil
import time
from typing import Dict, Any, Optional, List
from pathlib import Path
from pydantic import BaseModel

from langchain_ollama import ChatOllama

from ..core.config import config
from ..utils.logger import get_logger
from ..utils.ollama_client import extract_token_stats
from ..utils.prompts import load_prompt

logger = get_logger(__name__)


class FileChange(BaseModel):
    path: str
    content: str
    action: str


class FixOutput(BaseModel):
    files: List[FileChange]
    confidence: float
    reasoning: str


class SimpleCoder:
    def __init__(self):
        self._llm = None
        self._structured = None
        self._last_model = ''
        self._last_base_url = ''
        self.last_token_stats = {}
        self.git_config = config.git
        self.max_retries = config.get('agents.max_retries', 3)

    @property
    def model_name(self):
        roles = config.agents.get('roles', {})
        return roles.get('repo_coder') or roles.get('simple_coder', 'qwen2.5:0.5b')

    def _ensure_llm(self):
        model = self.model_name
        base_url = config.ollama.get('base_url', 'http://localhost:11434')
        if self._llm is None or model != self._last_model or base_url != self._last_base_url:
            self._last_model = model
            self._last_base_url = base_url
            self._llm = ChatOllama(
                model=model,
                base_url=base_url,
                temperature=0.4,
                num_predict=4096,
            )
            self._structured = self._llm.with_structured_output(FixOutput, include_raw=True)
        return self._llm

    def process(self, bounty: Dict[str, Any], subtask_description: str = None,
                repo_path: str = None, subtask_branch: str = None) -> Optional[Dict[str, Any]]:
        bounty_id = bounty.get('id')
        title = bounty.get('title', '')
        description = bounty.get('description', '')
        repo_url = bounty.get('repository_url', '')
        issue_url = bounty.get('issue_url', '')

        if not repo_url and not repo_path:
            logger.error(f"No repository URL for bounty {bounty_id}")
            return None

        tag = f"subtask: {subtask_description[:40]}..." if subtask_description else "full task"
        logger.info(f"SimpleCoder processing bounty {bounty_id} ({tag})")

        try:
            _start = time.time()

            if repo_path and subtask_branch:
                # Shared workspace mode — branch already exists, just switch to it
                subprocess.run(['git', 'checkout', subtask_branch],
                               cwd=repo_path, capture_output=True, text=True)
                used_repo = repo_path
                used_branch = subtask_branch
            else:
                used_repo = self._clone_repository(repo_url, bounty_id)
                if not used_repo:
                    return None
                used_branch = f"bounty-fix-{bounty_id}"

            fix_result = self._generate_fix(used_repo, title, description, issue_url, subtask_description)

            if not fix_result or not fix_result.files:
                logger.warning(f"No fix generated for bounty {bounty_id} ({tag})")
                return None

            commit_sha = self._create_commit(used_repo, used_branch, fix_result, bounty_id)
            if not commit_sha:
                return None

            diff_content = self._get_diff(used_repo)
            elapsed = time.time() - _start

            return {
                'bounty_id': bounty_id,
                'branch_name': used_branch,
                'commit_sha': commit_sha,
                'diff_content': diff_content,
                'files_changed': [f.model_dump() for f in fix_result.files],
                'confidence': fix_result.confidence,
                'repo_path': used_repo,
                'model_used': self.model_name,
                'token_stats': self.last_token_stats,
                'duration': elapsed,
            }

        except Exception as e:
            logger.error(f"SimpleCoder failed for bounty {bounty_id}: {e}")
            return None

    def _clone_repository(self, repo_url: str, bounty_id: int) -> Optional[str]:
        workspace_base = config.get('workspace.base_path')
        if not workspace_base:
            workspace_base = str(Path(__file__).parent.parent.parent / 'bounty_workspaces')

        task_dir = Path(workspace_base) / f'bounty_{bounty_id}'

        # Always re-clone (no skip) to avoid dirty workspace on graph retry
        if task_dir.exists():
            shutil.rmtree(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)

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
        issue_url: str,
        subtask_description: str = None
    ) -> Optional[FixOutput]:
        files_info = self._get_repo_files(repo_path)

        scope = ""
        if subtask_description:
            scope = f"\nYou are solving a specific subtask:\nSubtask: {subtask_description}\n"

        template = load_prompt('simple_coder')
        if template is None:
            template = "You are a code generation assistant. Fix bugs or implement small features based on issue descriptions.\n\nGuidelines:\n1. Only modify necessary files\n2. Make minimal, focused changes\n3. Follow existing code style\n4. Ensure the fix compiles/runs correctly\n5. Do NOT add unnecessary features or refactor unrelated code\n\nIssue Title: {title}\n\nIssue Description:\n{description}\n\nIssue URL: {issue_url}\n{scope}\nRepository Files (sample):\n{files_info}\n\nGenerate the fix for this issue."

        prompt = template.format(
            title=title, description=description or '',
            issue_url=issue_url, scope=scope, files_info=files_info,
        )

        try:
            self._ensure_llm()
            result = self._structured.invoke(prompt)
            self.last_token_stats = extract_token_stats(result['raw'].response_metadata)
            fix_result: FixOutput = result['parsed']
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
