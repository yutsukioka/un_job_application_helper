"""Exercise KVM setup without privileged commands or a real KVM device."""

import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/android_kvm.sh"

# Bash functions replace host operations only in the child process. The fake
# device is inaccessible until udev has settled, reproducing the readiness race.
STUBS = r'''
trigger_count=0
settled=no
test() {
  if [[ $# == 2 && $2 == /dev/kvm ]]; then
    case "$1" in
      -c) [[ $KVM_SCENARIO != missing_device ]] ;;
      -r) [[ $settled == yes && $KVM_SCENARIO != read_denied ]] ;;
      -w)
        [[ $settled == yes && $KVM_SCENARIO != write_denied ]] || return 1
        [[ $KVM_SCENARIO != delayed_permissions || $trigger_count -ge 3 ]]
        ;;
      *) return 98 ;;
    esac
  else
    builtin test "$@"
  fi
}
sudo() {
  case "$*" in
    'tee /etc/udev/rules.d/99-kvm4all.rules')
      cat >/dev/null
      [[ $KVM_SCENARIO != rule_error ]] || return 7
      ;;
    'udevadm control --reload-rules')
      [[ $KVM_SCENARIO != reload_error ]] || return 6
      ;;
    'udevadm trigger --name-match=kvm')
      trigger_count=$((trigger_count + 1))
      settled=no
      printf 'STUB trigger %s\n' "$trigger_count"
      [[ $KVM_SCENARIO != trigger_error ]] || return 4
      ;;
    'udevadm settle --timeout=10')
      printf 'STUB settle\n'
      [[ $KVM_SCENARIO != settle_error ]] || return 5
      settled=yes
      ;;
    'udevadm info --name=/dev/kvm')
      printf 'STUB device info\n'
      return 9  # Even failed diagnostics must not replace the setup exit code.
      ;;
    *) printf 'Unexpected privileged command: %s\n' "$*" >&2; return 98 ;;
  esac
}
sleep() { printf 'STUB sleep %s\n' "$*"; }
ls() { printf 'STUB device listing\n'; }
lsmod() { printf 'kvm 1 1\n'; }
lscpu() { printf 'Virtualization: test\n'; }
'''


def run_setup(tmp_path, scenario):
    stubs = tmp_path / "stubs.bash"
    stubs.write_text(STUBS)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env={**os.environ, "BASH_ENV": str(stubs), "KVM_SCENARIO": scenario},
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )


def test_waits_for_udev_before_checking_permissions(tmp_path):
    result = run_setup(tmp_path, "ready_after_settle")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("STUB trigger") == 1
    assert result.stdout.index("STUB settle") < result.stdout.index(
        "Android KVM is readable and writable"
    )
    assert "STUB sleep" not in result.stdout
    assert "Android KVM diagnostics" not in result.stdout


def test_recovers_when_permissions_arrive_on_last_attempt(tmp_path):
    result = run_setup(tmp_path, "delayed_permissions")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("STUB trigger") == 3
    assert result.stdout.count("STUB sleep 2") == 2
    assert result.stderr.count("not readable and writable") == 2


@pytest.mark.parametrize(
    ("scenario", "diagnostic"),
    [
        ("missing_device", "/dev/kvm test -c: no"),
        ("read_denied", "/dev/kvm test -r: no"),
        ("write_denied", "/dev/kvm test -w: no"),
        ("trigger_error", "KVM udev trigger or settle failed"),
        ("settle_error", "KVM udev trigger or settle failed"),
    ],
)
def test_persistent_unavailability_is_fatal_and_diagnostic(tmp_path, scenario, diagnostic):
    result = run_setup(tmp_path, scenario)
    assert result.returncode == 1
    assert result.stdout.count("Android KVM setup attempt") == 3
    assert result.stdout.count("STUB sleep 2") == 2
    assert "Android KVM diagnostics" in result.stdout
    assert "attempt 3/3" in result.stderr
    assert diagnostic in result.stdout + result.stderr
    assert "Android KVM is readable and writable" not in result.stdout
    if scenario == "missing_device":
        assert "STUB trigger" not in result.stdout
    if scenario == "trigger_error":
        assert "STUB settle" not in result.stdout


@pytest.mark.parametrize(
    ("scenario", "exit_code", "stage"),
    [
        ("rule_error", 7, "installing the KVM udev rule"),
        ("reload_error", 6, "reloading KVM udev rules"),
    ],
)
def test_configuration_failure_is_not_retried_or_hidden(tmp_path, scenario, exit_code, stage):
    result = run_setup(tmp_path, scenario)
    assert result.returncode == exit_code
    assert stage in result.stderr
    assert "Android KVM diagnostics" in result.stdout
    assert "STUB trigger" not in result.stdout
    assert "STUB sleep" not in result.stdout
