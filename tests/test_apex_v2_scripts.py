from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "agents" / "apex" / "scripts"


def run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_validate_advisor_notes_accepts_substantive_reviews(tmp_path: Path) -> None:
    notes = tmp_path / "advisor_notes.md"
    notes.write_text(
        "\n".join(
            [
                "### technical-lead",
                "Please verify metric ledger grounding for option1_admin_profile.md and clarify",
                "the CCOG evidence before final merge because the JD coverage risk is material.",
                "### ats-format-lead",
                "Tighten keyword coverage, check CAPEL character limits, and preserve source",
                "claims in option2_cv.md so the draft avoids unsupported ATS terminology.",
            ]
        ),
        encoding="utf-8",
    )
    result = run_script(
        "validate_advisor_notes.py",
        "--advisor-notes",
        str(notes),
        "--advisors",
        "technical-lead,ats-format-lead",
        "--json",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True


def test_check_draft_diversity_detects_copied_drafts(tmp_path: Path) -> None:
    for role in ("screening-lead", "technical-lead", "ats-format-lead"):
        role_dir = tmp_path / role
        role_dir.mkdir()
        (role_dir / "option1_admin_profile.md").write_text("same copied draft\n", encoding="utf-8")
    result = run_script(
        "check_draft_diversity.py",
        "--outdir",
        str(tmp_path),
        "--threshold",
        "0.95",
        "--json",
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["ok"] is False


def test_prepare_d_writer_scaffold_creates_empty_targets(tmp_path: Path) -> None:
    result = run_script(
        "prepare_d_writer_scaffold.py",
        "--outdir",
        str(tmp_path),
        "--role",
        "screening-lead",
        "--option",
        "option1_admin_profile.md",
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "screening-lead" / "option1_admin_profile.md").exists()
    assert (tmp_path / "_discussion" / "d_writer_scaffold_screening-lead.json").exists()


def test_prepare_independent_eval_input_excludes_candidate_history(tmp_path: Path) -> None:
    context = tmp_path / "application_context.md"
    context.write_text(
        """# Context

## USER_JOB_HISTORY_TEXT
Sensitive candidate history.

## JOB_DESCRIPTION_TEXT
Target role description.

## JOB_QUALIFICATION_QUESTIONS
Question text.

## LIMITS
TARGET_SYSTEM: INSPIRA
""",
        encoding="utf-8",
    )
    output = tmp_path / "independent_eval_input.md"
    result = run_script(
        "prepare_independent_eval_input.py",
        "--context-pack",
        str(context),
        "--output-file",
        str(output),
    )
    assert result.returncode == 0, result.stderr
    text = output.read_text(encoding="utf-8")
    assert "Target role description." in text
    assert "Question text." in text
    assert "Sensitive candidate history." not in text


def test_write_run_manifest_lifecycle(tmp_path: Path) -> None:
    init = run_script(
        "write_run_manifest.py",
        "init",
        "--outdir",
        str(tmp_path),
        "--job-slug",
        "sample",
        "--target-system",
        "INSPIRA",
        "--servers",
        "P0a,C1",
    )
    assert init.returncode == 0, init.stderr
    add = run_script(
        "write_run_manifest.py",
        "add-skill",
        "--outdir",
        str(tmp_path),
        "--skill",
        "apex-ccog-resolver",
        "--server",
        "P0a",
        "--artifact",
        "ccog_reference_resolved.md",
    )
    assert add.returncode == 0, add.stderr
    done = run_script("write_run_manifest.py", "finalize", "--outdir", str(tmp_path))
    assert done.returncode == 0, done.stderr
    manifest = json.loads((tmp_path / "_discussion" / "run_manifest.json").read_text())
    assert manifest["job_slug"] == "sample"
    assert manifest["run_end_utc"]
    assert manifest["skills_invoked"][0]["skill"] == "apex-ccog-resolver"
