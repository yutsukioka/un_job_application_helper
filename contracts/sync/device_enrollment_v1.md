# Pairing Enrollment v1 (D099)

This additive contract authenticates one new device at the current epoch. It
does not edit an activation record, reinterpret a per-device delivery proof,
rotate an epoch, or change HPKE. The original activation ID remains an anchor.

## Trust Inputs

Verification requires the application's durably authenticated current registry,
account, vault, active epoch, activation ID, state root, registry generation,
and locally SAS-confirmed transcript hash. Do not derive these expected inputs
from the untrusted incoming record. The comparison value itself is absent.
Only ACTIVE custody may sign or accept; all pending, revoked, and fork states
fail closed. The issuer is an ACTIVE member of the prior registry.

The target signing and agreement public keys determine its existing v1 device
ID. Its agreement-key fingerprint is SHA-256 over the public-key bytes. An
identity already present, including a retained REVOKED entry, is not a new
enrollment. Revoked entries are not removed or restored by this operation.

## Signed Record

`atlasvault_device_enrollment_v1.schema.json` is the closed wire schema.
The signature covers the format/version; account/vault; activation and state
roots; current and next registry generation; active epoch; prior and resulting
registry roots; target identity/public keys/fingerprint; confirmed transcript
hash; issuer; confirmation category; and signature algorithm.

Canonical bytes are the existing ASCII, recursively key-sorted compact JSON of
all fields except `root` and `signature_b64`. The root is SHA-256 of
`atlasvault-device-enrollment-v1\n` followed by those bytes. Existing Ed25519
signs `atlasvault-device-enrollment-signature-v1\0` followed by the 32-byte
root. This is protocol-domain separation, not a replacement cryptosystem.
The registry uses the unchanged C25 root function and is sorted by device ID.

Registry generation is scoped to the activation ID. Enrollment increments it
by exactly one without changing the epoch. A later activation is a separate
immutable anchor, not an in-place edit or replay of this record.

## Admission Boundary

The verifier rejects a repeated addition. Durable admission must separately
recognize an exact already-accepted record as an idempotent retry; it must not
use re-verification against a substituted older registry to bypass replay
protection. A record is not a backend acceptance receipt or a key-delivery
artifact. Possessing it alone does not initialize a production runtime.

The signature primitive is implemented and vector-tested. Production epoch
publication, bootstrap, and UI admission must be verified separately; these
primitives alone do not establish completion of C30/T75.

No plaintext records, private keys, vault keys, wrappers, tokens, passphrases,
or displayed comparison values belong in this record or diagnostics. Existing
P6 fork/withholding limitations and P7 historical-access limitations remain.
