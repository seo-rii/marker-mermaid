"""Exercise CI's required-browser gate through actual pytest subprocess runs."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@pytest.mark.parametrize(
    ("body", "selection", "passes"),
    [
        ("assert True", [], True),
        ("pytest.skip('missing browser')", [], False),
        ("assert True", ["-k", "unrelated"], False),
    ],
)
def test_required_browser_gate_detects_unexecuted_nodes(pytester, body, selection, passes):
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    pytester.makeini(
        "[pytest]\nmarkers =\n    review_browser: requires real browser execution\n"
    )
    pytester.makepyfile(
        f"import pytest\n@pytest.mark.review_browser\ndef test_browser():\n    {body}\n"
    )
    result = pytester.runpytest_subprocess("--require-review-browser", *selection)
    assert result.ret == (pytest.ExitCode.OK if passes else pytest.ExitCode.TESTS_FAILED)
    if not passes:
        result.stdout.fnmatch_lines(["*required review browser coverage failed*"])


def test_required_browser_gate_rejects_empty_browser_collection(pytester):
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    pytester.makepyfile("def test_unit(): assert True")
    result = pytester.runpytest_subprocess("--require-review-browser")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*no review_browser node IDs were collected*"])
