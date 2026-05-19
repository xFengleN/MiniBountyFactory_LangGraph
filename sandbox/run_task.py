#!/usr/bin/env python3
"""
Sandbox validation script — runs inside container.
Mounts workspace read-only, runs install + test commands, returns JSON result.
No network access, no Ollama — pure validation.
"""

import subprocess
import sys
import json


def main():
    install_cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    test_cmd = sys.argv[2] if len(sys.argv) > 2 else ""

    result = {
        "install_ok": True,
        "tests_ok": True,
        "overall": True,
        "failures": [],
        "exit_code": 0,
    }

    if install_cmd:
        r = subprocess.run(
            install_cmd, shell=True, cwd="/workspace",
            capture_output=True, text=True, timeout=120
        )
        if r.returncode != 0:
            result["install_ok"] = False
            result["overall"] = False
            result["exit_code"] = r.returncode
            result["failures"].append(f"Install failed: {r.stderr[:500]}")
            print(json.dumps(result))
            return

    if test_cmd:
        r = subprocess.run(
            test_cmd, shell=True, cwd="/workspace",
            capture_output=True, text=True, timeout=120
        )
        if r.returncode != 0:
            result["tests_ok"] = False
            result["overall"] = False
            result["exit_code"] = r.returncode
            for line in (r.stdout + r.stderr).splitlines():
                if any(kw in line.lower() for kw in ["error", "failed", "expect", "exception", "assert"]):
                    result["failures"].append(line.strip())
                    if len(result["failures"]) >= 10:
                        break

    print(json.dumps(result))


if __name__ == "__main__":
    main()
