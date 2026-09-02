# Multi-Device Malicious-Server Phase Gate (C24)

## Scope

The C24 harness exercises the existing C21-C23 production validators, encrypted
queues, recovery store, and backend append API. It does not change signatures,
wire formats, recovery policy, epoch advancement, or revocation.

Run `python scripts/ci/atlasvault_malicious_server.py --output <report.json>`
on a macOS host with Swift, Flutter/Dart, VaultSync, FastAPI, and Uvicorn available.
The existing security workflow runs it on its Apple runner. Loopback ports 8443
and 18080 must be free. No cloud account or stored credential is used.

## Topology and Adversary

One Uvicorn instance sits behind a local TLS proxy. Synthetic authenticated
accounts have two signed device descriptors at epoch 2. The harness encrypts
archived candidate packets, uploads them through the production opaque-object
API, and reads back and authenticates the exact transported ciphertext. A
deliberately hostile delivery controller selects old, contradictory, substituted,
or resurrection-bearing authenticated packets for each recipient. Pre-signed
conflicting views model evidence a hostile server might possess; this does not
claim a server without a signing key can forge signatures.

Python-Dart, Dart-Swift, and Swift-Python pairs use separate processes and storage
directories. Each device owns an encrypted accepted-history/recovery file plus
its own durable inbox/cursor and outbox files. Combining accepted history and
recovery evidence in one atomic guard file preserves the C23 durability boundary;
no file is shared between devices. Guard admission precedes queue receipt updates.
The adapter does not substitute a mock validator for the production guard.

Each process is killed after persisting its baseline, then again after persisting
the attack result. A fresh process reopens every state. The report compares
accepted-state, recovery, cursor, and authenticated-evidence hashes across restart.
This tests durable fences, not arbitrary crash points between separate queue files.

## Required Attacks

- Valid old commitments, authenticated old snapshots, and pre-delete replay.
- Delayed creates/edits, missing terminal records, and pre-delete compaction.
- Same-sequence conflicting roots and invalid predecessors.
- Replayed, added, removed, or substituted registry views.
- Cross-account, cross-vault, and incorrect or regressing epoch contexts.
- Independently persisted split histories and registry forks, followed by explicit
  authenticated evidence exchange. Both clients fence without selecting a branch.
- Exact accepted retries, which do not advance state or create another receipt.
- Known-update omission versus globally unseen withholding and absent peer evidence.
- Concurrent conflicting POSTs to the real commitment endpoint: exactly one child
  succeeds; exact retry is harmless; other appends leave the stored history intact.

Rejected input must not advance an accepted root or cursor. A terminal tombstone
must remain unchanged. Recovery metadata is bounded and contains no ciphertext
contents, vault plaintext, keys, tokens, or passphrases. Genuine forks preserve both
signed branches and remain `MANUAL_REQUIRED`; the existing manual disposition tests
verify unsafe selection remains `RECOVERY_PENDING` for P7.

## Detection Trigger and Limits

A previously synchronized client detects replay below its own durable sequence.
Equivocation becomes detectable when contradictory signed evidence for overlapping
history is presented or exchanged. Matching prefixes are not a freshness proof.
Do not force divergent fork histories to equal hashes by selecting one branch.

The following remain unproven and are not phase-gate success claims:

- First-contact freshness without a trusted checkpoint.
- Globally unseen updates withheld from an isolated client.
- Timely discovery when the server withholds all peer evidence.
- Rollback of the local filesystem itself.
- Safe fork resolution without later P7 revocation/key-rotation policy.

The backend serializes conflicting appends; it neither detects selective
withholding nor chooses a recovery branch. R024 single-instance limits remain.
Reports retain only public context, hashes, counters, attack categories and process
IDs. Temporary TLS material, session tokens, ciphertext packets and encrypted client
state are deleted; none is uploaded as a workflow artifact.
