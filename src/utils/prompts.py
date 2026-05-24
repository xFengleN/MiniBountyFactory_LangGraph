import os
from pathlib import Path
from typing import Dict, Optional


_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / 'prompts'
_CACHE: Dict[str, str] = {}


def load_prompt(name: str) -> Optional[str]:
    if name in _CACHE:
        return _CACHE[name]
    path = _PROMPT_DIR / f'{name}.md'
    if not path.exists():
        return None
    content = path.read_text(encoding='utf-8')
    _CACHE[name] = content
    return content


def reload_prompts():
    _CACHE.clear()
