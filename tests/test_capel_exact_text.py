from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "agents/apex/skills/capel-fit/scripts/validate_text.py"


def run(text: str, *args: str):
    return subprocess.run([sys.executable, str(SCRIPT), "--json", *args],
                          input=text.encode(), capture_output=True)


def test_exact_text_keeps_unicode_spacing_newlines_and_ownership():
    text = '  Évaluation — 日本語: is responsible for grants (support role).\r\n'
    result = run(text, "--char-limit", "4000")
    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["count"] == len(text)
    assert report["input_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert report["normalization"] == "none"


def test_utf16_boundary_and_explicit_codepoint_mode():
    text = "A" * 3999 + "🌍"
    utf = run(text, "--char-limit", "4000", "--unit", "utf16")
    assert utf.returncode == 1
    assert json.loads(utf.stdout)["count"] == 4001
    cp = run(text, "--char-limit", "4000", "--unit", "codepoints")
    assert cp.returncode == 0
    assert json.loads(cp.stdout)["count"] == 4000


def test_maximum_does_not_expand_short_text_and_band_failure_is_nonzero():
    assert run("Brief", "--char-limit", "4000").returncode == 0
    assert run("Brief", "--char-limit", "4000", "--target-low", "10").returncode == 1
    above = run("Seven77", "--char-limit", "20", "--target-high", "6")
    assert above.returncode == 1
    assert json.loads(above.stdout)["status"] == "ABOVE_TARGET_HIGH"
    assert run("Brief", "--char-limit", "4", "--target-low", "5").returncode == 2


def test_file_and_stdin_count_the_same_exact_crlf_string(tmp_path):
    raw = "É\r\n🌍\n".encode()
    path = tmp_path / "field.txt"
    path.write_bytes(raw)
    result = subprocess.run([sys.executable, str(SCRIPT), "--file", str(path),
                             "--char-limit", "20", "--unit", "utf16", "--json"], capture_output=True)
    stdin = run(raw.decode(), "--char-limit", "20", "--unit", "utf16")
    assert result.returncode == 0
    assert result.stdout == stdin.stdout
    assert path.read_bytes() == raw
