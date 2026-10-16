"""Release binding regressions using fresh interpreters and synthetic work only."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from test_runner_reliability import HERE, RUNNER, phase_config

pytest_plugins = ["test_runner_reliability"]

PACKAGE = HERE.parents[3] / "packages/jobagg"
BOOTSTRAP = """
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys

runner_path, stale, selected = map(Path, sys.argv[1:4])
assert 'PYTHONPATH' not in os.environ
# Model a PEP 660 editable install: it supplies jobagg only after PathFinder.
sys.path[:] = [p for p in sys.path if not (Path(p) / 'jobagg').is_dir()]
class StaleEditableFinder:
    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        if fullname == 'jobagg':
            return importlib.util.spec_from_file_location(
                fullname, stale / 'jobagg/__init__.py')
sys.meta_path.insert(sys.meta_path.index(importlib.machinery.PathFinder) + 1,
                     StaleEditableFinder)
assert Path(importlib.util.find_spec('jobagg').origin) == stale / 'jobagg/__init__.py'
spec = importlib.util.spec_from_file_location('fixture_dispatcher', runner_path)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
"""


def package(root, release):
    target = root / "jobagg"
    target.mkdir(parents=True)
    (target / "__init__.py").write_text("")
    (target / "selected_dependency.py").write_text(f"RELEASE = {release!r}\n")
    for name in ("publication_snapshot", "storage_retention", "remediation_concurrency", "storage_cycle"):
        (target / f"{name}.py").write_text("from jobagg.selected_dependency import RELEASE\n")
    return root


def probe(root, selected, source, *args):
    stale = package(root / "stale-editable", "stale")
    script = root / "probe.py"
    script.write_text(BOOTSTRAP + source)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-B", str(script), str(RUNNER), str(stale), str(selected), *map(str, args)],
        cwd=root, env=environment, text=True, capture_output=True, timeout=20,
    )


@pytest.mark.parametrize("helper,key", [
    ("snapshot_helper", "publication_cwd"),
    ("retention_helper", "worker_cwd"),
    ("concurrency_helper", "worker_cwd"),
    ("package_helper", "worker_cwd"),
])
def test_helpers_pin_their_configured_release_even_if_already_later_on_path(tmp_path, helper, key):
    selected = package(tmp_path / "selected-release", "selected")
    other = package(tmp_path / "other-release", "other")
    result = probe(tmp_path, selected, f"""
sys.path[:0] = [str(stale), str(selected)]
config = {{'worker_cwd': Path(sys.argv[4]), 'publication_cwd': Path(sys.argv[4])}}
config[{key!r}] = selected
helper = getattr(runner, {helper!r})
module = helper(config, 'storage_cycle') if {helper!r} == 'package_helper' else helper(config)
assert module.RELEASE == 'selected'
assert sys.path[0] == str(selected)
assert all(Path(module.__file__).is_relative_to(selected / 'jobagg')
           for name, module in sys.modules.items() if name == 'jobagg' or name.startswith('jobagg.'))
assert not list(selected.rglob('__pycache__'))
""", other)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("conflict", [
    "root", "submodule", "package_path", "missing_origin", "regular_missing_origins",
])
def test_preloaded_foreign_or_incomplete_modules_fail_without_eviction(tmp_path, conflict):
    selected = package(tmp_path / "selected-release", "selected")
    result = probe(tmp_path, selected, f"""
if {conflict!r} != 'root':
    sys.path.insert(0, str(selected))
import jobagg
if {conflict!r} == 'submodule':
    spec = importlib.util.spec_from_file_location('jobagg.selected_dependency',
                                                stale / 'jobagg/selected_dependency.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
elif {conflict!r} == 'package_path':
    jobagg.__path__.append(str(stale / 'jobagg'))
elif {conflict!r} == 'missing_origin':
    jobagg.__spec__.origin = None
elif {conflict!r} == 'regular_missing_origins':
    import jobagg.selected_dependency
    jobagg.selected_dependency.__file__ = None
    jobagg.selected_dependency.__spec__.origin = None
before = {{name: module for name, module in sys.modules.items()
          if name == 'jobagg' or name.startswith('jobagg.')}}
try:
    runner.snapshot_helper({{'publication_cwd': selected}})
except ValueError as exc:
    assert 'differs from selected implementation' in str(exc), str(exc)
else:
    raise AssertionError('conflicting preloaded JobAgg was accepted')
assert all(sys.modules[name] is module for name, module in before.items())
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("conflict", [
    "foreign_path", "extra_path", "empty_path", "foreign_spec_path", "empty_spec_path",
    "missing_spec", "spec_origin", "file_origin", "wrong_loader", "wrong_name", "has_location",
    "initializer", "dangling_initializer", "escaped_directory", "missing_directory",
])
def test_invalid_namespace_packages_fail_without_eviction(tmp_path, conflict):
    selected = package(tmp_path / "selected-release", "selected")
    namespace = selected / "jobagg/pipelines"
    if conflict == "escaped_directory":
        outside = tmp_path / "foreign-pipelines"
        outside.mkdir()
        namespace.symlink_to(outside, target_is_directory=True)
    else:
        namespace.mkdir()
    result = probe(tmp_path, selected, f"""
sys.path.insert(0, str(selected))
import jobagg.pipelines
module = jobagg.pipelines
foreign = str(stale / 'jobagg/pipelines')
conflict = {conflict!r}
if conflict == 'foreign_path':
    module.__path__ = [foreign]
elif conflict == 'extra_path':
    module.__path__.append(foreign)
elif conflict == 'empty_path':
    module.__path__ = []
elif conflict == 'foreign_spec_path':
    module.__spec__.submodule_search_locations = [foreign]
elif conflict == 'empty_spec_path':
    module.__spec__.submodule_search_locations = []
elif conflict == 'missing_spec':
    module.__spec__ = None
elif conflict == 'spec_origin':
    module.__spec__.origin = str(selected / 'jobagg/pipelines/__init__.py')
elif conflict == 'file_origin':
    module.__file__ = str(selected / 'jobagg/pipelines/__init__.py')
elif conflict == 'wrong_loader':
    module.__spec__.loader = importlib.machinery.SourceFileLoader(
        module.__name__, str(selected / 'jobagg/pipelines/__init__.py'))
elif conflict == 'wrong_name':
    module.__spec__.name = 'jobagg.other'
elif conflict == 'has_location':
    module.__spec__.has_location = True
elif conflict == 'initializer':
    (selected / 'jobagg/pipelines/__init__.py').write_text('')
elif conflict == 'dangling_initializer':
    (selected / 'jobagg/pipelines/__init__.py').symlink_to(selected / 'missing.py')
elif conflict == 'missing_directory':
    (selected / 'jobagg/pipelines').rmdir()
before = {{name: module for name, module in sys.modules.items()
          if name == 'jobagg' or name.startswith('jobagg.')}}
try:
    runner.snapshot_helper({{'publication_cwd': selected}})
except ValueError as exc:
    assert 'differs from selected implementation' in str(exc), str(exc)
else:
    raise AssertionError('invalid preloaded namespace package was accepted')
assert all(sys.modules[name] is module for name, module in before.items())
""")
    assert result.returncode == 0, result.stderr


def test_mixing_distinct_worker_and_publication_packages_is_rejected(tmp_path):
    selected = package(tmp_path / "publication-release", "publication")
    worker = package(tmp_path / "worker-release", "worker")
    result = probe(tmp_path, selected, """
config = {'publication_cwd': selected, 'worker_cwd': Path(sys.argv[4])}
assert runner.snapshot_helper(config).RELEASE == 'publication'
try:
    runner.retention_helper(config)
except ValueError as exc:
    assert 'differs from selected implementation' in str(exc), str(exc)
else:
    raise AssertionError('mixed parent helper releases were accepted')
assert runner.snapshot_helper(config).RELEASE == 'publication'
""", worker)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("helper,filename", [
    ("concurrency_helper", "remediation_concurrency.py"),
    ("retention_helper", "storage_retention.py"),
])
def test_standalone_stdlib_helpers_preserve_separate_publication_package(tmp_path, helper, filename):
    selected = package(tmp_path / "publication-release", "publication")
    worker = tmp_path / "standalone-worker"
    (worker / "jobagg").mkdir(parents=True)
    (worker / "jobagg" / filename).write_bytes((PACKAGE / "jobagg" / filename).read_bytes())
    result = probe(tmp_path, selected, f"""
config = {{'publication_cwd': selected, 'worker_cwd': Path(sys.argv[4])}}
assert runner.snapshot_helper(config).RELEASE == 'publication'
before = sys.modules['jobagg']
module = getattr(runner, {helper!r})(config)
assert Path(module.__file__) == config['worker_cwd'] / 'jobagg' / {filename!r}
assert sys.modules['jobagg'] is before
assert runner.snapshot_helper(config).RELEASE == 'publication'
""", worker)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("helper", ["concurrency_helper", "retention_helper"])
@pytest.mark.parametrize("invalid", ["directory", "dangling_link", "foreign_link"])
def test_invalid_initializer_never_enables_standalone_fallback(tmp_path, helper, invalid):
    selected = package(tmp_path / "selected-release", "selected")
    initializer = selected / "jobagg/__init__.py"
    initializer.unlink()
    if invalid == "directory":
        initializer.mkdir()
    else:
        outside = tmp_path / "foreign.py"
        if invalid == "foreign_link":
            outside.write_text("raise AssertionError('foreign initializer executed')\n")
        initializer.symlink_to(outside)
    result = probe(tmp_path, selected, f"""
try:
    getattr(runner, {helper!r})({{'worker_cwd': selected}})
except ValueError as exc:
    assert 'missing or outside selected package' in str(exc), str(exc)
else:
    raise AssertionError('invalid package treated as standalone')
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("invalid", ["missing_package", "escaped_helper"])
def test_missing_or_escaped_selected_code_never_falls_back_to_editable(tmp_path, invalid):
    selected = package(tmp_path / "selected-release", "selected")
    if invalid == "missing_package":
        (selected / "jobagg/__init__.py").unlink()
    else:
        target = selected / "jobagg/publication_snapshot.py"
        target.unlink()
        outside = tmp_path / "foreign.py"
        outside.write_text("raise AssertionError('foreign helper executed')\n")
        target.symlink_to(outside)
    result = probe(tmp_path, selected, """
try:
    runner.snapshot_helper({'publication_cwd': selected})
except ValueError as exc:
    assert 'missing or outside selected package' in str(exc), str(exc)
else:
    raise AssertionError('invalid selected release was accepted')
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("preload_namespace", [False, True])
def test_budget_disabled_dispatch_publishes_with_stale_editable_and_no_pythonpath(fixture, preload_namespace):
    # This is the production failure boundary: the parent snapshot module imports
    # storage_budget even though optional budget enforcement has not been enabled.
    phase_config(fixture, publication="published", worker_cwd=str(PACKAGE),
                 sealed_publication_snapshots=True, publication_timeout_seconds=5,
                 storage_budget={"enabled": False})
    config = json.loads(fixture.config_path.read_text())
    child_probe = fixture.root / "child_probe.py"
    child_probe.write_text("""
import os
from pathlib import Path
import runpy
import sys
import jobagg
import jobagg.storage_budget
expected = Path(os.environ['PYTHONPATH']).resolve() / 'jobagg'
assert Path(jobagg.__file__).resolve().parent == expected
assert Path(jobagg.storage_budget.__file__).resolve().parent == expected
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
""")
    for key in ("worker_argv", "publication_argv"):
        config[key].insert(1, str(child_probe))
    fixture.config_path.write_text(json.dumps(config))
    result = probe(fixture.root, PACKAGE, f"""
# The CI service tests import jobagg.pipelines before the in-process dispatcher.
# It is a real implicit namespace package, without a file or spec origin.
if {preload_namespace!r}:
    sys.path.insert(0, str(selected))
    import jobagg.pipelines
    assert jobagg.pipelines.__file__ is None
    assert jobagg.pipelines.__spec__.origin is None
code = runner.main(['--config', sys.argv[4], '--execute'])
if code == 0:
    import jobagg.storage_budget
    assert Path(jobagg.storage_budget.__file__).resolve().parent == selected / 'jobagg'
sys.exit(code)
""", fixture.config_path)
    assert result.returncode == 0, result.stdout + result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["worker_exit_code"] == outcome["publication_exit_code"] == 0
    assert outcome["publication_status"] == "published"
    request = json.loads((Path(outcome["run_dir"]) / "publication_request.json").read_text())
    assert Path(request["publication_snapshot"]["path"]).is_file()
