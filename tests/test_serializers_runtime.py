from __future__ import annotations

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.serializers import (
    serialize_architecture,
    serialize_flowchart,
    serialize_gantt,
    serialize_mindmap,
    serialize_sequence,
    serialize_swimlane,
    serialize_timeline,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

CASES = [
    serialize_flowchart(
        {
            "title": "Flow",
            "nodes": [{"id": "A", "label": "Start"}, {"id": "B", "label": "End"}],
            "edges": [{"source": "A", "target": "B"}],
        }
    ),
    serialize_swimlane(
        {
            "title": "Swim",
            "lanes": [
                {"id": "user", "label": "User", "nodes": [{"id": "A", "label": "Ask"}]},
                {"id": "system", "label": "System", "nodes": [{"id": "B", "label": "Answer"}]},
            ],
            "edges": [{"source": "A", "target": "B"}],
        }
    ),
    serialize_sequence(
        {
            "title": "Sequence",
            "participants": [{"id": "U", "label": "User"}, {"id": "A", "label": "API"}],
            "messages": [{"source": "U", "target": "A", "label": "Call"}],
        }
    ),
    serialize_mindmap(
        {"title": "Mind", "root": {"label": "Root", "children": [{"label": "Child"}]}}
    ),
    serialize_timeline({"title": "Timeline", "events": [{"time": "2026", "label": "Launch"}]}),
    serialize_gantt(
        {
            "title": "Plan",
            "date_format": "YYYY-MM-DD",
            "sections": [
                {
                    "title": "Build",
                    "tasks": [
                        {"label": "Code", "id": "t1", "start": "2026-01-01", "end": "2026-01-02"}
                    ],
                }
            ],
        }
    ),
    serialize_architecture(
        {
            "title": "Architecture",
            "groups": [{"id": "cloud", "label": "Cloud", "icon": "cloud"}],
            "services": [
                {"id": "api", "label": "API", "group": "cloud", "icon": "server"},
                {"id": "db", "label": "DB", "group": "cloud", "icon": "database"},
            ],
            "edges": [{"source": "api", "target": "db"}],
        }
    ),
]


def test_bpmn_swimlane_is_explicit_flowchart_fallback():
    code = CASES[1]
    assert code.startswith("flowchart")
    assert "subgraph user" in code


@pytest.mark.integration
def test_phase_one_serializers_parse_and_render_in_real_mermaid():
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        for code in CASES:
            outcome = validator.validate(code, 20)
            assert outcome.runtime.syntax_valid, (code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (code, outcome.runtime.error, outcome.warnings)
    finally:
        process = runtime._process
        runtime.close()
    assert process is not None
    assert process.poll() is not None
