from __future__ import annotations

import pytest
from pydantic import ValidationError

from marker_mermaid.review_layout import MoveNodeLayoutOperation, ReviewLayoutHints


def test_layout_move_is_normalized_sorted_and_replaces_same_node():
    layout = ReviewLayoutHints().with_node("B", 0.8, 0.2).with_node("A", 0.1, 0.3)
    moved = layout.with_node("B", 0.4, 0.5)

    assert [item.node_id for item in moved.nodes] == ["A", "B"]
    assert moved.nodes[1].x == 0.4
    assert moved.nodes[1].y == 0.5


@pytest.mark.parametrize("value", [True, False, -0.1, 1.1, float("nan"), float("inf"), "0.5"])
def test_layout_rejects_unsafe_coordinates(value):
    with pytest.raises(ValidationError):
        ReviewLayoutHints().with_node("A", value, 0.5)


def test_layout_reconciliation_drops_deleted_nodes_and_empty_artifact():
    layout = ReviewLayoutHints().with_node("A", 0.1, 0.2).with_node("B", 0.3, 0.4)

    retained = layout.retain_nodes({"B"})

    assert retained is not None
    assert [item.node_id for item in retained.nodes] == ["B"]
    assert retained.retain_nodes(set()) is None


def test_move_operation_is_closed_and_rejects_boolean_coordinates():
    with pytest.raises(ValidationError):
        MoveNodeLayoutOperation.model_validate(
            {
                "operation": "move_node",
                "node_id": "A",
                "position": [0.2, 0.3],
                "url": "https://example.invalid",
            }
        )
    with pytest.raises(ValidationError):
        MoveNodeLayoutOperation.model_validate(
            {"operation": "move_node", "node_id": "A", "position": [True, 0.3]}
        )
