"""Bounded advisory layout hints for the local Review Workspace."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from marker_mermaid.models import MAX_ID_CHARS

LAYOUT_SCHEMA_VERSION = "mmx-review-layout-0.1"
MAX_LAYOUT_NODES = 2_000


class NodeLayoutHint(BaseModel):
    """A user-selected node center in an advisory normalized canvas."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)

    @field_validator("x", "y", mode="before")
    @classmethod
    def coordinate_is_numeric_and_not_boolean(cls, value):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("layout coordinates must be numeric and not boolean")
        return value

    @field_validator("x", "y")
    @classmethod
    def coordinate_is_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("layout coordinates must be finite")
        return value


class ReviewLayoutHints(BaseModel):
    """Revisioned user layout that never overwrites source evidence geometry."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[LAYOUT_SCHEMA_VERSION] = LAYOUT_SCHEMA_VERSION
    coordinate_space: Literal["normalized"] = "normalized"
    nodes: list[NodeLayoutHint] = Field(default_factory=list, max_length=MAX_LAYOUT_NODES)

    @model_validator(mode="after")
    def node_ids_are_unique(self) -> ReviewLayoutHints:
        ids = [item.node_id for item in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("layout node ids must be unique")
        return self

    def with_node(self, node_id: str, x: float, y: float) -> ReviewLayoutHints:
        hint = NodeLayoutHint(node_id=node_id, x=x, y=y)
        nodes = [item for item in self.nodes if item.node_id != node_id]
        nodes.append(hint)
        nodes.sort(key=lambda item: item.node_id)
        return ReviewLayoutHints(nodes=nodes)

    def retain_nodes(self, node_ids: set[str]) -> ReviewLayoutHints | None:
        retained = [item for item in self.nodes if item.node_id in node_ids]
        return ReviewLayoutHints(nodes=retained) if retained else None


class MoveNodeLayoutOperation(BaseModel):
    """Closed HTTP operation for committing one layout hint."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["move_node"]
    node_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    position: tuple[float, float]

    @field_validator("position", mode="before")
    @classmethod
    def position_is_exact_numeric_pair(cls, value):
        if not isinstance(value, list | tuple) or len(value) != 2:
            raise ValueError("layout position must contain exactly two coordinates")
        if any(isinstance(item, bool) or not isinstance(item, int | float) for item in value):
            raise ValueError("layout position coordinates must be numeric and not boolean")
        return value

    @model_validator(mode="after")
    def position_is_normalized(self) -> MoveNodeLayoutOperation:
        NodeLayoutHint(node_id=self.node_id, x=self.position[0], y=self.position[1])
        return self
