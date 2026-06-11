from pathlib import Path

from scripts import build_ccog_reference


def test_postscript_literal_escapes_path_tokens():
    literal = build_ccog_reference._postscript_literal(Path("/tmp/a)b\\c.pdf"))

    assert literal == "(/tmp/a\\)b\\\\c.pdf)"


def test_ghostscript_commands_keep_safer_enabled(monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text):
        calls.append(command)

        class Result:
            stdout = "7\n" if "-dNODISPLAY" in command else "pdf text"

        return Result()

    monkeypatch.setattr(build_ccog_reference.subprocess, "run", fake_run)

    assert build_ccog_reference.pdf_page_count(Path("/tmp/source.pdf")) == 7
    assert build_ccog_reference.extract_text(Path("/tmp/source.pdf")) == "pdf text"

    flattened = [part for command in calls for part in command]
    assert "-dNOSAFER" not in flattened
    assert flattened.count("-dSAFER") == 2
    assert any("runpdfbegin" in part for part in calls[0])
