#!/usr/bin/env python3
"""PostToolUse Hook: Check agent_sync server for new messages after every tool use.

If messages are pending, injects additionalContext so the agent sees them immediately
without waiting for the next Stop Hook cycle.

Environment variables:
  AGENT_SYNC_NAME  — agent name (agent-a | agent-b)
  AGENT_SYNC_PORT  — server port (default: 9800)
"""
import json
import os
import socket
import sys


def peek_server(agent: str, port: int) -> dict | None:
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        req = json.dumps({"cmd": "peek", "agent": agent}) + "\n"
        s.sendall(req.encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf.decode().strip())
    except Exception:
        return None


def main():
    data = json.loads(sys.stdin.read())
    # Skip peek for certain high-frequency tools to reduce latency
    tool = data.get("tool_name", "")
    if tool in ("read_file", "grep_search", "file_search", "list_dir",
                "semantic_search", "view_image"):
        print(json.dumps({}))
        return

    agent = os.environ.get("AGENT_SYNC_NAME", "agent-a")
    port = int(os.environ.get("AGENT_SYNC_PORT", "9800"))

    resp = peek_server(agent, port)
    if not resp or not resp.get("ok"):
        print(json.dumps({}))
        return

    pending = resp.get("pending", 0)
    phase = resp.get("phase", "")
    round_num = resp.get("round", 0)

    if pending > 0:
        ctx = (
            f"🔔 [agent_sync] {pending} new messages (phase={phase}, round={round_num})\n"
            f"Check now: python agent_sync/client_v6.py listen {agent} --timeout 5 --port {port}"
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": ctx,
            }
        }))
    else:
        print(json.dumps({}))


if __name__ == "__main__":
    main()
