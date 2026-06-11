#!/usr/bin/env python3
"""Stop Hook: Block agent stop if agent_sync server has work pending.

Reads from stdin: JSON with hookEventName, stop_hook_active, etc.
Outputs JSON to stdout: decision "block" + reason, or empty {} to allow stop.

Environment variables:
  AGENT_SYNC_NAME  — agent name (agent-a | agent-b)
  AGENT_SYNC_PORT  — server port (default: 9800)
"""
import json
import os
import socket
import sys


def peek_server(agent: str, port: int) -> dict | None:
    """Non-blocking check: pending messages + phase."""
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
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


def load_agent_body(agent: str) -> str:
    """Load .agent.md body instructions (after frontmatter) for re-injection."""
    for path in [
        os.path.join(".github", "agents", f"{agent}.agent.md"),
        os.path.join(".github", "agents", f"{agent}.md"),
    ]:
        try:
            text = open(path, encoding="utf-8").read()
            parts = text.split("---", 2)
            body = parts[2].strip() if len(parts) >= 3 else text.strip()
            return body[:600]  # limit to avoid bloating reason
        except (FileNotFoundError, IndexError):
            continue
    return ""


def main():
    data = json.loads(sys.stdin.read())
    stop_hook_active = data.get("stop_hook_active", False)

    agent = os.environ.get("AGENT_SYNC_NAME", "agent-a")
    port = int(os.environ.get("AGENT_SYNC_PORT", "9800"))

    resp = peek_server(agent, port)

    if resp and resp.get("ok"):
        phase = resp.get("phase", "SHUTDOWN")
        pending = resp.get("pending", 0)
        round_num = resp.get("round", 0)

        if phase == "SHUTDOWN":
            # Server says shutdown → allow stop
            print(json.dumps({}))
            return

        if pending > 0:
            # Messages waiting → always block (even on 2nd attempt)
            body = load_agent_body(agent)
            reason = (
                f"[agent_sync] phase={phase} round={round_num} pending={pending}\n"
                f"There are {pending} messages in your mailbox. Fetch them now:\n"
                f"  python agent_sync/client_v6.py listen {agent} --timeout 30 --port {port}\n"
            )
            if body:
                reason += f"\n--- core instructions ---\n{body}\n"
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "Stop",
                    "decision": "block",
                    "reason": reason,
                }
            }))
            return

        if stop_hook_active:
            # 2nd attempt + no pending + active phase → allow stop (save premium)
            print(json.dumps({}))
            return

        # 1st attempt + no pending + active phase → listen once
        body = load_agent_body(agent)
        reason = (
            f"[agent_sync] phase={phase} round={round_num}\n"
            f"Still on duty. Use listen and wait for the next instruction:\n"
            f"  python agent_sync/client_v6.py listen {agent} --timeout 30 --port {port}\n"
        )
        if body:
            reason += f"\n--- core instructions ---\n{body}\n"
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "decision": "block",
                "reason": reason,
            }
        }))
        return

    # Server unreachable → allow stop
    print(json.dumps({}))


if __name__ == "__main__":
    main()
