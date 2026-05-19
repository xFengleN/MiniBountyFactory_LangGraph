#!/usr/bin/env python3
"""
Sandbox entry script — runs inside container.
Receives task config via env vars, calls Ollama on host, outputs fix JSON to stdout.
No git, no file I/O — just LLM inference.
"""

import json
import os
import sys
import requests

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def call_ollama(model, system_prompt, user_prompt, temperature=0.4, max_tokens=4096):
    payload = {
        "model": model,
        "prompt": user_prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
    }
    if system_prompt:
        payload["system"] = system_prompt

    resp = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=600)
    resp.raise_for_status()
    return resp.json().get("response", "")


def parse_json_response(text):
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end != 0:
            return json.loads(text[start:end])
    except:
        pass
    return None


def main():
    config_path = "/sandbox/task_config.json"
    try:
        with open(config_path) as f:
            task_config = json.load(f)
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Invalid config: {e}"}))
        sys.exit(1)

    bounty = task_config["bounty"]
    agent_type = task_config.get("agent_type", "simple")
    model = task_config.get("model", "qwen2.5-coder:7b")
    subtasks = task_config.get("subtasks", [])

    title = bounty.get("title", "")
    description = bounty.get("description", "")
    issue_url = bounty.get("issue_url", "")
    repo_files = task_config.get("repo_files", "")

    all_files = []

    if agent_type == "simple":
        system_prompt = """You are a code generation assistant. Fix bugs or implement small features.
Guidelines:
1. Only modify necessary files
2. Make minimal, focused changes
3. Follow existing code style
4. Do NOT add unnecessary features

Output JSON:
{"files": [{"path": "relative/path", "content": "full content", "action": "create/modify"}], "confidence": 0.0-1.0, "reasoning": "brief explanation"}"""

        user_prompt = f"""Issue Title: {title}

Issue Description:
{description}

Issue URL: {issue_url}

Repository Files (sample):
{repo_files}

Generate the fix."""

        resp = call_ollama(model, system_prompt, user_prompt, temperature=0.4, max_tokens=4096)
        parsed = parse_json_response(resp)
        if parsed and "files" in parsed:
            all_files = parsed["files"]

    else:
        if subtasks:
            for subtask in subtasks:
                subtask_desc = subtask.get("description", "")
                subtask_prompt = f"""Subtask: {subtask_desc}
Original issue: {title}
Description: {description[:500]}

Repository Files (sample):
{repo_files}

Solve this subtask. Output JSON:
{{"files": [{{"path": "relative/path", "content": "full content", "action": "modify"}}], "confidence": 0.0-1.0}}"""

                resp = call_ollama(model, None, subtask_prompt, temperature=0.4, max_tokens=4096)
                parsed = parse_json_response(resp)
                if parsed and "files" in parsed:
                    all_files.extend(parsed["files"])
        else:
            # No subtasks provided, generate fix directly from issue
            complex_prompt = f"""Issue Title: {title}

Issue Description:
{description}

Issue URL: {issue_url}

Repository Files (sample):
{repo_files}

Generate the fix. Output JSON:
{{"files": [{{"path": "relative/path", "content": "full content", "action": "modify"}}], "confidence": 0.0-1.0}}"""

            resp = call_ollama(model, None, complex_prompt, temperature=0.4, max_tokens=4096)
            parsed = parse_json_response(resp)
            if parsed and "files" in parsed:
                all_files.extend(parsed["files"])

    if not all_files:
        print(json.dumps({"success": False, "error": "LLM returned no valid fix"}))
        sys.exit(0)

    print(json.dumps({
        "success": True,
        "agent_type": agent_type,
        "files_changed": all_files,
        "model_used": model,
    }))


if __name__ == "__main__":
    main()
