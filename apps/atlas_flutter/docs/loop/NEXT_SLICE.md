# Next Slice

Pending gate: G1 parity matrix approval.

Intent: after G1 approval, implement the Atlas Flutter domain/API foundation without touching shared packages or `services/job-api`. This slice will port the job/search/detail DTOs, filter helper logic, base URL normalization, local API client interface, and formatter-independent display helpers so later UI slices can be tested against stable contracts.

Acceptance tests to add before implementation:

- Unit tests for base URL normalization using the Swift `AtlasAPIClientTests` cases.
- Unit tests for decoding cached camelCase search rows and snake_case API rows into one Dart `JobSearchResult` model.
- Unit tests for grade formatting, organization display cleanup, source initials, deadline urgency/text, score normalization, and active filter chip removal/reset behavior.
- Mock HTTP tests proving the client targets `/api/health`, `/api/search`, `/api/job-detail`, `/api/saved-searches`, `/api/tracker`, `/api/updates`, and `/api/sources` with compatible methods and JSON keys.

No implementation work should start until the PR has an explicit `APPROVED: G1` comment.
