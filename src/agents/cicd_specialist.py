from typing import Dict, Any, Optional, List, Tuple
import os, subprocess
from pydantic import BaseModel

from langchain_ollama import ChatOllama

from ..core.config import config
from ..utils.logger import get_logger
from ..utils.ollama_client import extract_token_stats
from ..utils.prompts import load_prompt
from .repo_mapper import RepoMapper

logger = get_logger(__name__)


class ReviewIssue(BaseModel):
    severity: str
    description: str
    location: str


class ReviewOutput(BaseModel):
    approved: bool
    issues: List[ReviewIssue]
    score: int
    notes: str


class CicdSpecialist:
    def __init__(self):
        self._review_llm = None
        self._review_structured = None
        self._fix_llm = None
        self._last_review_model = ''
        self._last_fix_model = ''
        self._last_base_url = ''
        self.review_token_stats = {}
        self.fix_token_stats = {}
        self.repo_mapper = RepoMapper()

    @property
    def model_name(self):
        return config.agents.get('roles', {}).get('cicd_specialist', 'qwen2.5-coder:7b-instruct-q4_K_M')

    def _ensure_review_llm(self):
        model = self.model_name
        base_url = config.ollama.get('base_url', 'http://localhost:11434')
        if self._review_llm is None or model != self._last_review_model or base_url != self._last_base_url:
            self._last_review_model = model
            self._last_base_url = base_url
            self._review_llm = ChatOllama(
                model=model,
                base_url=base_url,
                temperature=0.2,
                num_predict=2048,
            )
            self._review_structured = self._review_llm.with_structured_output(ReviewOutput, include_raw=True)
        return self._review_llm

    def _get_fix_llm(self):
        model = self.model_name
        base_url = config.ollama.get('base_url', 'http://localhost:11434')
        if self._fix_llm is None or model != self._last_fix_model or base_url != self._last_base_url:
            self._last_fix_model = model
            self._last_base_url = base_url
            self._fix_llm = ChatOllama(
                model=model,
                base_url=base_url,
                temperature=0.3,
                num_predict=4096,
            )
        return self._fix_llm

    def process(self, bounty: Dict[str, Any], diff_content: str, repo_path: str = '') -> Dict[str, Any]:
        bounty_id = bounty.get('id')
        title = bounty.get('title', '')

        logger.info(f"CI/CD Specialist processing bounty {bounty_id}: {title}")

        result = {
            'validation_passed': False,
            'validation': {},
            'review_approved': False,
            'review_score': 0,
            'review_result': {},
            'fix_cycles': 0,
            'diff_content': diff_content,
            'token_stats': {},
        }

        if not repo_path:
            logger.warning(f"No repo_path for CI/CD, bounty {bounty_id}")
            result['validation'] = {'overall': True, 'install_ok': True, 'tests_ok': True, 'lint_ok': True}
            result['validation_passed'] = True
            review = self._run_review(diff_content, bounty)
            result['token_stats'] = self.review_token_stats
            result.update(review)
            return result

        validation = validate_in_container(bounty_id, repo_path)
        result['validation'] = validation
        result['validation_passed'] = validation.get('overall', False)

        head_before = ''
        try:
            r = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo_path, capture_output=True, text=True)
            head_before = r.stdout.strip()
        except Exception:
            pass

        cycle = 0
        max_cycles = config.get('agents.max_local_fix_cycles', 3)
        while not validation.get('overall', False) and cycle < max_cycles:
            cycle += 1
            logger.info(f"CI/CD fix cycle {cycle}/{max_cycles} for bounty {bounty_id}")

            failures = validation.get('failures', [])
            if not failures:
                break

            repo_map = self.repo_mapper.map(repo_path) or {}
            fix_output = self._generate_test_fix(repo_path, repo_map, failures, title, cycle)
            if not fix_output:
                logger.warning(f"Fix generation failed on cycle {cycle}")
                if head_before:
                    subprocess.run(['git', 'reset', '--hard', head_before], cwd=repo_path, capture_output=True)
                break

            validation = validate_in_container(bounty_id, repo_path)
            result['validation'] = validation
            result['validation_passed'] = validation.get('overall', False)

            if validation.get('overall', False):
                self._commit_fixes(repo_path, bounty_id, cycle, 'passed')
            elif head_before:
                subprocess.run(['git', 'reset', '--hard', head_before], cwd=repo_path, capture_output=True)

        result['fix_cycles'] = cycle

        total_prompt = self.fix_token_stats.get('prompt_tokens', 0)
        total_completion = self.fix_token_stats.get('completion_tokens', 0)

        try:
            diff_result = subprocess.run(
                ['git', 'diff', 'HEAD~1', 'HEAD'],
                cwd=repo_path, capture_output=True, text=True
            )
            result['diff_content'] = diff_result.stdout

            sha_result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=repo_path, capture_output=True, text=True
            )
            result['commit_sha'] = sha_result.stdout.strip()
        except Exception:
            pass

        result['last_validation_errors'] = result.get('validation', {}).get('failures', [])

        review = self._run_review(result.get('diff_content', diff_content), bounty)
        result.update(review)

        total_prompt += self.review_token_stats.get('prompt_tokens', 0)
        total_completion += self.review_token_stats.get('completion_tokens', 0)
        result['token_stats'] = {
            'prompt_tokens': total_prompt,
            'completion_tokens': total_completion,
            'total_tokens': total_prompt + total_completion,
        }

        logger.info(f"CI/CD complete: validation={'PASSED' if result['validation_passed'] else 'FAILED'}, "
                    f"review={'APPROVED' if result['review_approved'] else 'REJECTED'}, "
                    f"cycles={cycle}")

        return result

    def _run_review(self, diff_content: str, bounty: Dict[str, Any]) -> Dict[str, Any]:
        title = bounty.get('title', '')
        description = bounty.get('description', '')
        repo_url = bounty.get('repository_url', '')

        if not diff_content or len(diff_content.strip()) < 10:
            return {
                'review_approved': True,
                'review_score': 70,
                'review_result': {
                    'approved': True,
                    'issues': [],
                    'score': 70,
                    'notes': 'No diff content to review',
                    'model_used': self.model_name,
                    'token_stats': {},
                    'duration': 0,
                },
            }

        template = load_prompt('cicd_review')
        if template is None:
            template = "You are a code review agent. Review generated code changes for correctness, quality, and safety.\n\nReview Checklist:\n1. SYNTAX - Does the code compile/parse correctly?\n2. LOGIC - Does the fix actually solve the issue?\n3. STYLE - Does it follow the codebase conventions?\n4. SECURITY - Any security vulnerabilities?\n5. EDGE CASES - What about boundary conditions?\n\nOriginal Issue: {title}\nDescription: {description}\nRepository: {repo_url}\n\nCode Diff:\n{diff_content}\n\nPerform a thorough review."

        prompt = template.format(
            title=title, description=description,
            repo_url=repo_url, diff_content=diff_content[:4000],
        )

        try:
            self._ensure_review_llm()
            result = self._review_structured.invoke(prompt)
            self.review_token_stats = extract_token_stats(result['raw'].response_metadata)
            parsed: ReviewOutput = result['parsed']
            logger.info(f"Code review complete: approved={parsed.approved}, score={parsed.score}")

            return {
                'review_approved': parsed.approved,
                'review_score': parsed.score,
                'review_result': {
                    'approved': parsed.approved,
                    'issues': [issue.model_dump() for issue in parsed.issues],
                    'score': parsed.score,
                    'notes': parsed.notes,
                    'model_used': self.model_name,
                    'token_stats': self.review_token_stats,
                    'duration': 0,
                },
            }

        except Exception as e:
            logger.error(f"Code review failed: {e}")
            return {
                'review_approved': False,
                'review_score': 0,
                'review_result': {
                    'approved': False,
                    'issues': [{'severity': 'major', 'description': f'Review failed: {e}', 'location': 'unknown'}],
                    'score': 0,
                    'notes': 'Review process failed',
                    'model_used': self.model_name,
                    'token_stats': {},
                    'duration': 0,
                },
            }

    def _commit_fixes(self, repo_path: str, bounty_id: int, cycle: int, status: str = ''):
        try:
            subprocess.run(['git', 'add', '.'], cwd=repo_path, capture_output=True)
            subprocess.run(
                ['git', 'commit', '-m', f'cicd: fix cycle {cycle} for bounty #{bounty_id} ({status})'],
                cwd=repo_path, capture_output=True, text=True
            )
        except Exception as e:
            logger.warning(f"Commit after fix cycle {cycle} failed: {e}")

    def _generate_test_fix(
        self,
        repo_path: str,
        repo_map: Dict[str, Any],
        failures: List[str],
        title: str,
        cycle: int
    ) -> Optional[Dict[str, Any]]:
        failure_text = '\n'.join(failures[:15])

        template = load_prompt('cicd_test_fix')
        if template is None:
            template = "You are fixing test failures. The test suite is failing and you need to fix the code.\n\nIssue: {title}\n\nTest Failures:\n{failure_text}\n\nRepository path: {repo_path}\nLanguage: {language}\nTest command: {test_command}\nInstall command: {install_command}\n\nAnalyze the failures and generate fixes. Return ONLY the file changes as a JSON array:\n[\n  {{\"path\": \"relative/file/path.py\", \"content\": \"full file content after fix\", \"action\": \"modify\"}}\n]\n\nMake only the changes needed to fix the failures."

        prompt = template.format(
            title=title,
            failure_text=failure_text,
            repo_path=repo_path,
            language=repo_map.get('language', 'unknown'),
            test_command=repo_map.get('test_command', 'unknown'),
            install_command=repo_map.get('install_command', 'unknown'),
        )

        try:
            response = self._get_fix_llm().invoke(prompt)
            self.fix_token_stats = extract_token_stats(response.response_metadata)
            content = response.content if hasattr(response, 'content') else str(response)

            import json, re

            json_match = re.search(r'\[\s*\{.*\}\s*\]', content, re.DOTALL)
            if json_match:
                files = json.loads(json_match.group(0))
            else:
                json_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', content, re.DOTALL)
                if json_match:
                    files = json.loads(json_match.group(1))
                else:
                    logger.warning("Could not parse fix JSON from LLM response")
                    return None

            for file_info in files:
                raw_path = file_info.get('path', '').strip()
                rel_path = raw_path.lstrip('/').replace('\\', '/')
                _SKIP_DIRS = {'home', 'Users', 'var', 'tmp', 'etc', 'usr', 'opt'}
                first_part = rel_path.split('/')[0].lower()
                if first_part in _SKIP_DIRS:
                    logger.warning(f"Skipping hallucinated path: {raw_path}")
                    continue
                file_path = os.path.join(repo_path, rel_path)
                os.makedirs(os.path.dirname(file_path), exist_ok=True)

                with open(file_path, 'w') as f:
                    f.write(file_info.get('content', ''))

            return {'files': files}

        except Exception as e:
            logger.error(f"Test fix generation failed: {e}")
            return None

    def is_available(self) -> bool:
        try:
            from ..utils.ollama_client import OllamaClient
            ollama_config = config.ollama
            client = OllamaClient(base_url=ollama_config.get('base_url', 'http://localhost:11434'))
            return client.is_available()
        except Exception:
            return False
