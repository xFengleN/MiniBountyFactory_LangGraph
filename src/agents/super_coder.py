import os
import subprocess
import shutil
import tempfile
from typing import Dict, Any, Optional, List
from pathlib import Path
from pydantic import BaseModel
import requests

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


class SuperCoder:
    def __init__(self):
        self._local_llm = None
        self._last_model = ''
        self._last_base_url = ''
        self.git_config = config.git

    @property
    def model_name(self):
        return config.agents.get('roles', {}).get('super_coder', 'qwen2.5-coder:7b-instruct-q4_K_M')

    @property
    def api_key(self):
        key = config.opencode.get('api_key', '')
        return None if (not key or key == 'YOUR_OPENCODE_API_KEY') else key

    @property
    def opencode_base_url(self):
        return config.opencode.get('base_url', 'https://api.opencode.ai')

    def _get_local_llm(self):
        model = self.model_name
        base_url = config.ollama.get('base_url', 'http://localhost:11434')
        if self._local_llm is None or model != self._last_model or base_url != self._last_base_url:
            self._last_model = model
            self._last_base_url = base_url
            self._local_llm = ChatOllama(
                model=model,
                base_url=base_url,
                temperature=0.4,
                num_predict=2048,
            ).with_structured_output(FixOutput)
        return self._local_llm

    def process_subtask(self, bounty: Dict[str, Any], subtask_description: str, subtask_id: int) -> Optional[Dict[str, Any]]:
        bounty_id = bounty.get('id')
        repo_url = bounty.get('repository_url', '')

        if not repo_url:
            logger.error(f"No repository URL for bounty {bounty_id}")
            return None

        logger.info(f"SuperCoder processing subtask {subtask_id} of bounty {bounty_id}")

        if self.api_key:
            return self._solve_with_cloud(repo_path=None, bounty=bounty, subtask_description=subtask_description, subtask_id=subtask_id)
        else:
            return self._solve_locally(bounty=bounty, subtask_description=subtask_description, subtask_id=subtask_id)

    def process(self, bounty: Dict[str, Any], subtasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        bounty_id = bounty.get('id')
        repo_url = bounty.get('repository_url', '')

        if not repo_url:
            logger.error(f"No repository URL for bounty {bounty_id}")
            return None

        if not subtasks:
            return None

        logger.info(f"SuperCoder processing {len(subtasks)} subtasks for bounty {bounty_id}")

        try:
            repo_path = self._clone_repository(repo_url, bounty_id)
            if not repo_path:
                return None

            all_files = []

            for subtask in subtasks:
                subtask_id = subtask.get('id')
                subtask_desc = subtask.get('description', '')

                logger.info(f"SuperCoder subtask {subtask_id}: {subtask_desc[:50]}...")

                if self.api_key:
                    fix_result = self._solve_subtask_with_cloud(repo_path, subtask_desc, bounty, subtask_id)
                else:
                    fix_result = self._solve_subtask_locally(repo_path, subtask_desc, bounty, subtask_id)

                if fix_result:
                    all_files.extend(fix_result.get('files', []))

            if not all_files:
                logger.warning(f"No fix generated for bounty {bounty_id}")
                return None

            branch_name = f"bounty-fix-{bounty_id}"
            commit_sha = self._create_commit(repo_path, branch_name, {'files': all_files}, bounty_id)

            if not commit_sha:
                return None

            diff_content = self._get_diff(repo_path)

            return {
                'bounty_id': bounty_id,
                'branch_name': branch_name,
                'commit_sha': commit_sha,
                'diff_content': diff_content,
                'files_changed': all_files,
                'confidence': 0.7,
                'repo_path': repo_path,
                'model_used': self.model_name,
                'token_stats': {},
                'duration': 0,
            }

        except Exception as e:
            logger.error(f"SuperCoder failed for bounty {bounty_id}: {e}")
            return None

    def _solve_subtask_locally(
        self,
        repo_path: str,
        subtask_desc: str,
        bounty: Dict[str, Any],
        subtask_id: int
    ) -> Optional[Dict[str, Any]]:
        prompt = f"""You are a senior software engineer solving a complex subtask.

Subtask {subtask_id}: {subtask_desc}

Original issue: {bounty.get('title', '')}
Description: {bounty.get('description', '')[:500]}

Guidelines:
1. Make focused, correct changes
2. Consider architecture and edge cases
3. Follow existing codebase patterns
4. Ensure type safety and proper error handling

Solve this subtask."""

        try:
            fix_result: FixOutput = self._get_local_llm().invoke(prompt)
            return {
                'files': [f.model_dump() for f in fix_result.files],
                'confidence': fix_result.confidence,
                'reasoning': fix_result.reasoning,
            }

        except Exception as e:
            logger.error(f"Local super coder subtask failed: {e}")
            return None

    def _solve_subtask_with_cloud(
        self,
        repo_path: str,
        subtask_desc: str,
        bounty: Dict[str, Any],
        subtask_id: int
    ) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            return None

        prompt = f"""Subtask {subtask_id} of a larger task:

Subtask: {subtask_desc}

Original issue: {bounty.get('title', '')}
Description: {bounty.get('description', '')[:1000]}

Solve this subtask with expert-level code. Return JSON:
{{"files": [{{"path": "file.py", "content": "full content", "action": "modify"}}], "confidence": 0.0-1.0}}"""

        try:
            response = requests.post(
                f"{self.opencode_base_url}/chat",
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': self.model_name,
                    'messages': [
                        {'role': 'system', 'content': 'Expert developer solving a subtask. Return JSON only.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'max_tokens': 4000
                },
                timeout=300
            )

            if response.status_code != 200:
                logger.error(f"Super coder cloud request failed: {response.status_code}")
                return None

            result = response.json()
            content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
            return self._parse_fix_response(content)

        except Exception as e:
            logger.error(f"Super coder cloud request failed: {e}")
            return None

    def _solve_with_cloud(
        self,
        repo_path: Optional[str],
        bounty: Dict[str, Any],
        subtask_description: str,
        subtask_id: int
    ) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            return None

        prompt = f"""Task: {subtask_description}

Original issue: {bounty.get('title', '')}
Description: {bounty.get('description', '')[:1000]}

Analyze and solve this task. Return JSON:
{{"files": [{{"path": "file.py", "content": "full content", "action": "modify"}}], "confidence": 0.0-1.0}}"""

        try:
            response = requests.post(
                f"{self.opencode_base_url}/chat",
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': self.model_name,
                    'messages': [
                        {'role': 'system', 'content': 'You are an expert software engineer. Return JSON.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'max_tokens': 4000
                },
                timeout=300
            )

            if response.status_code != 200:
                return None

            result = response.json()
            content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
            return self._parse_fix_response(content)

        except Exception as e:
            logger.error(f"Cloud solve failed: {e}")
            return None

    def _parse_fix_response(self, response: str) -> Optional[Dict[str, Any]]:
        import json
        import re

        try:
            json_start = response.find('{')
            json_end = response.rfind('}') + 1

            if json_start != -1 and json_end != 0:
                json_str = response[json_start:json_end]
                data = json.loads(json_str)

                if 'files' in data:
                    return data

                if 'path' in data and 'content' in data:
                    return {'files': [data], 'confidence': data.get('confidence', 0.6)}

        except json.JSONDecodeError:
            pass

        try:
            json_section = re.search(r'```json\s*(.+?)\s*```', response, re.DOTALL)
            if json_section:
                data = json.loads(json_section.group(1))
                if 'files' in data:
                    return data
                if 'path' in data:
                    return {'files': [data], 'confidence': 0.6}
        except:
            pass

        return None

    def _clone_repository(self, repo_url: str, bounty_id: int) -> Optional[str]:
        workspace_base = config.get('workspace.base_path')
        if not workspace_base:
            workspace_base = str(Path(__file__).parent.parent.parent / 'bounty_workspaces')

        task_dir = str(Path(workspace_base) / f'bounty_{bounty_id}')
        Path(task_dir).mkdir(parents=True, exist_ok=True)

        if (Path(task_dir) / '.git').exists():
            logger.info(f"Workspace already exists for bounty {bounty_id}, skipping clone")
            return task_dir

        try:
            if 'github.com' in repo_url:
                token = self.git_config.get('token')
                if token and token != 'YOUR_GITHUB_TOKEN':
                    if repo_url.startswith('https://github.com/'):
                        repo_url = repo_url.replace('https://github.com/', f'https://{token}@github.com/')
                    elif repo_url.startswith('http://github.com/'):
                        repo_url = repo_url.replace('http://github.com/', f'https://{token}@github.com/')

            result = subprocess.run(
                ['git', 'clone', '--depth', '1', repo_url, task_dir],
                capture_output=True,
                text=True,
                timeout=180
            )

            if result.returncode != 0:
                logger.error(f"Git clone failed: {result.stderr}")
                shutil.rmtree(task_dir, ignore_errors=True)
                return None

            return task_dir

        except subprocess.TimeoutExpired:
            logger.error("Git clone timed out")
            shutil.rmtree(task_dir, ignore_errors=True)
            return None
        except Exception as e:
            logger.error(f"Clone failed: {e}")
            shutil.rmtree(task_dir, ignore_errors=True)
            return None

    def _create_commit(
        self,
        repo_path: str,
        branch_name: str,
        fix_result: Dict[str, Any],
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
            for file_info in fix_result.get('files', []):
                raw_path = file_info['path'].strip()
                rel_path = raw_path.lstrip('/').replace('\\', '/')
                first_part = rel_path.split('/')[0].lower()
                if first_part in _SKIP_DIRS:
                    logger.warning(f"Skipping hallucinated path: {raw_path}")
                    continue
                file_path = os.path.join(repo_path, rel_path)
                os.makedirs(os.path.dirname(file_path), exist_ok=True)

                with open(file_path, 'w') as f:
                    f.write(file_info.get('content', ''))

            subprocess.run(['git', 'add', '.'], cwd=repo_path, capture_output=True)

            result = subprocess.run(
                ['git', 'commit', '-m', f'Fix bounty #{bounty_id}: Super coder fix'],
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
        if self.api_key:
            try:
                response = requests.get(
                    f"{self.opencode_base_url}/models",
                    headers={'Authorization': f'Bearer {self.api_key}'},
                    timeout=10
                )
                return response.status_code == 200
            except:
                pass

        try:
            from ..utils.ollama_client import OllamaClient
            ollama_config = config.ollama
            client = OllamaClient(base_url=ollama_config.get('base_url', 'http://localhost:11434'))
            return client.is_available()
        except Exception:
            return False
