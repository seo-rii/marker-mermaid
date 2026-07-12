from marker_mermaid.accessibility import (
    EXPERIMENTAL_NOTICE,
    augment_accessibility_directives,
    enrich_accessibility_ir,
    resolve_accessibility,
)


def test_accessibility_preserves_explicit_text_and_adds_experimental_notice_once():
    resolved = resolve_accessibility(
        {
            "acc_title": "Payment approval",
            "acc_description": f"Observed payment flow. {EXPERIMENTAL_NOTICE}",
        },
        "flowchart",
        experimental=True,
    )

    assert resolved.title == "Payment approval"
    assert resolved.description.count(EXPERIMENTAL_NOTICE) == 1
    assert not resolved.generated_title
    assert not resolved.generated_description


def test_accessibility_derives_bounded_labels_and_unique_directed_endpoints():
    resolved = resolve_accessibility(
        {
            "nodes": [
                {"id": "request", "label": "Request"},
                {"id": "approve", "label": "Approve"},
                {"id": "done", "label": "Done"},
            ],
            "edges": [
                {"source": "request", "target": "approve"},
                {"source": "approve", "target": "done"},
            ],
        },
        "flowchart",
        experimental=False,
    )

    assert resolved.title == "Flowchart reconstruction"
    assert "Request, Approve, Done" in resolved.description
    assert "starts at Request and ends at Done" in resolved.description
    assert resolved.generated_title and resolved.generated_description


def test_accessibility_chart_summary_uses_observed_labels_without_inventing_trends():
    resolved = resolve_accessibility(
        {"slices": [{"label": "A", "value": 3}, {"label": "B", "value": 7}]},
        "pie",
        experimental=False,
    )

    assert resolved.description == "Pie reconstruction containing A, B."
    assert "increase" not in resolved.description


def test_accessibility_enrichment_preserves_input_and_semantic_requested_type():
    original = {"services": [{"id": "api", "label": "API"}]}

    enriched = enrich_accessibility_ir(original, "c4", experimental=True)

    assert "acc_title" not in original
    assert enriched["acc_title"] == "C4 model reconstruction"
    assert "API" in enriched["acc_description"]


def test_directive_augmentation_is_emitted_grammar_aware():
    augmented = augment_accessibility_directives(
        "flowchart LR\n    A --> B\n",
        "flowchart",
        {"nodes": [{"id": "A"}, {"id": "B"}]},
        semantic_type="bpmn",
        experimental=True,
    )

    assert augmented is not None
    assert "accTitle: BPMN process reconstruction" in augmented
    assert (
        augment_accessibility_directives(
            "mindmap\n    root((Root))\n",
            "mindmap",
            {"root": {"label": "Root"}},
            semantic_type="mindmap",
            experimental=False,
        )
        is None
    )
    assert (
        augment_accessibility_directives(
            "```mermaid\nflowchart LR\nA --> B\n```",
            "flowchart",
            {},
            semantic_type="flowchart",
            experimental=False,
        )
        is None
    )
