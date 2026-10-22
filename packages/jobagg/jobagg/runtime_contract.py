"""Actual byte identity for publication semantics; never startup approval.

Computed once per invocation, without a persistent timestamp cache. Preparation
must explicitly seal/revalidate this value in the dirty authority. Dependency
files are read from installed distribution catalogues; missing files are repair.
"""

import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sqlite3
import sys
import tomllib

from jobagg.publication_snapshot import stable_file
from jobagg.verification_budget import check


def build(*, package_root, dispatcher_files, config_sha256, registry_sha256, manifest_sha256):
    from jobagg.remediation_worker import implementation_hash

    root = Path(package_root).resolve()
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    declared = list(project["dependencies"])
    for kind in ("browser", "compression"):
        declared.extend(project.get("optional-dependencies", {}).get(kind, []))
    pending = {re.split(r"[<>=!~;\[ ]", entry, 1)[0] for entry in declared}
    distributions = {}
    seen = set()
    while pending:
        name = pending.pop()
        normalized = name.lower().replace("_", "-")
        if normalized in seen:
            continue
        seen.add(normalized)
        check()
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            distributions[normalized] = {"installed": False}
            continue
        if dist.files is None:
            raise ValueError("runtime_dependency_catalogue_missing:" + name)
        files = {}
        for member in dist.files:
            check()
            if member.suffix == ".pyc" or "__pycache__" in member.parts:
                continue
            path = Path(dist.locate_file(member)).resolve()
            if not path.is_file():
                raise ValueError("runtime_dependency_file_missing:" + name)
            files[str(member)] = stable_file(path)["sha256"]
        distributions[normalized] = {
            "version": dist.version,
            "files_sha256": _digest(files),
            "files": len(files),
        }
        # Include installed transitive dependencies conservatively, including
        # optional ones if present. Their absence is bound too.
        for dependency in dist.requires or ():
            marker = dependency.partition(";")[2]
            extras = set(re.findall(r"extra\s*==\s*['\"]([^'\"]+)['\"]", marker))
            if extras and extras.issubset(
                {"test", "tests", "dev", "docs", "doc", "lint", "ci", "typing"}
            ):
                continue
            child = re.split(r"[<>=!~;\[ (]", dependency, 1)[0]
            if child:
                pending.add(child)
    import _ssl
    import _sqlite3
    import zlib
    import gzip
    import ssl
    import urllib.request
    import http.client

    runtime = {}
    for module in (_ssl, _sqlite3, zlib, gzip, ssl, urllib.request, http.client, json):
        filename = getattr(module, "__file__", None)
        if filename:
            path = Path(filename).resolve()
            runtime[str(path)] = stable_file(path)["sha256"]
        elif getattr(getattr(module, "__spec__", None), "origin", None) == "built-in":
            runtime[module.__name__] = {"builtin_in_executable": True}
        else:
            raise ValueError("runtime_module_origin_unknown:" + module.__name__)
    runtime[str(Path(sys.executable).resolve())] = stable_file(Path(sys.executable).resolve())[
        "sha256"
    ]
    resources = {
        str(path.relative_to(root)): stable_file(path)["sha256"]
        for path in sorted((root / "jobagg").rglob("*"))
        if path.is_file() and path.suffix in {".json", ".yaml", ".yml", ".csv", ".toml"}
    }
    value = {
        "package_resources": resources,
        "schema": "runtime-publication-contract-1",
        "config_sha256": config_sha256,
        "registry_sha256": registry_sha256,
        "manifest_sha256": manifest_sha256,
        "jobagg_implementation_sha256": implementation_hash(),
        "pyproject_sha256": stable_file(root / "pyproject.toml")["sha256"],
        "dispatcher": {
            str(Path(path).resolve()): stable_file(Path(path).resolve())["sha256"]
            for path in dispatcher_files
        },
        "runtime": runtime,
        "python": sys.version,
        "sqlite": sqlite3.sqlite_version,
        "dependencies": distributions,
    }
    value["sha256"] = _digest(value)
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def verify(value, *, package_root):
    if not isinstance(value, dict) or value.get("schema") != "runtime-publication-contract-1":
        raise ValueError("admitted_runtime_contract_missing_or_unknown")
    actual = build(
        package_root=package_root,
        dispatcher_files=value["dispatcher"].keys(),
        config_sha256=value["config_sha256"],
        registry_sha256=value["registry_sha256"],
        manifest_sha256=value["manifest_sha256"],
    )
    if actual != value:
        raise ValueError("admitted_runtime_contract_bytes_changed")
    return value["sha256"]
