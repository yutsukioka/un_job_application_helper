# AtlasVault Two-Device Convergence Proof

The C20 proof exercises two independent clients through the ciphertext-only P4
API. Each client owns separate encrypted replica, outbox, inbox, and cursor
state. Clients never hand operations directly to one another.

For every language pairing, both clients create the same record offline, edit it
concurrently, and race a delete against a later edit. The transport uploads the
opaque operations to one local-ephemeral Uvicorn backend behind loopback TLS,
retries every upload with the same idempotency key, and downloads cursor pages
from that backend. A client may see its own operation again and may receive a
previously applied page again; both cases are no-ops.

The resolution rule remains the C19 rule:

1. A tombstone is terminal for its object.
2. Otherwise, explicit operations outrank snapshot baselines.
3. Remaining candidates use the maximum tuple of Lamport counter, author device
   identifier, author sequence, and operation UUID.

The proof runs Python/Dart, Dart/Swift, and Swift/Python pairings. The two
processes in each pairing must emit the same canonical final-state SHA-256. All
three pairings must agree on the same final hash and terminal tombstone.

The backend transport envelope contains only encrypted operation bytes and
opaque metadata. No record plaintext, passphrase, raw key, or wrapped vault key
is sent to or logged by the backend. This is an engineering proof under the
single-instance D065/R024 topology, not hosted or multi-replica staging.
