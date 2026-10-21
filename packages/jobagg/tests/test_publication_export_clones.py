"""Cloned export copies retain the same independent, sealed recovery evidence."""

import ctypes
import errno
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

from jobagg.pipelines import publication_exports as publication
import test_publication_exports as fixtures


export_case = fixtures.export_case


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS native clone API")
def test_native_clones_isolate_live_export_prepared_bytes_and_rollback(export_case, monkeypatch):
    plan, item, target, old = export_case
    probe = target.with_name("clone-probe")
    if not publication._clone_file(target, probe):
        pytest.skip("fixture filesystem does not support cloning")
    assert probe.stat().st_ino != target.stat().st_ino
    probe.unlink()
    monkeypatch.setattr(publication, "open_artifact", lambda *_: pytest.fail("stream fallback"))

    assert publication.publish_exports(plan, [item])["complete"]
    checkpoint = fixtures._journal(plan)["entries"][str(target)]
    prepared, backup = Path(checkpoint["prepared"]), Path(checkpoint["backup_path"])
    new = target.read_bytes()
    assert len({p.stat().st_ino for p in (target, prepared, backup)}) == 3
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in (target, prepared, backup))
    target.write_bytes(b"later live edit")
    assert prepared.read_bytes() == new
    assert backup.read_bytes() == old
    prepared.write_bytes(b"later prepared edit")
    assert target.read_bytes() == b"later live edit"
    assert backup.read_bytes() == old
    backup.write_bytes(b"later backup edit")
    assert target.read_bytes() == b"later live edit"
    assert prepared.read_bytes() == b"later prepared edit"


def test_clone_readback_rejects_corruption_before_sealing(export_case, monkeypatch):
    plan, item, target, old = export_case

    def corrupt_clone(source, destination):
        destination.write_bytes(b"not the bound source bytes")
        return True

    monkeypatch.setattr(publication, "_clone_file", corrupt_clone)
    with pytest.raises(ValueError, match="export_copy_readback_failed"):
        publication.publish_exports(plan, [item])
    assert target.read_bytes() == old
    root = Path(plan["generation_root"])
    assert not (root / "before_exports" / target.name).exists()
    assert not list((root / "before_exports").iterdir())
    monkeypatch.setattr(publication, "_clone_file", lambda *_: False)
    assert publication.publish_exports(plan, [item])["complete"]


@pytest.mark.parametrize("error", [errno.ENOSPC, errno.EACCES, errno.EIO])
def test_clone_operational_failure_holds_publication_without_stream_retry(
    export_case, monkeypatch, error
):
    plan, item, target, old = export_case

    def fail_clone(*args):
        raise OSError(error, "clone failed")

    monkeypatch.setattr(publication, "_clone_file", fail_clone)
    monkeypatch.setattr(publication, "open_artifact", lambda *_: pytest.fail("stream retry"))
    with pytest.raises(OSError) as raised:
        publication.publish_exports(plan, [item])
    assert raised.value.errno == error
    assert target.read_bytes() == old
    assert not (Path(plan["generation_root"]) / "before_exports" / target.name).exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS syscall error contract")
@pytest.mark.parametrize("error, fallback", [
    (errno.ENOTSUP, True), (errno.EXDEV, True), (errno.ENOSYS, True),
    (errno.ENOSPC, False), (errno.EACCES, False), (errno.EIO, False),
    (errno.EINVAL, False),
])
def test_native_clone_falls_back_only_when_unavailable(tmp_path, monkeypatch, error, fallback):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"bound source")

    def syscall(source_fd, directory_fd, filename, flags):
        assert stat.S_ISREG(os.fstat(source_fd).st_mode)
        assert stat.S_ISDIR(os.fstat(directory_fd).st_mode)
        assert filename == b"target" and flags == 0
        ctypes.set_errno(error)
        return -1

    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: SimpleNamespace(fclonefileat=syscall))
    if fallback:
        assert publication._clone_file(source, target) is False
    else:
        with pytest.raises(OSError) as raised:
            publication._clone_file(source, target)
        assert raised.value.errno == error
    assert not target.exists()
    assert syscall.argtypes == [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    assert syscall.restype == ctypes.c_int


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS native clone API")
def test_native_clone_rejects_symlink_source(tmp_path):
    source, link, target = tmp_path / "source", tmp_path / "link", tmp_path / "target"
    source.write_bytes(b"source")
    link.symlink_to(source)
    with pytest.raises(OSError) as raised:
        publication._clone_file(link, target)
    assert raised.value.errno == errno.ELOOP
    assert not target.exists()


def test_non_mac_platform_uses_stream_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(publication.sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: pytest.fail("loaded native clone API"))
    assert publication._clone_file(tmp_path / "source", tmp_path / "target") is False
