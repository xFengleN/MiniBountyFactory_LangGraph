import json
import time
from typing import Optional, Dict, Any, List
import requests

from .logger import get_logger

logger = get_logger(__name__)


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2:3b"):
        self.base_url = base_url.rstrip('/')
        self.model = model

    def set_model(self, model: str):
        self.model = model

    def generate(
        self,
        prompt: str,
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False
    ) -> str:
        url = f"{self.base_url}/api/generate"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }

        if system:
            payload["system"] = system

        try:
            response = requests.post(url, json=payload, timeout=300)
            response.raise_for_status()

            if stream:
                full_response = ""
                for line in response.iter_lines():
                    if line:
                        data = json.loads(line)
                        if 'response' in data:
                            full_response += data['response']
                        if data.get('done', False):
                            break
                return full_response
            else:
                result = response.json()
                return result.get('response', '')

        except requests.exceptions.Timeout:
            logger.error(f"Ollama request timed out for model {self.model}")
            raise TimeoutError(f"Ollama request timed out")
        except requests.exceptions.ConnectionError:
            logger.error(f"Cannot connect to Ollama at {self.base_url}")
            raise ConnectionError(f"Cannot connect to Ollama at {self.base_url}")
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            raise

    def generate_with_stats(
        self,
        prompt: str,
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False
    ) -> tuple:
        url = f"{self.base_url}/api/generate"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }

        if system:
            payload["system"] = system

        try:
            response = requests.post(url, json=payload, timeout=300)
            response.raise_for_status()

            if stream:
                full_response = ""
                stats = {}
                for line in response.iter_lines():
                    if line:
                        data = json.loads(line)
                        if 'response' in data:
                            full_response += data['response']
                        if data.get('done', False):
                            stats = data
                            break
                return full_response, stats
            else:
                result = response.json()
                return result.get('response', ''), result

        except requests.exceptions.Timeout:
            logger.error(f"Ollama request timed out for model {self.model}")
            raise TimeoutError(f"Ollama request timed out")
        except requests.exceptions.ConnectionError:
            logger.error(f"Cannot connect to Ollama at {self.base_url}")
            raise ConnectionError(f"Cannot connect to Ollama at {self.base_url}")
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            raise

    def chat_with_stats(self, messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: int = 2048) -> tuple:
        url = f"{self.base_url}/api/chat"

        payload = {
            "model": self.model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }

        try:
            response = requests.post(url, json=payload, timeout=300)
            response.raise_for_status()
            result = response.json()
            return result.get('message', {}).get('content', ''), result
        except Exception as e:
            logger.error(f"Ollama chat failed: {e}")
            raise

    def is_available(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except:
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get('models', [])
        except Exception as e:
            logger.error(f"Failed to list Ollama models: {e}")
            return []

    def pull_model(self, model: str) -> bool:
        url = f"{self.base_url}/api/pull"
        payload = {"name": model, "stream": False}

        try:
            response = requests.post(url, json=payload, timeout=600)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to pull model {model}: {e}")
            return False


def extract_token_stats(metadata: dict) -> dict:
    prompt = metadata.get('prompt_eval_count', 0) or 0
    completion = metadata.get('eval_count', 0) or 0
    return {
        'prompt_tokens': prompt,
        'completion_tokens': completion,
        'total_tokens': prompt + completion,
        'total_duration_ns': metadata.get('total_duration', 0) or 0,
        'eval_duration_ns': metadata.get('eval_duration', 0) or 0,
        'prompt_eval_duration_ns': metadata.get('prompt_eval_duration', 0) or 0,
    }