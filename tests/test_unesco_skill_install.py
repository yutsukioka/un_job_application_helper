import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "agents/apex/scripts/install_unesco_skills.py"
spec = importlib.util.spec_from_file_location("unesco_install", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def sources(tmp_path):
    root = tmp_path / "source"
    for name in installer.NAMES:
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("canonical source")
    return root


def test_install_is_idempotent_and_points_to_canonical_sources(tmp_path):
    source, target = sources(tmp_path), tmp_path / "discovery"
    assert all(r["state"] == "missing" for r in installer.inspect(target, source))
    assert all(r["state"] == "installed" for r in installer.install(target, source))
    assert all(r["state"] == "installed" for r in installer.install(target, source))
    (source / installer.NAMES[0] / "SKILL.md").write_text("updated canonical")
    assert (target / installer.NAMES[0] / "SKILL.md").read_text() == "updated canonical"


def test_conflict_never_overwrites_existing_skill_or_partially_links(tmp_path):
    source, target = sources(tmp_path), tmp_path / "discovery"
    conflict = target / installer.NAMES[-1]
    conflict.mkdir(parents=True)
    (conflict / "SKILL.md").write_text("existing custom skill")
    with pytest.raises(ValueError):
        installer.install(target, source)
    assert (conflict / "SKILL.md").read_text() == "existing custom skill"
    assert sorted(p.name for p in target.iterdir()) == [installer.NAMES[-1]]


def test_missing_source_blocks_before_creating_discovery_directory(tmp_path):
    target = tmp_path / "discovery"
    with pytest.raises(ValueError):
        installer.install(target, tmp_path / "missing")
    assert not target.exists()
