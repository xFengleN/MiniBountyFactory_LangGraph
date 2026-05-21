import os
import subprocess
import tempfile
import shutil
from typing import Dict, Any, Optional, List
from pathlib import Path
from pydantic import BaseModel
import requests

from langchain_ollama import ChatOllama

from ..core.config import config
from ..utils.logger import get_logger
from .task_decomposer import TaskDecomposer

logger = get_logger(__name__)


class FileChange(BaseModel):
    path: str
    content: str
    action: str


class FixOutput(BaseModel):
    files: List[FileChange]
    confidence: float
    reasoning: str


class ComplexTaskAgent:
    def __init__(self):
        opencode_config = config.opencode
        self.api_key = opencode_config.get('api_key')
        self.base_url = opencode_config.get('base_url', 'https://api.opencode.ai')

        if self.api_key == 'YOUR_OPENCODE_API_KEY':
            logger.warning("OpenCode API key not configured - complex tasks may fail")

        self.git_config = config.git
        self.decomposer = TaskDecomposer()

        ollama_config = config.ollama
        agents_config = config.agents
        roles = agents_config.get('roles', {})

        self.role_models = {}
        for role_name in ['junior_coder', 'super_coder', 'code_reviewer', 'tester']:
            model = roles.get(role_name, roles.get('simple_agent', 'qwen2.5-coder:7b-instruct-q4_K_M'))
            self.role_models[role_name] = model

        self.local_model_name = self.role_models.get('junior_coder', 'qwen2.5-coder:3b-instruct-q4_K_M')
        self.local_llm = ChatOllama(
            model=self.local_model_name,
            base_url=ollama_config.get('base_url', 'http://localhost:11434'),
            temperature=0.4,
            num_predict=2048,
        ).with_structured_output(FixOutput)

    def process_bounty(self, bounty: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        bounty_id = bounty.get('id')
        title = bounty.get('title', '')
        description = bounty.get('description', '')
        repo_url = bounty.get('repository_url', '')
        issue_url = bounty.get('issue_url', '')

        if not repo_url:
            logger.error(f"No repository URL for bounty {bounty_id}")
            return None

        logger.info(f"Processing complex bounty {bounty_id}: {title}")

        try:
            repo_path = self._clone_repository(repo_url, bounty_id)
            if not repo_path:
                return None

            logger.info(f"Decomposing task {bounty_id}...")
            subtasks = self.decomposer.decompose(bounty)

            if not subtasks:
                logger.warning("Decomposition failed, falling back to cloud")
                return self._process_with_cloud(repo_path, bounty, bounty_id)

            all_files = []
            role_usage = {}

            for subtask in subtasks:
                subtask_id = subtask.get('id')
                subtask_desc = subtask.get('description', '')
                subtask_role = subtask.get('role', 'junior_coder')

                role_usage[subtask_role] = role_usage.get(subtask_role, 0) + 1

                logger.info(f"Subtask {subtask_id}: {subtask_role} - {subtask_desc[:50]}...")

                if subtask_role == 'super_coder':
                    fix_result = self._solve_subtask_with_cloud(
                        repo_path, subtask_desc, bounty, subtask_id
                    )
                else:
                    fix_result = self._solve_subtask_locally(
                        repo_path, subtask_desc, bounty, subtask_id
                    )

                if fix_result:
                    all_files.extend(fix_result.get('files', []))

            if not all_files:
                logger.warning(f"No fix generated for complex bounty {bounty_id}")
                return None

            branch_name = f"bounty-fix-{bounty_id}"
            commit_sha = self._create_commit(repo_path, branch_name, {'files': all_files}, bounty_id)

            if not commit_sha:
                return None

            diff_content = self._get_diff(repo_path)

            confidence = 0.6 if role_usage.get('super_coder', 0) > 0 else 0.8

            return {
                'bounty_id': bounty_id,
                'branch_name': branch_name,
                'commit_sha': commit_sha,
                'diff_content': diff_content,
                'files_changed': all_files,
                'confidence': confidence,
                'agent_type': 'complex_hybrid',
                'role_usage': role_usage,
                'subtasks_completed': len(subtasks),
                'repo_path': repo_path
            }

        except Exception as e:
            logger.error(f"Failed to process complex bounty {bounty_id}: {e}")
            return None

    def _solve_subtask_locally(
        self,
        repo_path: str,
        subtask_desc: str,
        bounty: Dict[str, Any],
        subtask_id: int
    ) -> Optional[Dict[str, Any]]:
        prompt = f"""You are solving a specific subtask. Make minimal, focused changes.

Subtask {subtask_id}: {subtask_desc}

Original issue: {bounty.get('title', '')}
Description: {bounty.get('description', '')[:500]}

Solve this subtask."""

        try:
            fix_result: FixOutput = self.local_llm.invoke(prompt)
            return {
                'files': [f.model_dump() for f in fix_result.files],
                'confidence': fix_result.confidence,
                'reasoning': fix_result.reasoning,
            }

        except Exception as e:
            logger.error(f"Local subtask solve failed: {e}")
            return None

    def _solve_subtask_with_cloud(
        self,
        repo_path: str,
        subtask_desc: str,
        bounty: Dict[str, Any],
        subtask_id: int
    ) -> Optional[Dict[str, Any]]:
        model = self.role_models.get('super_coder', 'default')
        prompt = f"""Subtask {subtask_id} of a larger task:

Subtask: {subtask_desc}

Original issue: {bounty.get('title', '')}
Description: {bounty.get('description', '')[:1000]}

Solve this subtask with expert-level code. Return JSON:
{{"files": [{{"path": "file.py", "content": "full content", "action": "modify"}}], "confidence": 0.0-1.0}}"""

        try:
            response = requests.post(
                f"{self.base_url}/chat",
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': model,
                    'messages': [
                        {'role': 'system', 'content': 'Expert developer solving a subtask. Return JSON only.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'max_tokens': 4000
                },
                timeout=300
            )

            if response.status_code != 200:
                logger.error(f"Super coder subtask failed: {response.status_code}")
                return None

            result = response.json()
            content = result.get('choices', [{}])[0].get('message', {}).get('content', '')

            return self._parse_fix_response(content)

        except Exception as e:
            logger.error(f"Super coder subtask failed: {e}")
            return None

    def _process_with_cloud(
        self,
        repo_path: str,
        bounty: Dict[str, Any],
        bounty_id: int
    ) -> Optional[Dict[str, Any]]:
        title = bounty.get('title', '')
        description = bounty.get('description', '')
        issue_url = bounty.get('issue_url', '')

        fix_result = self._generate_fix_with_opencode(
            repo_path, title, description, issue_url, bounty_id
        )

        if not fix_result:
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
            'files_changed': fix_result.get('files', []),
            'confidence': fix_result.get('confidence', 0.7),
            'agent_type': 'complex_full_cloud',
            'role_usage': {'super_coder': 1},
            'repo_path': repo_path
        }

    def _clone_repository(self, repo_url: str, bounty_id: int = None) -> Optional[str]:
        workspace_base = config.get('workspace.base_path')
        if not workspace_base:
            workspace_base = str(Path(__file__).parent.parent.parent / 'bounty_workspaces')

        if bounty_id:
            temp_dir = str(Path(workspace_base) / f'bounty_{bounty_id}')
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
            if (Path(temp_dir) / '.git').exists():
                logger.info(f"Workspace already exists for bounty {bounty_id}, skipping clone")
                return temp_dir
        else:
            temp_dir = tempfile.mkdtemp(prefix='bounty_complex_', dir=workspace_base)

        try:
            if 'github.com' in repo_url:
                token = self.git_config.get('token')
                if token and token != 'YOUR_GITHUB_TOKEN':
                    if repo_url.startswith('https://github.com/'):
                        repo_url = repo_url.replace('https://github.com/', f'https://{token}@github.com/')
                    elif repo_url.startswith('http://github.com/'):
                        repo_url = repo_url.replace('http://github.com/', f'https://{token}@github.com/')

            result = subprocess.run(
                ['git', 'clone', '--depth', '1', repo_url, temp_dir],
                capture_output=True,
                text=True,
                timeout=180
            )

            if result.returncode != 0:
                logger.error(f"Git clone failed: {result.stderr}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return None

            return temp_dir

        except subprocess.TimeoutExpired:
            logger.error("Git clone timed out")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None
        except Exception as e:
            logger.error(f"Clone failed: {e}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None

    def _generate_fix_with_opencode(
        self,
        repo_path: str,
        title: str,
        description: str,
        issue_url: str,
        bounty_id: int
    ) -> Optional[Dict[str, Any]]:
        if not self.api_key or self.api_key == 'YOUR_OPENCODE_API_KEY':
            logger.error("OpenCode API key not configured")
            return None

        prompt = f"""You are an expert software engineer. Fix this bounty issue:

Issue Title: {title}

Issue Description:
{description}

Issue URL: {issue_url}

Repository: {repo_path}

Instructions:
1. Analyze the issue thoroughly
2. Make necessary code changes to fix the issue
3. Ensure changes compile and are correct
4. Output the changes in this format:

FILES_CHANGED:
file: path/to/file.py
---
(full file content or the specific changes)
---

After making the fix, provide:
CONFIDENCE: 0.0-1.0
REASONING: Brief explanation of what was changed and why"""

        try:
            response = requests.post(
                f"{self.base_url}/chat",
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': 'default',
                    'messages': [
                        {'role': 'system', 'content': 'You are an expert developer who writes clean, correct code.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'max_tokens': 8000
                },
                timeout=600
            )

            if response.status_code != 200:
                logger.error(f"OpenCode API error: {response.status_code} - {response.text}")
                return None

            result = response.json()
            content = result.get('choices', [{}])[0].get('message', {}).get('content', '')

            return self._parse_opencode_response(content, repo_path)

        except requests.exceptions.Timeout:
            logger.error("OpenCode request timed out")
            return None
        except Exception as e:
            logger.error(f"OpenCode request failed: {e}")
            return None

    def _parse_opencode_response(self, response: str, repo_path: str) -> Optional[Dict[str, Any]]:
        import re
        import json

        files = []
        confidence = 0.7
        reasoning = ""

        confidence_match = re.search(r'CONFIDENCE:\s*([\d.]+)', response)
        if confidence_match:
            confidence = float(confidence_match.group(1))

        reasoning_match = re.search(r'REASONING:\s*(.+?)(?=CONFIDENCE|$)', response, re.DOTALL)
        if reasoning_match:
            reasoning = reasoning_match.group(1).strip()

        parts = response.split('FILES_CHANGED:')
        if len(parts) > 1:
            files_section = parts[1]

            file_matches = list(re.finditer(r'file:\s*(.+?)(?=\n|$)', files_section))
            content_matches = list(re.finditer(r'---\s*(.+?)(?=---)', files_section, re.DOTALL))

            for i, match in enumerate(file_matches):
                file_path = match.group(1).strip()

                content = ""
                if i < len(content_matches):
                    content = content_matches[i].group(1).strip()

                if content:
                    files.append({'path': file_path, 'content': content})

        if not files:
            try:
                json_section = re.search(r'```json\s*(.+?)\s*```', response, re.DOTALL)
                if json_section:
                    files = json.loads(json_section.group(1))
                    if isinstance(files, dict):
                        files = [files]
            except:
                pass

        if files:
            return {'files': files, 'confidence': confidence, 'reasoning': reasoning}

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
                ['git', 'commit', '-m', f'Fix bounty #{bounty_id}: Complex fix via OpenCode'],
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
        if not self.api_key or self.api_key == 'YOUR_OPENCODE_API_KEY':
            return False

        try:
            response = requests.get(
                f"{self.base_url}/models",
                headers={'Authorization': f'Bearer {self.api_key}'},
                timeout=10
            )
            return response.status_code == 200
        except:
            return False
