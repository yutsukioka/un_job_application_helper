# Browser runtime in separate releases

The Python package and Playwright's browser installation are separate runtime
dependencies. A release checkout may load JobAgg code while using the Python
environment under another checkout's `packages/jobagg/.venv`.

Both guarded browser renderers configure Playwright before starting its driver:

1. Preserve an explicitly set `PLAYWRIGHT_BROWSERS_PATH` unchanged.
2. Otherwise use `private/jobagg-runtime/browsers` under the source checkout,
   if that directory exists.
3. Otherwise, only when `sys.prefix` ends in `packages/jobagg/.venv`, use
   `private/jobagg-runtime/browsers` under that interpreter's checkout, if present.
4. Otherwise leave Playwright's default browser location unchanged.

This lookup does not install browsers, search other checkouts, choose another
browser version, or retry a different executable after a failed launch.
Playwright still requires its own matching browser revision. An explicitly
configured or source-local installation that is incomplete continues to fail.
See [Playwright's browser installation and location documentation](https://playwright.dev/python/docs/browsers).

## October 5 regression

The independent release loaded the same reviewed code with the existing Python
environment, but lacked the ignored source-local browser directory. Both
renderers derived the bundle location exclusively from the source file. The
OSCE listing therefore tried the empty default macOS cache and failed before
navigation, although the interpreter's checkout already held its matching
Chromium installation. This is distinct from earlier OSCE HTTP 403 responses.

The regression tests cover separate source/runtime trees, configuration
precedence, missing bundles, other venv layouts, and both renderer entry points.
Historical OSCE listing/detail, preservation and publication tests retain their
existing fixtures and contracts.

## Activation boundary

A successful local browser launch proves only runtime availability. Deployment
still requires the reviewed whole-package implementation binding and the
existing ownership, maintenance and publication gates. The worker includes
that binding in retry-input fingerprints: changing it can make typed blocked
tasks eligible for re-enqueue when revisited. Review that queue effect before
activation; do not bulk-reset tasks, attempts, quotas or host holds.

After an approved correction, verify a fresh complete listing, detail identity
and main text, persistence, publication and readback separately. Existing
successful descriptions must survive. Supplementary attachments stay disabled.
