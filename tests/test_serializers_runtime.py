from __future__ import annotations

import pytest

from marker_mermaid.accessibility import supports_accessibility_directives
from marker_mermaid.config import SecurityProfile
from marker_mermaid.models import (
    MAX_ID_CHARS,
    MAX_SCENE_ELEMENTS,
    MAX_SCENE_RELATIONS,
    MAX_TEXT_CHARS,
)
from marker_mermaid.serializers import (
    SerializationError,
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
            "groups": [{"id": "phase", "label": "Phase", "member_ids": ["A", "B"]}],
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
    serialize_sequence(
        {
            "participants": [
                {"id": "A-B", "label": "First"},
                {"id": "A B", "label": "Second"},
            ],
            "messages": [{"source": "A-B", "target": "A B", "label": "Next"}],
        }
    ),
]


def test_bpmn_swimlane_is_explicit_flowchart_fallback():
    code = CASES[1]
    assert code.startswith("flowchart")
    assert "subgraph user" in code


def test_flowchart_groups_are_emitted_as_subgraphs():
    assert 'subgraph phase["Phase"]' in CASES[0]


def test_sequence_participant_ids_are_collision_free_and_unambiguous():
    code = serialize_sequence(
        {
            "participants": [
                {"id": "A-B", "label": "First"},
                {"id": "A B", "label": "Second"},
            ],
            "messages": [{"source": "A-B", "target": "A B", "label": "Next"}],
        }
    )

    assert "participant A_B as First" in code
    assert "participant A_B_2 as Second" in code
    assert "A_B->>A_B_2: Next" in code

    with pytest.raises(SerializationError, match="participant ids must be unique"):
        serialize_sequence(
            {
                "participants": ["client", {"id": "client", "label": "Duplicate"}],
                "messages": [],
            }
        )


@pytest.mark.parametrize(
    ("participants", "message"),
    [
        (["A"] * (MAX_SCENE_ELEMENTS + 1), "participant count exceeds"),
        ([{"id": "x" * (MAX_ID_CHARS + 1)}], "identifier limit"),
        (
            [{"id": "A", "label": "x" * (MAX_TEXT_CHARS + 1)}],
            "label exceeds",
        ),
    ],
)
def test_sequence_participant_planning_is_resource_bounded(participants, message):
    with pytest.raises(SerializationError, match=message):
        serialize_sequence({"participants": participants, "messages": []})


@pytest.mark.parametrize(
    ("messages", "message"),
    [
        ([{}] * (MAX_SCENE_RELATIONS + 1), "message count exceeds"),
        (["not-an-object"], "messages must be objects"),
        ({"source": "A", "target": "A"}, "messages must be a list"),
    ],
)
def test_sequence_message_planning_is_resource_bounded(messages, message):
    with pytest.raises(SerializationError, match=message):
        serialize_sequence({"participants": ["A"], "messages": messages})


def test_sequence_raw_message_ids_do_not_change_rendered_messages():
    code = serialize_sequence(
        {
            "participants": ["A", "B"],
            "messages": [
                {"id": "call", "source": "A", "target": "B"},
                {"id": "call", "source": "B", "target": "A"},
            ],
        }
    )

    assert code.count("A->>B") == 1
    assert code.count("B->>A") == 1


def test_mindmap_uses_unique_serializer_ids_for_duplicate_logical_ids():
    code = serialize_mindmap(
        {
            "root": {
                "id": "duplicate",
                "label": "Root",
                "children": [
                    {"id": "duplicate", "label": "First"},
                    {"id": "duplicate", "label": "Second"},
                ],
            }
        }
    )

    assert "root((Root))" in code
    assert 'node_2["First"]' in code
    assert 'node_3["Second"]' in code


@pytest.mark.integration
def test_labeled_flowchart_connector_styles_parse_and_render_in_real_mermaid():
    cases = [
        (
            {
                "source": "A",
                "target": "B",
                "label": "Retry",
                "style": "dashed",
            },
            "A -.->|Retry| B",
        ),
        (
            {
                "source": "A",
                "target": "B",
                "label": "Sync",
                "bidirectional": True,
            },
            "A <-->|Sync| B",
        ),
    ]
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        for edge, expected in cases:
            code = serialize_flowchart(
                {
                    "nodes": [
                        {"id": "A", "label": "Start"},
                        {"id": "B", "label": "End"},
                    ],
                    "edges": [edge],
                }
            )
            assert expected in code
            outcome = validator.validate(code, 20)
            assert outcome.runtime.syntax_valid, (code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (
                code,
                outcome.runtime.error,
                outcome.warnings,
            )
    finally:
        process = runtime._process
        runtime.close()
    assert process is not None
    assert process.poll() is not None


@pytest.mark.integration
def test_phase_one_serializers_parse_and_render_in_real_mermaid():
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        for code in CASES:
            outcome = validator.validate(code, 20)
            assert outcome.runtime.syntax_valid, (code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (code, outcome.runtime.error, outcome.warnings)
            if supports_accessibility_directives(outcome.runtime.diagram_type):
                assert "<title" in outcome.runtime.svg
                assert "<desc" in outcome.runtime.svg
    finally:
        process = runtime._process
        runtime.close()
    assert process is not None
    assert process.poll() is not None
