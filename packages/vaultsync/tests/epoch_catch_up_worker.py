"""Process-only synthetic crash worker; no key values in diagnostics."""

import json
import sys
import time
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
libraries = (
    Path(sys.executable).parent.parent
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
)
sys.path[:0] = [
    str(repo / "packages/vaultsync"),
    str(repo / "services/atlasvault-api"),
    str(repo / "tests"),
    str(libraries),
]
sys.path.extend(
    str(p) for p in (repo / "packages/vaultsync/.venv/lib").glob("python*/site-packages")
)
from atlasvault_c27_fixture import client

root = Path(sys.argv[1])
stage = sys.argv[2]
v = json.loads((root / "public-packets.json").read_text())
c = client(root, 2, v["registry"], v["view"])


def checkpoint(name):
    if name == stage:
        (root / "ready").write_text(name)
        while True:
            time.sleep(1)


c._catch_up_for_testing(
    v["packets"],
    current_activation_id=v["target"],
    agreement_private_key=bytes([22]) * 32,
    checkpoint=checkpoint,
)
if stage in ("cleanup_pending", "deleted_epoch"):

    class Deletion:
        def delete_epoch(self, epoch):
            (root / f"deleted-{epoch}").write_text("deleted")

        def contains_epoch(self, epoch):
            return not (root / f"deleted-{epoch}").exists()

    c._cleanup_epochs_for_testing(retain_epochs={5}, storage=Deletion(), checkpoint=checkpoint)
