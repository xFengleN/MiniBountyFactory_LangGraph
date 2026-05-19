import subprocess
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional, List

from ..utils.logger import get_logger

logger = get_logger(__name__)


class TestRunner:
    def __init__(self):
        pass

    def run_tests(
        self,
        repo_path: str,
        test_command: str = None,
        install_command: str = None,
        language: str = 'javascript'
    ) -> Dict[str, Any]:
        path = Path(repo_path)

        if not path.exists():
            logger.error(f"Repo path does not exist: {repo_path}")
            return {'success': False, 'error': 'Path not found'}

        report = {
            'repo': repo_path,
            'install_success': False,
            'tests_passed': False,
            'test_exit_code': -1,
            'important_failures': [],
            'stdout': '',
            'stderr': '',
        }

        if install_command:
            install_result = self._run_command(install_command, repo_path)
            report['install_success'] = install_result['exit_code'] == 0

            if not report['install_success']:
                report['stderr'] = install_result['stderr']
                logger.warning(f"Install failed: {install_result['stderr'][:200]}")
                return report

        if test_command:
            test_result = self._run_command(test_command, repo_path)
            report['tests_passed'] = test_result['exit_code'] == 0
            report['test_exit_code'] = test_result['exit_code']
            report['stdout'] = test_result['stdout']
            report['stderr'] = test_result['stderr']

            if not report['tests_passed']:
                report['important_failures'] = self._extract_failures(
                    test_result['stdout'] + '\n' + test_result['stderr']
                )
                logger.info(f"Tests failed. Found {len(report['important_failures'])} error lines")

        report_path = path / 'execution_report.json'
        try:
            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save execution report: {e}")

        return report

    def run_lint(
        self,
        repo_path: str,
        lint_command: str = None,
        language: str = 'javascript'
    ) -> Dict[str, Any]:
        if not lint_command:
            if language == 'javascript':
                lint_command = 'npm run lint'
            elif language == 'python':
                lint_command = 'ruff check .'

        result = self._run_command(lint_command, repo_path)

        return {
            'lint_passed': result['exit_code'] == 0,
            'lint_exit_code': result['exit_code'],
            'lint_output': result['stdout'] + result['stderr'],
        }

    def run_build(
        self,
        repo_path: str,
        build_command: str = None,
        language: str = 'javascript'
    ) -> Dict[str, Any]:
        if not build_command:
            if language == 'javascript':
                build_command = 'npm run build'
            elif language == 'python':
                build_command = 'python -m py_compile'

        result = self._run_command(build_command, repo_path)

        return {
            'build_passed': result['exit_code'] == 0,
            'build_exit_code': result['exit_code'],
            'build_output': result['stdout'] + result['stderr'],
        }

    def validate_fix(
        self,
        repo_path: str,
        repo_map: Dict[str, Any],
        fix_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        validation = {
            'install_ok': True,
            'lint_ok': True,
            'tests_ok': True,
            'build_ok': False,
            'overall': False,
            'failures': [],
        }

        install_cmd = repo_map.get('install_command')
        if install_cmd:
            install_result = self._run_command(install_cmd, repo_path)
            validation['install_ok'] = install_result['exit_code'] == 0
            if not validation['install_ok']:
                validation['failures'].append(f"Install failed: {install_result['stderr'][:200]}")

        test_cmd = repo_map.get('test_command')
        if test_cmd and repo_map.get('has_tests'):
            test_result = self._run_command(test_cmd, repo_path)
            validation['tests_ok'] = test_result['exit_code'] == 0
            if not validation['tests_ok']:
                failures = self._extract_failures(test_result['stdout'] + '\n' + test_result['stderr'])
                validation['failures'].extend(failures[:10])

        if repo_map.get('has_lint'):
            lint_result = self.run_lint(repo_path, language=repo_map.get('language', 'javascript'))
            validation['lint_ok'] = lint_result['lint_passed']
            if not validation['lint_ok']:
                validation['failures'].append(f"Lint failed: {lint_result['lint_output'][:200]}")

        validation['overall'] = (
            validation['install_ok'] and
            (validation['tests_ok'] or not repo_map.get('has_tests')) and
            (validation['lint_ok'] or not repo_map.get('has_lint'))
        )

        logger.info(f"Validation: {'PASSED' if validation['overall'] else 'FAILED'}")

        return validation

    def _run_command(self, command: str, cwd: str, timeout: int = 120) -> Dict[str, Any]:
        logger.info(f"Running: {command}")

        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            return {
                'command': command,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'exit_code': result.returncode,
            }

        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {command}")
            return {
                'command': command,
                'stdout': '',
                'stderr': 'Command timed out',
                'exit_code': -1,
            }
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return {
                'command': command,
                'stdout': '',
                'stderr': str(e),
                'exit_code': -1,
            }

    def _extract_failures(self, output: str) -> List[str]:
        output = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', output)

        failure_lines = []
        for line in output.splitlines():
            lower = line.lower()
            if any(keyword in lower for keyword in ['error', 'failed', 'expect', 'exception', 'assert']):
                failure_lines.append(line.strip())

        return failure_lines[:50]
