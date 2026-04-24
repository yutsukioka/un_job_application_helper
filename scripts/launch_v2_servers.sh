#!/usr/bin/env bash
# Launch / stop / status helper for v2 multi-agent coordination servers.
#
# This script starts the BACKGROUND coordination servers (server_v6.py
# instances on the ports defined by .agents/topology/server_manifest.yaml).
# It does NOT spawn the agents themselves — agents (writer / advisor /
# canonical-tester) are chat-driven and join each running server via
# client_v6.py from their own session, using the prompts in
# .agents/prompts/v2/.
#
# Usage:
#   scripts/launch_v2_servers.sh start   <stage>
#   scripts/launch_v2_servers.sh stop    <stage>
#   scripts/launch_v2_servers.sh status  <stage>
#
# Stages:
#   prep1     -> P0a (port 9800)
#   prep2     -> P0b (port 9801)
#   strategy  -> S1, S2, S3 (ports 9811, 9812, 9813)   [concurrent]
#   consensus1 -> C1 (port 9820)
#   document  -> D1, D2, D3 (ports 9831, 9832, 9833)   [concurrent]
#   consensus2 -> C2 (port 9840)
#   all       -> every server (NOT recommended; for status only)
#
# Layout:
#   tmp/agent_sync/<server-id>/   server_v6.py run dir / cwd
#   tmp/agent_sync/<server-id>.pid
#   tmp/agent_sync/<server-id>.log

set -euo pipefail

# Find REPO_ROOT: assuming this script is in .agents/scripts/
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SERVER_PY="${REPO_ROOT}/.agents/agent_sync/server_v6.py"
RUN_BASE="${REPO_ROOT}/tmp/agent_sync"
PYTHON_BIN=""

# id -> port lookup (must match .agents/topology/server_manifest.yaml).
# Implemented as a function for compatibility with macOS system bash 3.2,
# which does not support `declare -A`.
port_for() {
  case "$1" in
    P0a) echo 9800 ;;
    P0b) echo 9801 ;;
    S1)  echo 9811 ;;
    S2)  echo 9812 ;;
    S3)  echo 9813 ;;
    C1)  echo 9820 ;;
    D1)  echo 9831 ;;
    D2)  echo 9832 ;;
    D3)  echo 9833 ;;
    C2)  echo 9840 ;;
    *)   echo "" ;;
  esac
}

# Per-server agent registry. server_v6.py reads this from the AGENTS_LIST
# env var at process start (default: agent-a,agent-b). It MUST contain
# every agent that will join the server: writer + advisors + canonical
# tester (qa-auditor). Order does not matter.
#
# Source: .agents/topology/server_manifest.yaml (writer + advisors +
# canonical_tester). Consensus servers C1, C2 have qa-auditor as writer
# and three advisors; no separate canonical tester.
agents_for() {
  case "$1" in
    P0a) echo "screening-lead,technical-lead,ats-format-lead,qa-auditor" ;;
    P0b) echo "technical-lead,screening-lead,ats-format-lead,qa-auditor" ;;
    S1)  echo "screening-lead,technical-lead,ats-format-lead,qa-auditor" ;;
    S2)  echo "technical-lead,screening-lead,ats-format-lead,qa-auditor" ;;
    S3)  echo "ats-format-lead,screening-lead,technical-lead,qa-auditor" ;;
    C1)  echo "qa-auditor,screening-lead,technical-lead,ats-format-lead" ;;
    D1)  echo "screening-lead,technical-lead,ats-format-lead,qa-auditor" ;;
    D2)  echo "technical-lead,screening-lead,ats-format-lead,qa-auditor" ;;
    D3)  echo "ats-format-lead,screening-lead,technical-lead,qa-auditor" ;;
    C2)  echo "qa-auditor,screening-lead,technical-lead,ats-format-lead" ;;
    *)   echo "" ;;
  esac
}

stage_servers() {
  case "$1" in
    prep1)      echo "P0a" ;;
    prep2)      echo "P0b" ;;
    strategy)   echo "S1 S2 S3" ;;
    consensus1) echo "C1" ;;
    document)   echo "D1 D2 D3" ;;
    consensus2) echo "C2" ;;
    all)        echo "P0a P0b S1 S2 S3 C1 D1 D2 D3 C2" ;;
    *)          echo "unknown stage: $1" >&2; exit 2 ;;
  esac
}

ensure_runtime() {
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "python3 or python not on PATH" >&2
    exit 3
  fi
  [[ -f "${SERVER_PY}" ]] || { echo "missing ${SERVER_PY}" >&2; exit 3; }
}

start_one() {
  local id="$1"
  local port
  port="$(port_for "$id")"
  local agents
  agents="$(agents_for "$id")"
  local rundir="${RUN_BASE}/${id}"
  local pidfile="${RUN_BASE}/${id}.pid"
  local logfile="${RUN_BASE}/${id}.log"

  if [[ -z "${agents}" ]]; then
    echo "[${id}] no AGENTS_LIST mapping; aborting" >&2
    return 1
  fi

  mkdir -p "${rundir}"

  if [[ -f "${pidfile}" ]] && kill -0 "$(cat "${pidfile}")" 2>/dev/null; then
    echo "[${id}] already running (pid $(cat "${pidfile}"), port ${port})"
    return 0
  fi

  if lsof -i ":${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[${id}] port ${port} already in use by another process; skipping" >&2
    return 1
  fi

  ( cd "${rundir}" && AGENTS_LIST="${agents}" nohup "${PYTHON_BIN}" "${SERVER_PY}" --port "${port}" \
      >>"${logfile}" 2>&1 & echo $! >"${pidfile}" )
  sleep 0.3
  if kill -0 "$(cat "${pidfile}")" 2>/dev/null; then
    echo "[${id}] started (pid $(cat "${pidfile}"), port ${port}, agents=${agents}, log ${logfile})"
  else
    echo "[${id}] FAILED to start; see ${logfile}" >&2
    return 1
  fi
}

stop_one() {
  local id="$1"
  local pidfile="${RUN_BASE}/${id}.pid"
  if [[ ! -f "${pidfile}" ]]; then
    echo "[${id}] no pid file"
    return 0
  fi
  local pid
  pid="$(cat "${pidfile}")"
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" || true
    sleep 0.2
    if kill -0 "${pid}" 2>/dev/null; then
      kill -9 "${pid}" || true
    fi
    echo "[${id}] stopped (pid ${pid})"
  else
    echo "[${id}] not running (stale pid ${pid})"
  fi
  rm -f "${pidfile}"
}

status_one() {
  local id="$1"
  local port
  port="$(port_for "$id")"
  local pidfile="${RUN_BASE}/${id}.pid"
  if [[ -f "${pidfile}" ]] && kill -0 "$(cat "${pidfile}")" 2>/dev/null; then
    echo "[${id}] RUNNING pid=$(cat "${pidfile}") port=${port}"
  else
    echo "[${id}] stopped (port ${port})"
  fi
}

main() {
  ensure_runtime
  local action="${1:-}"
  local stage="${2:-}"
  if [[ -z "${action}" || -z "${stage}" ]]; then
    grep '^# ' "$0" | sed 's/^# \{0,1\}//'
    exit 1
  fi
  if [[ "${stage}" == "all" && "${action}" != "status" ]]; then
    echo "stage 'all' is only supported for status; start/stop stages in runbook order" >&2
    exit 2
  fi
  mkdir -p "${RUN_BASE}"
  local id
  for id in $(stage_servers "${stage}"); do
    case "${action}" in
      start)  start_one  "$id" ;;
      stop)   stop_one   "$id" ;;
      status) status_one "$id" ;;
      *) echo "unknown action: ${action}" >&2; exit 2 ;;
    esac
  done
}

main "$@"
