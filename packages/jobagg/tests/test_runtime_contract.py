from pathlib import Path
from types import SimpleNamespace

import pytest

from jobagg import runtime_contract


def test_same_config_dependency_and_dispatcher_byte_changes_advance_contract(tmp_path, monkeypatch):
    dependency = tmp_path / "provider.py"
    dependency.write_text("parser = 1\n")
    module = tmp_path / "dispatcher.py"
    module.write_text("runner = 1\n")
    fake = SimpleNamespace(
        files=[Path("provider.py")], version="1", requires=[], locate_file=lambda _: dependency
    )
    monkeypatch.setattr(runtime_contract.metadata, "distribution", lambda _: fake)
    args = dict(
        package_root=Path(runtime_contract.__file__).parent.parent,
        dispatcher_files=[module],
        config_sha256="a" * 64,
        registry_sha256="b" * 64,
        manifest_sha256="c" * 64,
    )
    first = runtime_contract.build(**args)
    dependency.write_text("parser = 2\n")  # same size, version and configuration
    second = runtime_contract.build(**args)
    assert first["sha256"] != second["sha256"]
    module.write_text("runner = 2\n")
    third = runtime_contract.build(**args)
    assert third["sha256"] != second["sha256"]
    dependency.unlink()
    with pytest.raises(ValueError, match="dependency_file_missing"):
        runtime_contract.build(**args)
