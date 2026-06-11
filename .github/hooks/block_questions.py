#!/usr/bin/env python3
"""PreToolUse Hook: Block clarifying questions to prevent agent from pausing.

Blocks vscode_askQuestions tool calls, forcing the agent to make assumptions
and continue working instead of asking the user.
"""
import json
import sys


BLOCKED_TOOLS = {
    "vscode_askQuestions",
}


def main():
    data = json.loads(sys.stdin.read())
    tool = data.get("tool_name", "")

    if tool in BLOCKED_TOOLS:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "decision": "block",
                "reason": (
                    "Do not ask the user questions. Make the most reasonable assumption and continue working."
                    " Leave unclear points as TODO comments."
                ),
            }
        }))
    else:
        print(json.dumps({}))


if __name__ == "__main__":
    main()
