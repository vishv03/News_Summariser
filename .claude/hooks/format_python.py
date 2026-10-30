"""PostToolUse hook: runs ruff format and ruff check --fix on any Python file Claude edits."""

import json
import os
import shutil
import subprocess
import sys


def find_uv() -> str | None:
    """Find uv even when the app was started before uv was added to PATH."""
    found = shutil.which("uv")
    if found or sys.platform != "win32":
        return found
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        user_path, _ = winreg.QueryValueEx(key, "Path")
    return shutil.which("uv", path=os.path.expandvars(user_path))


def main() -> None:
    payload = json.load(sys.stdin)
    file_path = payload.get("tool_input", {}).get("file_path", "")
    if not file_path.endswith(".py"):
        return
    uv = find_uv()
    if uv is None:
        print("format hook: uv not found, skipped ruff", file=sys.stderr)
        return
    subprocess.run([uv, "run", "ruff", "format", "--quiet", file_path], check=False)
    result = subprocess.run(
        [uv, "run", "ruff", "check", "--fix", "--quiet", file_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Exit code 2 shows the remaining lint errors to Claude so it fixes them.
        print(result.stdout or result.stderr, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
