# Browser contract for ATS skills

Read before live ATS navigation, inspection, entry or uploads. Each organization
skill retains its own field, authorization and save rules.

Use **Codex Browser (the in-app browser)** and its currently documented browser
APIs. Check directly exposed tools as well as any deferred tool registry before
declaring the route unavailable. A panel-opening tool alone cannot inspect or
operate a page.

The browser surface of `mcp__cua_repl` / `cua_repl` is permitted when the unified
runtime provides it. Select `iab` through the documented entry point and read
the returned documentation before further interaction. The runtime name does
not determine the surface: browser tab/page APIs are distinct from native-app
or desktop automation. Do not use native-app accessibility, desktop mouse or
keyboard automation, screenshot-coordinate actions, or another browser/HTTP
automation route as a fallback.

Use rendered DOM snapshots, semantic locators, supported browser scrolling and
documented upload/file-chooser controls. Re-observe after navigation, editor
changes and uncertain actions. Do not guess selectors, reuse stale row indexes,
read hidden application/session state, or use private endpoints to bypass the UI.
Do not mutate file inputs through page evaluation or invent browser methods.

Sign-in and verification take place directly in the visible Codex Browser tab.
The user supplies credentials and handles authentication terms; never request
passwords or codes in chat. Reuse an authenticated session when available.

If a control cannot be operated through documented browser methods, report the
specific action and limitation. Leave that step pending, retain verified saved
progress, and continue independent work. Inspect current state before retrying
a timed-out save or upload; do not repeat an ambiguous mutation blindly. A native
file dialog is not permission to switch to desktop automation. The user may
complete that exact upload/control manually in the same tab, after which verify
the resulting browser state.

Honor existing authorization for requested edits and saves. An approval rejection
is not a technical selector failure: do not bypass it through another route.
Explain the rejected action and reason, complete unaffected work, and obtain only
the missing confirmation when required. Never submit or certify an application
under these ATS input skills.
