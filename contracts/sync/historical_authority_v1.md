# Bounded Historical Authority v1

Status: C30/D106 implementation slice, not T74 or production sign-off.

This supplement transports existing public evidence. It neither signs new
authority facts nor changes HPKE, epoch ciphertext, D087 activation, D089 delivery,
or origin-1/P6 validation. No wrapper, private key, SAS, or old ciphertext archive
belongs in this object.

## Supported Witness

Version 1 deliberately covers one bounded lineage: signed descriptor-root genesis
at sequence 1, one signed removal, a one-epoch advance, and the signed D099
enrollment leading to the exact sequence-2 D102 anchor. Both state views must be
signed by the pinned, continuously ACTIVE issuer. The verified signed descriptors
must reconstruct both the genesis descriptor root and the complete pre-removal
public registry. Existing removal and enrollment verifiers must reproduce the
exact current registry root. No missing registry transition is inferred.

For the exact D102-authenticated current opaque collection bytes `B`, recompute:

```
d = SHA256(B)
c = SHA256("atlasvault-state-commitment-v1\n" + collection_id + "\n1\n"
           + ZERO_ROOT + "\n" + d + "\n")
```

`c` must equal the earlier signed state view's collection root. This is an
authenticated inclusion reconstruction, not a claim that a record merely existed
because an old key can decrypt it. Each covered epoch-N envelope must also have
canonical bound metadata and its historical ACTIVE author's valid signature.

The verifier returns a read-only mapping from exact object/envelope hash to its
verified historical public author. It never returns a general historical-key
lookup, installs an old operational registry, changes delivery eligibility, or
authorizes a current/future write. A late envelope signed using a retained old
key is not covered unless its exact bytes were in the authenticated old view.

## Binding And Bounds

The supplement's context is compared to the independently verified, recipient-
pinned D102 anchor: account, vault, anchor root, current registry root, active
epoch, registry generation, current ciphertext-projection hash, recipient, and
enrollment transcript. The anchor already binds the recipient agreement-key
fingerprint, signed enrollment, issuer, and current state root. Supplement fields
are never taken as independent authority. Maximum canonical supplement size is
128 KiB; at most 32 signed descriptors, exactly two signed state views, and at
most 256 current records in a projection of at most 1 MiB are accepted.

Registry entry order is canonicalized by existing root functions. History order
is checked by authenticated sequence and predecessor roots, never transport order.
Duplicate descriptors, missing entries, changed signatures, substituted context,
discontinuous views, or a contradictory fork fail closed.

## Missing Preimages

If the current collection differs from the signed earlier preimage, verification
returns `ATLAS_HISTORY_PREIMAGE_REQUIRED`. In particular, retained A plus changed
B does not prove A's old inclusion when the earlier signed aggregate covered
A plus old B. Do not export old B, infer its hash, trust a supplement assertion,
or fall back to an unverified historical key lookup. Other lineage shapes are
unsupported by this bounded version; failure is not a claim that no witness could
exist elsewhere. Any wider format requires a separately reviewed implementation
within the governing authorization, not silent acceptance by this verifier.

## Local Installation

Installation is allowed only in the standalone anchored store before immutable
runtime publication. Verify all inputs, then atomically replace that store's
existing encrypted `{anchor, state}` object, adding the supplement and exact
current projection to the anchor while leaving accepted state unchanged. The
runtime publication's existing origin hash binds the installed supplement.
Origin-1 stores have no installation route.

An interrupted replacement leaves either the old complete store (retry required)
or the complete supplemented store. Exact supplement and original-bootstrap
retries are idempotent and never erase the installed evidence. A changed
supplement cannot replace it. A signed fork remains durable and fenced; installing
historical authority cannot clear it. This is application-level atomicity, not
protection against malicious rollback of the local filesystem.

## Remaining Boundary

Production SAS-to-HPKE delivery and recipient installation are separate gates.
The existing coordinator must not use its rejected legacy interoperability
session to transport this data. Passing these authority tests alone does not
complete the production pairing ceremony or T74. R024, R026, R029 and the external
Dart HPKE review remain unchanged. First-contact trust still comes from the
recipient-bound, active-device-signed D102 ceremony, not a server assertion.
