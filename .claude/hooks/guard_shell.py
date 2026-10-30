"""PreToolUse hook for shell commands: blocks git commit/push, asks before adding packages."""

import json
import re
import sys

GIT_WRITE = re.compile(r"\bgit\b[^|;&\n]*\s(commit|push)\b")
ADD_PACKAGE = re.compile(r"\b(uv(\.exe)?\s+(add|pip\s+install)|pip3?\s+install)\b")


def decide(command: str) -> dict | None:
    """Return a hook decision for the command, or None to allow it."""
    if GIT_WRITE.search(command):
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "CLAUDE.md: Claude never runs git commit or git push. "
                "Suggest a commit message to the user instead; they commit."
            ),
        }
    if ADD_PACKAGE.search(command):
        return {
            "permissionDecision": "ask",
            "permissionDecisionReason": (
                "CLAUDE.md: adding a package needs your approval. "
                "Claude should have explained why it's needed and the alternatives."
            ),
        }
    return None


def main() -> None:
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")
    decision = decide(command)
    if decision:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **decision}}))


if __name__ == "__main__":
    main()
