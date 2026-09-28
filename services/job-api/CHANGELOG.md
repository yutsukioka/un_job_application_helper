# Job API Changelog

## Unreleased

### Security

- LAN exposure is now opt-in. Binding to `0.0.0.0` requires
  `JOB_API_ALLOW_LAN=1` plus either `JOB_API_TOKEN` for the
  `X-Job-Api-Token` header or `JOB_API_UNIX_SOCKET` for socket mode.
- LAN token authentication uses constant-time comparison and throttles repeated
  failed attempts.
- `score_against` input is confined to `JOB_API_SCORING_ROOT` and capped by
  `JOB_API_SCORING_MAX_BYTES`.
- Saved-search persistence and tracker writes use locked atomic file updates.
- Saved-search names now reject path separators, control characters, empty
  names, and names longer than 128 characters.
- Path disclosure in health and error responses is scrubbed to repo-relative
  or opaque identifiers.

### API Contract

- Search and detail job responses now include additive `apply_url_trust` and
  `source_url_trust` objects with `origin_host` and `matches_source_org`.
- Search request models now enforce bounds on paging, free-text fields, notes,
  and list lengths; invalid requests return FastAPI/Pydantic `422` responses.
