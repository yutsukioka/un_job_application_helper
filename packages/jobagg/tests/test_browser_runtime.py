"""A relocated release must retain its interpreter's installed browser bundle."""

from contextlib import asynccontextmanager
import pytest

from jobagg import browser_fetch, osce_native_browser
from test_browser_fetch import make_browser


@pytest.fixture
def separated_runtime(tmp_path, monkeypatch):
    # Track the initially absent value too: the helper writes os.environ itself.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    release = tmp_path / "releases" / "reviewed"
    runtime = tmp_path / "primary"
    monkeypatch.setattr(browser_fetch, "__file__", str(release / "packages/jobagg/jobagg/browser_fetch.py"))
    monkeypatch.setattr(browser_fetch.sys, "prefix", str(runtime / "packages/jobagg/.venv"))
    bundle = runtime / "private/jobagg-runtime/browsers"
    bundle.mkdir(parents=True)
    return release, bundle


def test_relocated_release_uses_its_interpreter_companion(separated_runtime):
    _, bundle = separated_runtime
    browser_fetch.configure_browser_runtime()
    assert browser_fetch.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(bundle)


@pytest.mark.parametrize("explicit", ["0", "", "relative-cache", "/missing/operator-cache"])
def test_explicit_configuration_is_never_overridden(separated_runtime, monkeypatch, explicit):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", explicit)
    browser_fetch.configure_browser_runtime()
    assert browser_fetch.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == explicit


def test_source_bundle_keeps_priority_even_if_empty(separated_runtime):
    release, _ = separated_runtime
    source_bundle = release / "private/jobagg-runtime/browsers"
    source_bundle.mkdir(parents=True)
    browser_fetch.configure_browser_runtime()
    assert browser_fetch.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(source_bundle)


@pytest.mark.parametrize("suffix", [".venv", "packages/other/.venv", "packages/jobagg/venv", "usr"])
def test_other_runtime_layouts_leave_playwright_defaults(separated_runtime, monkeypatch, suffix):
    _, bundle = separated_runtime
    monkeypatch.setattr(browser_fetch.sys, "prefix", str(bundle.parents[2] / suffix))
    browser_fetch.configure_browser_runtime()
    assert "PLAYWRIGHT_BROWSERS_PATH" not in browser_fetch.os.environ


def test_missing_or_non_directory_bundle_leaves_defaults(separated_runtime):
    _, bundle = separated_runtime
    bundle.rmdir()
    browser_fetch.configure_browser_runtime()
    assert "PLAYWRIGHT_BROWSERS_PATH" not in browser_fetch.os.environ
    bundle.write_text("not an installed runtime")
    browser_fetch.configure_browser_runtime()
    assert "PLAYWRIGHT_BROWSERS_PATH" not in browser_fetch.os.environ


@pytest.mark.parametrize("native", [False, True])
def test_both_renderers_configure_before_driver_start(separated_runtime, tmp_path, monkeypatch, native):
    """Exercise the actual entry points; stop before any browser or request."""
    _, bundle = separated_runtime
    api = pytest.importorskip("playwright.async_api" if native else "playwright.sync_api")
    browser, client = make_browser(tmp_path, monkeypatch, {})

    class DriverReached(RuntimeError):
        pass

    def driver():
        assert browser_fetch.os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == str(bundle)
        raise DriverReached("configured before Playwright startup")

    if native:
        @asynccontextmanager
        async def unused_proxy(*args):
            yield "http://127.0.0.1:9"

        monkeypatch.setattr(osce_native_browser, "pinned_browser_proxy", unused_proxy)
        monkeypatch.setattr(api, "async_playwright", driver)
        browser = osce_native_browser.OSCENativeBrowser(client, browser.capture, browser.contract)
    else:
        monkeypatch.setattr(api, "sync_playwright", driver)
    with pytest.raises(DriverReached):
        browser.render("https://jobs.example.test/jobs/101")
    assert client.calls == [] and browser.capture.dispatched == 0
