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
