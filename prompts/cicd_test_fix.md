You are fixing test failures. The test suite is failing and you need to fix the code.

Issue: {title}

Test Failures:
{failure_text}

Repository path: {repo_path}
Language: {language}
Test command: {test_command}
Install command: {install_command}

Analyze the failures and generate fixes. Return ONLY the file changes as a JSON array:
[
  {"path": "relative/file/path.py", "content": "full file content after fix", "action": "modify"}
]

Make only the changes needed to fix the failures.
