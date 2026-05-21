import os
import yaml
from typing import Any, Dict, Optional
from pathlib import Path


class Config:
    _instance: Optional['Config'] = None
    _config: Dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._config:
            self.load()

    def load(self, config_path: str = None):
        if config_path is None:
            config_path = os.environ.get(
                'BOUNTY_FACTORY_CONFIG',
                Path(__file__).parent.parent.parent / 'config' / 'config.yaml'
            )

        with open(config_path, 'r') as f:
            self._config = yaml.safe_load(f)

        self._expand_paths()

    def _expand_paths(self):
        base_dir = Path(__file__).parent.parent.parent
        for key in ['database', 'logging']:
            if key in self._config and 'path' in self._config[key]:
                path = self._config[key]['path']
                if not os.path.isabs(path):
                    self._config[key]['path'] = str(base_dir / path)

        log_file = self._config.get('logging', {}).get('file')
        if log_file and not os.path.isabs(log_file):
            self._config['logging']['file'] = str(base_dir / log_file)

        workspace_path = self._config.get('workspace', {}).get('base_path')
        if workspace_path and not os.path.isabs(workspace_path):
            self._config['workspace']['base_path'] = str((base_dir / workspace_path).resolve())

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split('.')
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value

    @property
    def ollama(self) -> Dict[str, Any]:
        return self._config.get('ollama', {})

    @property
    def opencode(self) -> Dict[str, Any]:
        return self._config.get('opencode', {})

    @property
    def database(self) -> Dict[str, Any]:
        return self._config.get('database', {})

    @property
    def git(self) -> Dict[str, Any]:
        return self._config.get('git', {})

    @property
    def agents(self) -> Dict[str, Any]:
        return self._config.get('agents', {})

    @property
    def agent_roles(self) -> Dict[str, Any]:
        return self._config.get('agents', {}).get('roles', {})

    @property
    def logging(self) -> Dict[str, Any]:
        return self._config.get('logging', {})


config = Config()