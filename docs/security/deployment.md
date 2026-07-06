# Security Deployment Notes

## Job API LAN Exposure

Default deployment is loopback-only:

- Host: `127.0.0.1`
- Port: `8765`
- LAN access: disabled

Use this decision tree before changing the bind address:

1. If only local desktop or simulator clients need access, keep the default
   `job-api` bind at `127.0.0.1:8765`.
2. If another local process can use a Unix domain socket, set
   `JOB_API_UNIX_SOCKET=/path/to/job-api.sock` and avoid TCP LAN exposure.
3. If a physical device must reach the service over LAN, set
   `JOB_API_ALLOW_LAN=1`, configure `JOB_API_TOKEN`, and start with
   `job-api --host 0.0.0.0`.

Startup refuses `0.0.0.0` unless LAN exposure is explicit and either
`JOB_API_TOKEN` or `JOB_API_UNIX_SOCKET` is configured. Clients using token
mode must send the token in the `X-Job-Api-Token` header.

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `JOB_API_ALLOW_LAN` | Must be exactly `1` to permit binding to `0.0.0.0`. |
| `JOB_API_TOKEN` | Shared secret expected in the `X-Job-Api-Token` header. |
| `JOB_API_UNIX_SOCKET` | Unix domain socket path for local socket mode. |
