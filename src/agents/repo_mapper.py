import json
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..utils.logger import get_logger

logger = get_logger(__name__)


class RepoMapper:
    def __init__(self):
        self.skip_dirs = {
            "node_modules", ".git", "dist", "build",
            ".svelte-kit", "coverage", ".next", "out"
        }

    def map(self, repo_path: str) -> Dict[str, Any]:
        path = Path(repo_path)

        if not path.exists():
            logger.error(f"Repo path does not exist: {repo_path}")
            return {}

        result = {
            'repo_path': repo_path,
            'language': self._detect_language(path),
            'framework': 'unknown',
            'package_manager': 'unknown',
            'test_command': None,
            'install_command': None,
            'has_tests': False,
            'has_lint': False,
            'has_format': False,
            'important_paths': [],
            'env_files': [],
            'dependencies': {},
        }

        if result['language'] == 'javascript':
            js_info = self._map_js_repo(path)
            result.update(js_info)
        elif result['language'] == 'python':
            py_info = self._map_python_repo(path)
            result.update(py_info)
        elif result['language'] == 'go':
            go_info = self._map_go_repo(path)
            result.update(go_info)

        result['important_paths'] = self._find_important_paths(path)
        result['env_files'] = self._find_env_files(path)

        logger.info(f"Mapped repo: {result['language']}, {result['framework']}, "
                    f"test: {result['test_command']}, install: {result['install_command']}")

        return result

    def _detect_language(self, path: Path) -> str:
        if (path / 'package.json').exists():
            return 'javascript'
        if (path / 'pyproject.toml').exists() or (path / 'setup.py').exists() or (path / 'requirements.txt').exists():
            return 'python'
        if (path / 'go.mod').exists():
            return 'go'
        if (path / 'Cargo.toml').exists():
            return 'rust'
        if any(path.glob('*.csproj')):
            return 'csharp'
        return 'unknown'

    def _map_js_repo(self, path: Path) -> Dict[str, Any]:
        info = {
            'framework': 'unknown',
            'package_manager': 'npm',
            'test_command': None,
            'install_command': 'npm install',
            'has_tests': False,
            'has_lint': False,
            'has_format': False,
            'dependencies': {},
        }

        try:
            with open(path / 'package.json') as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read package.json: {e}")
            return info

        scripts = data.get('scripts', {})

        dependencies = {
            **data.get('dependencies', {}),
            **data.get('devDependencies', {}),
        }
        info['dependencies'] = dependencies

        all_text = json.dumps(data).lower()

        if 'svelte-kit' in all_text or '@sveltejs/kit' in dependencies:
            info['framework'] = 'SvelteKit'
        elif 'next' in dependencies:
            info['framework'] = 'Next.js'
        elif 'react' in dependencies:
            info['framework'] = 'React'
        elif 'vue' in dependencies:
            info['framework'] = 'Vue'
        elif 'express' in dependencies:
            info['framework'] = 'Express'
        elif 'fastify' in dependencies:
            info['framework'] = 'Fastify'

        if (path / 'pnpm-lock.yaml').exists():
            info['package_manager'] = 'pnpm'
            info['install_command'] = 'pnpm install'
        elif (path / 'yarn.lock').exists():
            info['package_manager'] = 'yarn'
            info['install_command'] = 'yarn install'
        elif (path / 'bun.lockb').exists() or (path / 'bun.lock').exists():
            info['package_manager'] = 'bun'
            info['install_command'] = 'bun install'

        if 'test' in scripts:
            info['test_command'] = f"{info['package_manager']} run test"
        else:
            for script_name in scripts:
                if 'test' in script_name:
                    info['test_command'] = f"{info['package_manager']} run {script_name}"
                    break

        info['has_tests'] = any('test' in s for s in scripts)
        info['has_lint'] = any('lint' in s for s in scripts)
        info['has_format'] = any('format' in s for s in scripts)

        return info

    def _map_python_repo(self, path: Path) -> Dict[str, Any]:
        info = {
            'framework': 'unknown',
            'package_manager': 'pip',
            'test_command': None,
            'install_command': 'pip install -r requirements.txt',
            'has_tests': False,
            'has_lint': False,
            'has_format': False,
            'dependencies': {},
        }

        if (path / 'pyproject.toml').exists():
            info['install_command'] = 'pip install -e .'
        elif (path / 'setup.py').exists():
            info['install_command'] = 'pip install -e .'

        if (path / 'pytest.ini').exists() or (path / 'pyproject.toml').exists():
            info['test_command'] = 'pytest'
            info['has_tests'] = True
        elif (path / 'tox.ini').exists():
            info['test_command'] = 'tox'
            info['has_tests'] = True

        if (path / 'Makefile').exists():
            info['test_command'] = 'make test'
            info['has_tests'] = True

        if (path / 'ruff.toml').exists() or (path / '.ruff.toml').exists():
            info['has_lint'] = True
            info['has_format'] = True
        elif (path / 'pyproject.toml').exists():
            try:
                with open(path / 'pyproject.toml') as f:
                    content = f.read()
                if 'ruff' in content or 'flake8' in content or 'pylint' in content:
                    info['has_lint'] = True
                if 'black' in content or 'ruff' in content:
                    info['has_format'] = True
            except:
                pass

        return info

    def _map_go_repo(self, path: Path) -> Dict[str, Any]:
        info = {
            'framework': 'unknown',
            'package_manager': 'go',
            'test_command': 'go test ./...',
            'install_command': 'go mod download',
            'has_tests': False,
            'has_lint': False,
            'has_format': False,
            'dependencies': {},
        }

        test_dirs = [d for d in path.iterdir() if d.is_dir() and d.name.endswith('_test')]
        if test_dirs or list(path.glob('*_test.go')):
            info['has_tests'] = True

        return info

    def _find_important_paths(self, path: Path) -> List[str]:
        patterns = ['src', 'app', 'pages', 'components', 'tests', '__tests__', 'lib', 'pkg']

        important = []
        for p in path.rglob('*'):
            if any(part in self.skip_dirs for part in p.parts):
                continue
            if any(pattern in p.parts for pattern in patterns):
                important.append(str(p.relative_to(path)))

        return list(dict.fromkeys(important))[:50]

    def _find_env_files(self, path: Path) -> List[str]:
        env_patterns = ['.env', '.env.example', '.env.local', '.env.test']
        found = []

        for pattern in env_patterns:
            for env_path in path.rglob(pattern):
                found.append(str(env_path.relative_to(path)))

        return found

    def get_context_for_llm(self, repo_map: Dict[str, Any]) -> str:
        return f"""Repository Context:
- Language: {repo_map.get('language', 'unknown')}
- Framework: {repo_map.get('framework', 'unknown')}
- Package Manager: {repo_map.get('package_manager', 'unknown')}
- Install Command: {repo_map.get('install_command', 'unknown')}
- Test Command: {repo_map.get('test_command', 'none')}
- Has Tests: {repo_map.get('has_tests', False)}
- Has Lint: {repo_map.get('has_lint', False)}

Important Paths:
{chr(10).join('  - ' + p for p in repo_map.get('important_paths', [])[:20])}

Dependencies:
{json.dumps(repo_map.get('dependencies', {}), indent=2)[:500]}
"""
