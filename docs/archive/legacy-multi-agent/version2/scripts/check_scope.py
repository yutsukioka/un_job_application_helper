"""
check_scope.py — STUB (v2 design spec; implementation deferred).

Purpose
-------
Verify that, for a given multi-agent v2 run, every file written under
`output/generated_documents/history/<position>/` was written by the
agent declared as that file's owner in `topology/server_manifest.yaml`.

Specifically:

1. Every file in a writer's draft subfolder must be writable only by
   that writer (e.g., `screening-lead/**` only by `screening-lead` when
   serving as writer on S1 or D1).
2. Every `_discussion/advisor_notes_<server>.md` file must be writable
   only by the WRITER or `qa-auditor` (canonical tester) declared on
   that server. NEVER by any agent declared as `advisor` on that server.
3. Canonical flat-path outputs (`phase1_7_strategy_report.md`,
   `option*.md`) must be writable only by `qa-auditor` on the
   appropriate consensus server (C1 or C2).
4. Prep artifacts must be writable only by their declared P0a/P0b
   writer.

Inputs
------
- `topology/server_manifest.yaml` — server roster with writer / advisors
  / canonical_tester / *_allowed_paths fields.
- A changeset to inspect:
    - default: `git diff --name-only HEAD` against the working tree
    - or a list of paths passed via `--paths file1 file2 ...`
- An attribution mapping (path -> agent) supplied via:
    - `--attribution attribution.json` (mapping produced by the
      orchestrator during the run), or
    - inferred from the `tmp/agent_sync/<server>/` log directory (each
      server's run dir identifies the writer for that server)

Output
------
- Exit 0 with "OK" if every modified path is within an authorized
  agent's allowed_paths for at least one server in the manifest where
  that agent was declared as the file's owner.
- Exit 1 with a structured failure report listing each violation:
    - path
    - actual writer (per attribution)
    - declared owner (per manifest)
    - rule violated

Usage
-----
    python multi-agent-version2/scripts/check_scope.py
    python multi-agent-version2/scripts/check_scope.py --paths a.md b.md
    python multi-agent-version2/scripts/check_scope.py --attribution attribution.json

Idempotent and safe to run as a pre-commit hook.

Status
------
STUB ONLY. The function bodies below intentionally raise NotImplementedError.
Implementation is deferred to rollout step 15 (see
spec/99_rollout_and_parked.md).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def load_manifest(manifest_path: Path) -> dict:
    """Load and validate topology/server_manifest.yaml."""
    raise NotImplementedError("check_scope.py is a stub in v2 spec")


def collect_changeset(paths: list[str] | None) -> list[Path]:
    """Return list of changed paths (from --paths or `git diff --name-only`)."""
    raise NotImplementedError("check_scope.py is a stub in v2 spec")


def load_attribution(attribution_path: Path | None) -> dict[str, str]:
    """Map path -> writer agent name (from orchestrator-emitted file or inferred)."""
    raise NotImplementedError("check_scope.py is a stub in v2 spec")


def check_violations(
    manifest: dict,
    changeset: list[Path],
    attribution: dict[str, str],
) -> list[dict]:
    """
    Return a list of violation dicts. Empty list means OK.

    Each violation dict has keys: path, actual_writer, declared_owner,
    rule_violated.

    Rules checked:
      1. file in <agent>/** folder must have actual_writer == <agent>
      2. _discussion/advisor_notes_<server>.md must have
         actual_writer in {server.writer, server.canonical_tester}
         AND actual_writer NOT in server.advisors
      3. canonical flat paths must have actual_writer == qa-auditor
         AND must be attributed to a consensus server (C1 or C2)
      4. prep artifacts must match P0a/P0b writer assignments
    """
    raise NotImplementedError("check_scope.py is a stub in v2 spec")


def main() -> int:
    parser = argparse.ArgumentParser(description="v2 scope verifier (stub).")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).parent.parent / "topology" / "server_manifest.yaml",
    )
    parser.add_argument("--paths", nargs="*", default=None)
    parser.add_argument("--attribution", type=Path, default=None)
    args = parser.parse_args()

    print(
        "check_scope.py is a stub in the multi-agent-version2 spec. "
        "Implementation is deferred to rollout step 15. "
        "See multi-agent-version2/spec/03_tier_c_filesystem_scope.md §C3.",
        file=sys.stderr,
    )
    return 2  # explicit "not implemented" exit code


if __name__ == "__main__":
    sys.exit(main())
