#!/usr/bin/env bash
# Configure KVM on an ephemeral GitHub-hosted Linux runner.
set -euo pipefail

stage="installing the KVM udev rule"

diagnostics() {
  printf '::group::Android KVM diagnostics\n'
  uname -sr || true
  id || true
  for flag in c r w; do
    if test "-$flag" /dev/kvm; then
      printf '/dev/kvm test -%s: yes\n' "$flag"
    else
      printf '/dev/kvm test -%s: no\n' "$flag"
    fi
  done
  ls -l /dev/kvm || true
  lsmod | awk '$1 ~ /^kvm/ { print }' || true
  lscpu | grep -E 'Architecture:|Virtualization:|Hypervisor vendor:' || true
  sudo udevadm info --name=/dev/kvm || true
  printf '::endgroup::\n'
}

on_exit() {
  status=$?
  if (( status != 0 )); then
    printf '::error::Android KVM setup failed while %s (exit %s). Emulator tests cannot run without writable /dev/kvm.\n' "$stage" "$status" >&2
    diagnostics
  fi
}
trap on_exit EXIT

printf '%s\n' \
  'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' |
  sudo tee /etc/udev/rules.d/99-kvm4all.rules >/dev/null
stage="reloading KVM udev rules"
sudo udevadm control --reload-rules

# Triggering udev is asynchronous. Wait for rule processing before checking
# access, and allow a bounded retry for runner device/permission readiness.
for attempt in 1 2 3; do
  stage="waiting for KVM device and permissions (attempt $attempt/3)"
  printf 'Android KVM setup attempt %s/3\n' "$attempt"
  if test -c /dev/kvm; then
    if sudo udevadm trigger --name-match=kvm && sudo udevadm settle --timeout=10; then
      if test -r /dev/kvm && test -w /dev/kvm; then
        ls -l /dev/kvm
        printf 'Android KVM is readable and writable by the runner.\n'
        exit 0
      fi
      printf '::warning::KVM device exists but is not readable and writable by the runner.\n' >&2
    else
      printf '::warning::KVM udev trigger or settle failed.\n' >&2
    fi
  else
    printf '::warning::/dev/kvm is not available as a character device.\n' >&2
  fi
  if (( attempt < 3 )); then
    sleep 2
  fi
done

exit 1
