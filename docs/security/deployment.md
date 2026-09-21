# Security Deployment Notes

## Local and LAN launch

Run `python -m job_api.launcher` (or `job-api`) for the default
`127.0.0.1:8765` listener. The launcher uses environment variables; it does not
parse `--host` arguments or support a Unix-socket launch mode.

For a physical device on a controlled LAN, create an external token file and
start the validated launcher:

```bash
umask 077
TOKEN_FILE="$(mktemp "${TMPDIR:-/tmp}/atlas-private-api-token.XXXXXX")"
openssl rand -base64 48 | tr -d '\n' > "$TOKEN_FILE"
ATLAS_API_HOST=0.0.0.0 \
ATLAS_ALLOW_LAN=1 \
ATLAS_PRIVATE_API_MODE=token \
ATLAS_PRIVATE_API_TOKEN_FILE="$TOKEN_FILE" \
python -m job_api.launcher
```

Delete the token file after stopping the service. Configure the client with the
host's LAN address. Private routes require `Authorization: Bearer <token>`;
public vacancy search, detail, and inventory routes remain unauthenticated.
The Apple app currently has no token-entry UI, so public reads work over LAN
but its private features require a client capable of sending the bearer header.
HTTP token mode provides no transport encryption; use it only for temporary
controlled-network testing.

## Active environment variables

| Variable | Purpose |
| --- | --- |
| `ATLAS_API_HOST` | Bind address; defaults to `127.0.0.1`. |
| `ATLAS_API_PORT` | TCP port, 1–65535; defaults to `8765`. |
| `ATLAS_ALLOW_LAN` | Must be `1` for a non-loopback bind. |
| `ATLAS_PRIVATE_API_MODE` | `loopback` (default), `token`, or `disabled`; LAN requires `token`. |
| `ATLAS_PRIVATE_API_TOKEN_FILE` | External regular token file; use exactly one token source. |
| `ATLAS_PRIVATE_API_TOKEN` | Alternative direct token source, 32–4096 UTF-8 bytes. |
| `ATLAS_CORS_ORIGINS` | Exact comma-separated browser origins; no wildcard. |
| `JOB_API_STRATEGY_ROOT` | Root for scoring files; defaults to `<repo>/private`. Reads are capped at 1 MiB. |
| `JOB_API_DB` | Live database path override. |
| `JOB_API_SAVED_SEARCHES` | Saved-search storage path override. |
| `JOB_API_TRACKER` | Tracker storage path override. |

`ATLAS_TRUST_PROXY_HEADERS` must remain unset. Forwarded headers do not grant
loopback access. The older `JOB_API_ALLOW_LAN`, `JOB_API_TOKEN`,
`JOB_API_UNIX_SOCKET`, `JOB_API_SCORING_ROOT`, `JOB_API_SCORING_MAX_BYTES`, and
`X-Job-Api-Token` contract is superseded and must not be used for deployment.

See [private endpoint security](local_api_private_endpoint_security.md) and
[validated launch architecture](../architecture/secure_local_api_launch.md).
