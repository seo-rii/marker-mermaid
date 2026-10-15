from __future__ import annotations

import pytest

from marker_mermaid.protocols import RuntimeResult


class FakeRuntime:
    def __init__(self, *, valid: bool = True):
        self.valid = valid
        self.closed = False
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        self.calls.append(code)
        if not self.valid or "BROKEN" in code:
            return RuntimeResult(False, False, error="invalid fixture")
        return RuntimeResult(
            True,
            True,
            diagram_type="flowchart-v2",
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text>x</text></svg>'
            ),
        )

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_runtime():
    return FakeRuntime()


_REVIEW_BROWSER_EXPECTED = pytest.StashKey[set[str]]()
_REVIEW_BROWSER_PASSED: set[str] = set()


def pytest_addoption(parser):
    parser.addoption(
        "--require-review-browser",
        action="store_true",
        help="Fail unless every collected review_browser node ID runs and passes.",
    )


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(config, items):
    # Capture the full collection before -m/-k deselection, then compare it with
    # completed call reports. A newly marked fixture cannot silently disappear.
    config.stash[_REVIEW_BROWSER_EXPECTED] = {
        item.nodeid for item in items if item.get_closest_marker("review_browser")
    }
    _REVIEW_BROWSER_PASSED.clear()
    yield


def pytest_runtest_logreport(report):
    if report.when == "call" and report.passed:
        _REVIEW_BROWSER_PASSED.add(report.nodeid)


def pytest_sessionfinish(session):
    if not session.config.getoption("--require-review-browser"):
        return
    expected = session.config.stash.get(_REVIEW_BROWSER_EXPECTED, set())
    missing = expected - _REVIEW_BROWSER_PASSED
    if expected and not missing:
        return
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("=", "required review browser coverage failed", red=True)
        for nodeid in sorted(missing):
            reporter.write_line(f"did not run and pass: {nodeid}", red=True)
        if not expected:
            reporter.write_line("no review_browser node IDs were collected", red=True)
