"""Conservative, deterministic natural-language patches for the review workspace.

This module deliberately recognizes a small command language.  It does not try to
guess when a reference is spatial or otherwise ambiguous; callers can hand those
commands to a human (or an explicitly configured model) without risking a partial
edit.  Applying a command is transactional across the supplied IR and Mermaid
artifacts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from marker_mermaid.models import ReviewHistoryEntry, VisualEvidence

MAX_COMMAND_LENGTH = 500
MAX_LABEL_LENGTH = 200
MAX_NODE_IDS = 50
MAX_MERMAID_LENGTH = 1_000_000
MAX_IR_JSON_LENGTH = 2_000_000
MAX_REASON_LENGTH = 4096

_ID = r"[A-Za-z][A-Za-z0-9_-]{0,63}"
_ID_RE = re.compile(rf"^{_ID}$")
_SPATIAL_REFERENCE_RE = re.compile(
    r"(?:왼쪽|오른쪽|위쪽|아래쪽|첫\s*번째|두\s*번째|세\s*번째|"
    r"left(?:most)?|right(?:most)?|top|bottom)",
    re.IGNORECASE,
)

_TYPE_ALIASES = {
    "flowchart": "flowchart",
    "flow chart": "flowchart",
    "sequence": "sequence",
    "sequence diagram": "sequence",
    "state": "state",
    "state diagram": "state",
    "class": "class",
    "class diagram": "class",
    "er": "er",
    "er diagram": "er",
    "architecture": "architecture",
    "architecture diagram": "architecture",
    "c4": "c4",
    "requirement": "requirement",
    "mindmap": "mindmap",
    "timeline": "timeline",
    "gantt": "gantt",
    "kanban": "kanban",
    "pie": "pie",
    "xychart": "xychart",
    "xy chart": "xychart",
    "quadrant": "quadrant",
    "sankey": "sankey",
    "radar": "radar",
    "treemap": "treemap",
    "venn": "venn",
    "packet": "packet",
    "ishikawa": "ishikawa",
    "swimlane": "swimlane",
    "bpmn": "bpmn",
}


class ReviewCommandError(ValueError):
    """A command could not be parsed or safely applied."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ParsedReviewCommand(BaseModel):
    """A bounded command intent produced without consulting document state."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal[
        "reverse_edge",
        "relabel_node",
        "relabel_node_from_evidence",
        "group_nodes",
        "delete_group",
        "change_diagram_type",
        "add_node",
        "add_edge",
        "delete_edge",
        "delete_node",
        "reconnect_edge",
    ]
    edge_id: str | None = None
    group_id: str | None = None
    source_id: str | None = None
    target_id: str | None = None
    node_id: str | None = None
    node_ordinal: int | None = None
    node_ids: list[str] = Field(default_factory=list)
    label: str | None = None
    diagram_type: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    evidence_id: str | None = None


class AddNodeOperation(BaseModel):
    """Add one source-anchored node with server-created user evidence."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["add_node"]
    node_id: str
    label: str
    bbox: tuple[float, float, float, float]

    @field_validator("bbox", mode="before")
    @classmethod
    def bbox_rejects_booleans(cls, value):
        if (
            not isinstance(value, list | tuple)
            or len(value) != 4
            or any(isinstance(item, bool) for item in value)
        ):
            raise ValueError("bbox must contain four numeric coordinates")
        return value


class DeleteNodeOperation(BaseModel):
    """Delete one explicit flowchart node and all of its incident edges."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["delete_node"]
    node_id: str


class ReconnectEdgeOperation(BaseModel):
    """Reconnect one Scene relation selected by its stable relation id."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["reconnect_edge"]
    edge_id: str
    source_id: str
    target_id: str


class GroupNodesOperation(BaseModel):
    """Place explicit Scene nodes in one deterministic Mermaid subgraph."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["group_nodes"]
    node_ids: list[str] = Field(min_length=2, max_length=MAX_NODE_IDS)
    label: str

    @field_validator("node_ids")
    @classmethod
    def node_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("group node ids must be unique")
        return value


class AddEdgeOperation(BaseModel):
    """Add one user-confirmed plain directed relation between explicit nodes."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["add_edge"]
    source_id: str
    target_id: str


class DeleteEdgeOperation(BaseModel):
    """Delete one explicit Scene relation and its unique plain Mermaid edge."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["delete_edge"]
    edge_id: str


class DeleteGroupOperation(BaseModel):
    """Delete one exact Scene group and matching flat Mermaid subgraph."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["delete_group"]
    group_id: str


class RelabelNodeFromEvidenceOperation(BaseModel):
    """Relabel one node from an already-linked OCR or vector-text observation."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["relabel_node_from_evidence"]
    node_id: str
    evidence_id: str = Field(min_length=1, max_length=256)


StructuredReviewOperation = Annotated[
    AddNodeOperation
    | DeleteNodeOperation
    | ReconnectEdgeOperation
    | GroupNodesOperation
    | AddEdgeOperation
    | DeleteEdgeOperation
    | DeleteGroupOperation
    | RelabelNodeFromEvidenceOperation,
    Field(discriminator="operation"),
]
_STRUCTURED_OPERATION_ADAPTER = TypeAdapter(StructuredReviewOperation)


class ReviewCommandResult(BaseModel):
    """Transactional command result suitable for review workspace persistence."""

    applied: bool
    ir: dict[str, Any] | None = None
    mermaid_code: str | None = None
    provenance: list[dict[str, Any]] | None = None
    provenance_changed: bool = False
    history_entry: ReviewHistoryEntry | None = None
    error_code: str | None = None
    message: str
    regeneration_required: bool = False


def _clean_command(command: str) -> str:
    if not isinstance(command, str):
        raise ReviewCommandError("invalid_command", "command must be a string")
    cleaned = " ".join(command.strip().split())
    if not cleaned:
        raise ReviewCommandError("invalid_command", "command cannot be empty")
    if len(cleaned) > MAX_COMMAND_LENGTH:
        raise ReviewCommandError("input_too_large", "command exceeds the length limit")
    if any(ord(character) < 32 for character in cleaned):
        raise ReviewCommandError("invalid_command", "command contains control characters")
    return cleaned


def _validated_id(value: str) -> str:
    if not _ID_RE.fullmatch(value):
        raise ReviewCommandError(
            "invalid_identifier", f"unsafe or unsupported identifier: {value!r}"
        )
    return value


def _validated_label(value: str) -> str:
    label = value.strip().strip(".。")
    label = re.sub(r"(?:이야|야|입니다|이다)$", "", label).strip()
    if not label:
        raise ReviewCommandError("invalid_label", "label cannot be empty")
    if len(label) > MAX_LABEL_LENGTH or "\n" in label or "\r" in label:
        raise ReviewCommandError("invalid_label", "label exceeds the safe single-line limit")
    if any(ord(character) < 32 for character in label):
        raise ReviewCommandError("invalid_label", "label contains control characters")
    return label


def _validated_evidence_label(value: str | None) -> str:
    """Validate an observed label without rewriting its linguistic content."""

    if not isinstance(value, str):
        raise ReviewCommandError("invalid_label", "selected evidence has no text label")
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for character in value
    ):
        raise ReviewCommandError(
            "invalid_label", "selected evidence text must be a safe single-line label"
        )
    label = value.strip()
    if not label:
        raise ReviewCommandError("invalid_label", "selected evidence text is empty")
    if len(label) > MAX_LABEL_LENGTH:
        raise ReviewCommandError(
            "invalid_label", "selected evidence text exceeds the label length limit"
        )
    return label


def _normalize_type(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().lower().replace("다이어그램", "diagram"))
    normalized = normalized.removesuffix(" diagram diagram")
    result = _TYPE_ALIASES.get(normalized)
    if result is None:
        raise ReviewCommandError("unsupported_diagram_type", f"unsupported diagram type: {value!r}")
    return result


def _validate_ir_input(ir: Mapping[str, Any]) -> None:
    try:
        encoded_ir = json.dumps(ir, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ReviewCommandError("unsupported_ir", "IR must be strict JSON serializable") from error
    if len(encoded_ir) > MAX_IR_JSON_LENGTH:
        raise ReviewCommandError("input_too_large", "IR exceeds the safe edit limit")


def _parse_id_list(value: str) -> list[str]:
    normalized = re.sub(r"\s*(?:와|과|and)\s*", ",", value, flags=re.IGNORECASE)
    ids = [_validated_id(item.strip()) for item in normalized.split(",") if item.strip()]
    if len(ids) < 2:
        raise ReviewCommandError(
            "ambiguous_reference", "group commands require at least two node ids"
        )
    if len(ids) > MAX_NODE_IDS:
        raise ReviewCommandError("input_too_large", "too many nodes in one group command")
    if len(set(ids)) != len(ids):
        raise ReviewCommandError("ambiguous_reference", "group node ids must be unique")
    return ids


def parse_review_command(command: str) -> ParsedReviewCommand:
    """Parse the supported Korean/English command subset.

    Parsing alone does not prove that referenced ids exist.  That validation happens
    atomically in :func:`apply_review_command`.
    """

    text = _clean_command(command)

    match = re.fullmatch(
        rf"(?P<src>{_ID})에서\s+(?P<dst>{_ID})(?:으)?로\s+가는\s+화살표를?\s+"
        r"반대로(?:\s+바꿔(?:\s*줘)?|\s+뒤집어(?:\s*줘)?)?[.!。]?",
        text,
        re.IGNORECASE,
    )
    if match:
        return ParsedReviewCommand(
            operation="reverse_edge",
            source_id=_validated_id(match["src"]),
            target_id=_validated_id(match["dst"]),
        )

    match = re.fullmatch(
        rf"(?:please\s+)?reverse(?:\s+the)?\s+(?:edge|arrow)\s+"
        rf"(?P<src>{_ID})\s*(?:->|to)\s*(?P<dst>{_ID})[.!]?",
        text,
        re.IGNORECASE,
    )
    if match:
        return ParsedReviewCommand(
            operation="reverse_edge",
            source_id=_validated_id(match["src"]),
            target_id=_validated_id(match["dst"]),
        )

    match = re.fullmatch(
        rf"(?P<id>{_ID})\s*(?:노드|node)의?\s*(?:라벨|label)(?:을|를)?\s+"
        r"(?P<label>.+?)(?:으)?로\s+(?:바꿔(?:\s*줘)?|변경해(?:\s*줘)?)[.!。]?",
        text,
        re.IGNORECASE,
    )
    if match:
        return ParsedReviewCommand(
            operation="relabel_node",
            node_id=_validated_id(match["id"]),
            label=_validated_label(match["label"]),
        )

    match = re.fullmatch(
        rf"(?:please\s+)?(?:rename|relabel)\s+(?:node\s+)?(?P<id>{_ID})\s+to\s+"
        r"(?P<label>.+?)[.!]?",
        text,
        re.IGNORECASE,
    )
    if match:
        return ParsedReviewCommand(
            operation="relabel_node",
            node_id=_validated_id(match["id"]),
            label=_validated_label(match["label"]),
        )

    match = re.fullmatch(
        r"(?:이\s*영역은\s*)?(?:일반\s+)?[A-Za-z][A-Za-z0-9 _-]*?\s*(?:가|이)?\s*"
        r"아니라\s+(?P<type>[A-Za-z][A-Za-z0-9 _-]*?)(?:이야|야|입니다|이다)?[.!。]?",
        text,
        re.IGNORECASE,
    )
    if match:
        return ParsedReviewCommand(
            operation="change_diagram_type",
            diagram_type=_normalize_type(match["type"]),
        )

    match = re.fullmatch(
        r"(?:please\s+)?change(?:\s+the)?\s+diagram\s+type\s+to\s+"
        r"(?P<type>[A-Za-z][A-Za-z0-9 _-]*?)[.!]?",
        text,
        re.IGNORECASE,
    )
    if match:
        return ParsedReviewCommand(
            operation="change_diagram_type",
            diagram_type=_normalize_type(match["type"]),
        )

    match = re.fullmatch(
        rf"(?P<ids>{_ID}(?:\s*,\s*{_ID})+)(?:\s*노드(?:들)?(?:을|를)?|\s+nodes?)\s+"
        r"(?:하나의\s+)?(?:subgraph|그룹)(?:으)?로\s+묶어(?:\s*줘)?[.!。]?",
        text,
        re.IGNORECASE,
    )
    if match:
        return ParsedReviewCommand(operation="group_nodes", node_ids=_parse_id_list(match["ids"]))

    match = re.fullmatch(
        rf"(?:please\s+)?group\s+nodes?\s+(?P<ids>{_ID}(?:\s*,\s*{_ID})+)"
        r"(?:\s+as\s+(?P<label>.+?))?[.!]?",
        text,
        re.IGNORECASE,
    )
    if match:
        return ParsedReviewCommand(
            operation="group_nodes",
            node_ids=_parse_id_list(match["ids"]),
            label=_validated_label(match["label"]) if match["label"] else None,
        )

    if _SPATIAL_REFERENCE_RE.search(text) or re.search(r"\b(?:it|this|that|them)\b", text, re.I):
        raise ReviewCommandError(
            "ambiguous_reference", "spatial, ordinal, and pronoun references require manual review"
        )
    raise ReviewCommandError("unsupported_command", "command is outside the supported safe subset")


def _node_container(ir: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    for key in ("elements", "nodes"):
        value = ir.get(key)
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value, key
    raise ReviewCommandError("unsupported_ir", "IR must contain an elements or nodes list")


def _relation_container(ir: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("relations", "edges"):
        value = ir.get(key)
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
    raise ReviewCommandError("unsupported_ir", "IR must contain a relations or edges list")


def _node_ids(ir: dict[str, Any]) -> list[str]:
    nodes, _ = _node_container(ir)
    result = [node.get("id") for node in nodes]
    if not all(isinstance(node_id, str) and _ID_RE.fullmatch(node_id) for node_id in result):
        raise ReviewCommandError(
            "unsupported_ir", "IR node ids must use the safe identifier subset"
        )
    if len(result) != len(set(result)):
        raise ReviewCommandError("ambiguous_reference", "IR node ids must be unique")
    return result


def _edge_keys(edge: Mapping[str, Any]) -> tuple[str, str]:
    for source_key, target_key in (
        ("source_id", "target_id"),
        ("source", "target"),
        ("from", "to"),
    ):
        if source_key in edge and target_key in edge:
            return source_key, target_key
    raise ReviewCommandError("unsupported_ir", "edge has no recognized endpoint fields")


def _group_id(node_ids: list[str]) -> str:
    readable = "group_" + "_".join(node_ids)
    if _ID_RE.fullmatch(readable):
        return readable
    digest = hashlib.sha256("\0".join(node_ids).encode()).hexdigest()[:20]
    return f"group_{digest}"


def _is_finite_ordered_bbox(value: Any) -> bool:
    return (
        isinstance(value, list | tuple)
        and len(value) == 4
        and all(
            not isinstance(item, bool)
            and isinstance(item, int | float)
            and math.isfinite(item)
            for item in value
        )
        and value[2] > value[0]
        and value[3] > value[1]
    )


def _scene_canvas_bounds(ir: Mapping[str, Any]) -> tuple[float, float]:
    if ir.get("coordinate_space", "pixels") == "normalized":
        return 1.0, 1.0
    canvas_size = ir.get("canvas_size")
    if (
        not isinstance(canvas_size, list | tuple)
        or len(canvas_size) != 2
        or any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value <= 0
            for value in canvas_size
        )
    ):
        raise ReviewCommandError(
            "unsupported_ir", "grouping requires an explicit finite Scene canvas"
        )
    return float(canvas_size[0]), float(canvas_size[1])


def _validated_scene_groups(
    ir: Mapping[str, Any], node_ids: set[str]
) -> dict[str, tuple[str, ...]]:
    groups = ir.get("groups", [])
    if not isinstance(groups, list) or not all(isinstance(item, dict) for item in groups):
        raise ReviewCommandError("unsupported_ir", "IR groups must be a list")
    result: dict[str, tuple[str, ...]] = {}
    claimed: set[str] = set()
    width, height = _scene_canvas_bounds(ir)
    nodes, _ = _node_container(dict(ir))
    node_by_id = {node.get("id"): node for node in nodes}
    for group in groups:
        group_id = group.get("id")
        members = group.get("member_ids")
        if not isinstance(group_id, str) or not _ID_RE.fullmatch(group_id):
            raise ReviewCommandError("unsupported_ir", "IR group ids must be safe and explicit")
        if group_id in node_ids:
            raise ReviewCommandError(
                "ambiguous_reference", "IR group ids cannot collide with node ids"
            )
        if group_id in result:
            raise ReviewCommandError("ambiguous_reference", "IR group ids must be unique")
        if (
            not isinstance(members, list)
            or not members
            or not all(isinstance(item, str) and item in node_ids for item in members)
            or len(members) != len(set(members))
        ):
            raise ReviewCommandError(
                "unsupported_ir", "IR group members must be unique existing node ids"
            )
        overlap = claimed.intersection(members)
        if overlap:
            raise ReviewCommandError(
                "ambiguous_reference", "IR nodes cannot belong to multiple groups"
            )
        claimed.update(members)
        boxes = [node_by_id[member].get("bbox") for member in members]
        if not all(
            _is_finite_ordered_bbox(box)
            and box[0] >= 0
            and box[1] >= 0
            and box[2] <= width
            and box[3] <= height
            for box in boxes
        ):
            raise ReviewCommandError(
                "unsupported_ir", "existing group members require bounded finite bbox evidence"
            )
        expected_bbox = [
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        ]
        if not _is_finite_ordered_bbox(group.get("bbox")) or list(group["bbox"]) != expected_bbox:
            raise ReviewCommandError(
                "unsupported_ir", "existing group bbox must equal its member bbox union"
            )
        result[group_id] = tuple(sorted(members))
    return result


_SUBGRAPH_HEADER_RE = re.compile(
    rf'^\s*subgraph\s+(?P<id>{_ID})(?:\s*\[\s*"(?:[^"\\]|\\.)*"\s*\])?\s*$'
)


def _flat_mermaid_subgraphs(
    code: str,
) -> dict[str, tuple[tuple[str, ...], int, int]]:
    bare_member = re.compile(rf"^\s*(?P<id>{_ID})\s*$")
    result: dict[str, tuple[tuple[str, ...], int, int]] = {}
    active_id: str | None = None
    start_line = -1
    members: list[str] = []
    claimed: set[str] = set()
    for index, line in enumerate(code.splitlines(keepends=True)):
        raw_line = line.rstrip("\r\n")
        match = _SUBGRAPH_HEADER_RE.fullmatch(raw_line)
        if match:
            if active_id is not None:
                raise ReviewCommandError(
                    "unsupported_mermaid", "nested Mermaid subgraphs are not safely editable"
                )
            active_id = match["id"]
            start_line = index
            members = []
            continue
        if raw_line.strip() == "end":
            if active_id is None:
                raise ReviewCommandError(
                    "unsupported_mermaid", "orphan Mermaid subgraph end is not safely editable"
                )
            if active_id in result or not members or len(members) != len(set(members)):
                raise ReviewCommandError(
                    "ambiguous_reference", "Mermaid subgraph ids and members must be unique"
                )
            overlap = claimed.intersection(members)
            if overlap:
                raise ReviewCommandError(
                    "ambiguous_reference", "Mermaid nodes cannot belong to multiple subgraphs"
                )
            claimed.update(members)
            result[active_id] = (tuple(sorted(members)), start_line, index)
            active_id = None
            start_line = -1
            members = []
            continue
        if active_id is not None:
            member = bare_member.fullmatch(raw_line)
            if member is None:
                raise ReviewCommandError(
                    "unsupported_mermaid",
                    "existing subgraphs must contain only explicit bare node memberships",
                )
            members.append(member["id"])
    if active_id is not None:
        raise ReviewCommandError("unsupported_mermaid", "Mermaid subgraph is not closed")
    return result


def _mermaid_subgraph_memberships(code: str) -> dict[str, tuple[str, ...]]:
    return {
        group_id: record[0]
        for group_id, record in _flat_mermaid_subgraphs(code).items()
    }


def _quoted_rectangle_declaration_counts(
    code: str, node_ids: set[str]
) -> Counter[str]:
    declaration = re.compile(
        rf'^\s*(?P<id>{_ID})\s*\[\s*"(?:[^"\\]|\\.)*"\s*\]\s*$'
    )
    counts: Counter[str] = Counter()
    for line in code.splitlines():
        match = declaration.fullmatch(line)
        if match and match["id"] in node_ids:
            counts[match["id"]] += 1
    return counts


def _apply_ir(
    intent: ParsedReviewCommand, ir: dict[str, Any]
) -> tuple[dict[str, Any], str, dict, dict]:
    ids = _node_ids(ir)
    if intent.operation == "add_node":
        assert intent.node_id and intent.label is not None and intent.evidence_id
        if intent.node_id in ids:
            raise ReviewCommandError("ambiguous_reference", "node id already exists in the IR")
        nodes, key = _node_container(ir)
        if key != "elements":
            raise ReviewCommandError(
                "unsupported_ir", "source-anchored insertion requires Scene IR elements"
            )
        bbox = intent.bbox
        if bbox is None or not all(math.isfinite(value) for value in bbox):
            raise ReviewCommandError("invalid_bbox", "bbox must contain four finite numbers")
        x0, y0, x1, y1 = bbox
        if x1 <= x0 or y1 <= y0:
            raise ReviewCommandError("invalid_bbox", "bbox must have positive ordered area")
        coordinate_space = ir.get("coordinate_space", "pixels")
        if coordinate_space == "normalized":
            width = height = 1.0
        else:
            canvas_size = ir.get("canvas_size")
            if (
                not isinstance(canvas_size, list | tuple)
                or len(canvas_size) != 2
                or not all(isinstance(value, int | float) for value in canvas_size)
                or any(isinstance(value, bool) for value in canvas_size)
                or not all(math.isfinite(value) and value > 0 for value in canvas_size)
            ):
                raise ReviewCommandError(
                    "unsupported_ir", "source-anchored insertion requires Scene canvas_size"
                )
            width, height = canvas_size
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            raise ReviewCommandError("invalid_bbox", "bbox must remain inside the Scene canvas")
        node = {
            "id": intent.node_id,
            "role": "user_node",
            "text": intent.label,
            "bbox": list(bbox),
            "confidence": 1.0,
            "evidence_ids": [intent.evidence_id],
        }
        nodes.append(node)
        return ir, intent.node_id, {}, node

    if intent.operation == "add_edge":
        assert intent.edge_id and intent.source_id and intent.target_id and intent.evidence_id
        if intent.source_id == intent.target_id:
            raise ReviewCommandError("invalid_edge", "self-loop edge addition is not supported")
        if intent.source_id not in ids or intent.target_id not in ids:
            raise ReviewCommandError(
                "unresolved_reference", "new edge endpoint does not exist in the IR"
            )
        relations = _relation_container(ir)
        relation_ids = [relation.get("id") for relation in relations]
        if (
            not all(isinstance(relation_id, str) for relation_id in relation_ids)
            or intent.edge_id in relation_ids
            or len(relation_ids) != len(set(relation_ids))
        ):
            raise ReviewCommandError(
                "ambiguous_reference", "new edge id collides with an existing IR relation"
            )
        for relation in relations:
            source_key, target_key = _edge_keys(relation)
            if (
                relation.get(source_key) == intent.source_id
                and relation.get(target_key) == intent.target_id
            ):
                raise ReviewCommandError(
                    "ambiguous_reference", "parallel directed edges are not safely editable"
                )
        relation = {
            "id": intent.edge_id,
            "source_id": intent.source_id,
            "target_id": intent.target_id,
            "relation_type": "user_edge",
            "semantic_relation": "unknown",
            "label": None,
            "polyline": [],
            "arrow_at_start": False,
            "arrow_at_end": True,
            "confidence": 1.0,
            "evidence_ids": [intent.evidence_id],
        }
        relations.append(relation)
        return ir, intent.edge_id, {}, relation

    if intent.operation == "delete_edge":
        assert intent.edge_id
        relations = _relation_container(ir)
        matches = [relation for relation in relations if relation.get("id") == intent.edge_id]
        if len(matches) != 1:
            raise ReviewCommandError(
                "unresolved_reference", "edge id does not identify exactly one IR relation"
            )
        relation = matches[0]
        source_key, target_key = _edge_keys(relation)
        source = relation.get(source_key)
        target = relation.get(target_key)
        if not isinstance(source, str) or not isinstance(target, str):
            raise ReviewCommandError("unsupported_ir", "deleted edge endpoints must be explicit")
        parallel = []
        for candidate in relations:
            candidate_source, candidate_target = _edge_keys(candidate)
            if (
                candidate.get(candidate_source) == source
                and candidate.get(candidate_target) == target
            ):
                parallel.append(candidate)
        if len(parallel) != 1:
            raise ReviewCommandError(
                "ambiguous_reference", "parallel directed edges cannot be deleted safely"
            )
        before = deepcopy(relation)
        relations.remove(relation)
        return ir, intent.edge_id, before, {"deleted": intent.edge_id}

    if intent.operation == "reconnect_edge":
        assert intent.edge_id and intent.source_id and intent.target_id
        if intent.source_id == intent.target_id:
            raise ReviewCommandError("invalid_edge", "self-loop reconnection is not supported")
        if intent.source_id not in ids or intent.target_id not in ids:
            raise ReviewCommandError(
                "unresolved_reference", "new edge endpoint does not exist in the IR"
            )
        matches = [edge for edge in _relation_container(ir) if edge.get("id") == intent.edge_id]
        if len(matches) != 1:
            raise ReviewCommandError(
                "unresolved_reference", "edge id does not identify exactly one IR relation"
            )
        edge = matches[0]
        source_key, target_key = _edge_keys(edge)
        before = {"source": edge.get(source_key), "target": edge.get(target_key)}
        if before == {"source": intent.source_id, "target": intent.target_id}:
            raise ReviewCommandError("no_change", "edge already has the requested endpoints")
        edge[source_key] = intent.source_id
        edge[target_key] = intent.target_id
        return (
            ir,
            intent.edge_id,
            before,
            {"source": intent.source_id, "target": intent.target_id},
        )

    if intent.operation == "delete_node":
        assert intent.node_id
        nodes, _ = _node_container(ir)
        matches = [node for node in nodes if node.get("id") == intent.node_id]
        if len(matches) != 1:
            raise ReviewCommandError("unresolved_reference", "node id does not exist in the IR")
        groups = ir.get("groups", [])
        if not isinstance(groups, list) or not all(isinstance(group, dict) for group in groups):
            raise ReviewCommandError("unsupported_ir", "IR groups must be a list")
        member_lists = [group.get("member_ids") for group in groups]
        if not all(isinstance(members, list) for members in member_lists):
            raise ReviewCommandError("unsupported_ir", "IR group members must be lists")
        if any(intent.node_id in members for members in member_lists):
            raise ReviewCommandError(
                "unsupported_ir", "grouped nodes require an explicit group edit before deletion"
            )
        relations = _relation_container(ir)
        removed_relations: list[dict[str, Any]] = []
        retained_relations: list[dict[str, Any]] = []
        for edge in relations:
            source_key, target_key = _edge_keys(edge)
            if intent.node_id in {edge.get(source_key), edge.get(target_key)}:
                removed_relations.append(deepcopy(edge))
            else:
                retained_relations.append(edge)
        relations[:] = retained_relations
        removed_node = deepcopy(matches[0])
        nodes.remove(matches[0])
        before = {
            "node": removed_node,
            "relations": removed_relations,
        }
        return ir, intent.node_id, before, {"deleted": intent.node_id}

    if intent.operation == "reverse_edge":
        assert intent.source_id and intent.target_id
        if intent.source_id not in ids or intent.target_id not in ids:
            raise ReviewCommandError(
                "unresolved_reference", "edge endpoint does not exist in the IR"
            )
        matches: list[tuple[dict[str, Any], str, str]] = []
        for edge in _relation_container(ir):
            source_key, target_key = _edge_keys(edge)
            if (
                edge.get(source_key) == intent.source_id
                and edge.get(target_key) == intent.target_id
            ):
                matches.append((edge, source_key, target_key))
        if len(matches) != 1:
            code = "unresolved_reference" if not matches else "ambiguous_reference"
            raise ReviewCommandError(code, "reverse edge command must identify exactly one edge")
        edge, source_key, target_key = matches[0]
        before = {"source": edge[source_key], "target": edge[target_key]}
        edge[source_key], edge[target_key] = edge[target_key], edge[source_key]
        after = {"source": edge[source_key], "target": edge[target_key]}
        target = str(edge.get("id") or f"{intent.source_id}->{intent.target_id}")
        return ir, target, before, after

    if intent.operation in {"relabel_node", "relabel_node_from_evidence"}:
        assert intent.node_id and intent.label is not None
        matches = [node for node in _node_container(ir)[0] if node.get("id") == intent.node_id]
        if len(matches) != 1:
            raise ReviewCommandError("unresolved_reference", "node id does not exist in the IR")
        node = matches[0]
        label_key = next((key for key in ("text", "label", "name") if key in node), "label")
        before = {label_key: node.get(label_key)}
        if (
            intent.operation == "relabel_node_from_evidence"
            and before[label_key] == intent.label
        ):
            raise ReviewCommandError("no_change", "node already has the selected evidence label")
        node[label_key] = intent.label
        after = {label_key: intent.label}
        if intent.operation == "relabel_node_from_evidence":
            assert intent.evidence_id
            after["evidence_id"] = intent.evidence_id
        return ir, intent.node_id, before, after

    if intent.operation == "delete_group":
        assert intent.group_id
        groups = ir.get("groups", [])
        if not isinstance(groups, list) or not all(isinstance(item, dict) for item in groups):
            raise ReviewCommandError("unsupported_ir", "IR groups must be a list")
        matches = [group for group in groups if group.get("id") == intent.group_id]
        if len(matches) != 1:
            raise ReviewCommandError(
                "unresolved_reference", "group id does not identify exactly one Scene group"
            )
        before = deepcopy(matches[0])
        groups.remove(matches[0])
        return ir, intent.group_id, before, {"deleted": intent.group_id}

    if intent.operation == "group_nodes":
        selected = set(intent.node_ids)
        missing = selected - set(ids)
        if missing:
            raise ReviewCommandError(
                "unresolved_reference", f"group references missing node ids: {sorted(missing)}"
            )
        intent.node_ids = [node_id for node_id in ids if node_id in selected]
        group_id = _group_id(intent.node_ids)
        existing_groups = _validated_scene_groups(ir, set(ids))
        if group_id in set(ids) or group_id in existing_groups:
            raise ReviewCommandError("ambiguous_reference", f"group id already exists: {group_id}")
        claimed = {member for members in existing_groups.values() for member in members}
        if selected.intersection(claimed):
            raise ReviewCommandError(
                "ambiguous_reference", "a selected node already belongs to an IR group"
            )
        selected_nodes = [
            node for node in _node_container(ir)[0] if node.get("id") in intent.node_ids
        ]
        boxes = [node.get("bbox") for node in selected_nodes]
        if len(boxes) != len(intent.node_ids) or not all(
            _is_finite_ordered_bbox(box) for box in boxes
        ):
            raise ReviewCommandError(
                "unsupported_ir", "group members require explicit four-number bbox evidence"
            )
        width, height = _scene_canvas_bounds(ir)
        if any(
            box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height for box in boxes
        ):
            raise ReviewCommandError(
                "unsupported_ir", "group member bbox must remain inside the Scene canvas"
            )
        group_bbox = [
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        ]
        group = {
            "id": group_id,
            "role": "subgraph",
            "label": intent.label,
            "bbox": group_bbox,
            "member_ids": list(intent.node_ids),
        }
        ir.setdefault("groups", []).append(group)
        return ir, group_id, {"existing_groups": existing_groups}, group

    assert intent.operation == "change_diagram_type" and intent.diagram_type
    before_type = ir.get("diagram_type")
    ir["diagram_type"] = intent.diagram_type
    candidates = ir.get("diagram_type_candidates")
    if isinstance(candidates, list):
        ir["diagram_type_candidates"] = [
            intent.diagram_type,
            *[item for item in candidates if item != intent.diagram_type],
        ]
    return ir, "diagram", {"diagram_type": before_type}, {"diagram_type": intent.diagram_type}


def _mermaid_node_ids(code: str) -> list[str]:
    ids: list[str] = []
    for match in re.finditer(rf"(?m)^\s*(?P<id>{_ID})(?=\s*(?:\[|\(|\{{))", code):
        if match["id"] not in ids:
            ids.append(match["id"])
    edge_pattern = rf"(?m)\b(?P<src>{_ID})\s*(?:-->|---|==>|-\.->)\s*(?P<dst>{_ID})\b"
    for match in re.finditer(edge_pattern, code):
        for key in ("src", "dst"):
            if match[key] not in ids:
                ids.append(match[key])
    return ids


_PLAIN_EDGE_RE = re.compile(
    rf"^(?P<indent>\s*)(?P<src>{_ID})\s*(?P<arrow>-->|---|==>|-\.->)\s*"
    rf"(?P<dst>{_ID})(?P<tail>\s*)(?P<newline>\r?\n)?$"
)


def _plain_mermaid_edge_counter(code: str) -> Counter[tuple[str, str]]:
    counter: Counter[tuple[str, str]] = Counter()
    quoted_declaration = re.compile(
        rf'^\s*{_ID}\s*\[\s*"(?:[^"\\]|\\.)*"\s*\]\s*$'
    )
    edge_signal = re.compile(r"(?:--|==|-\.|~~~|<--|--[ox])")
    for line in code.splitlines(keepends=True):
        match = _PLAIN_EDGE_RE.fullmatch(line)
        if match:
            counter[(match["src"], match["dst"])] += 1
            continue
        raw_line = line.rstrip("\r\n")
        stripped = raw_line.strip()
        if (
            not stripped
            or stripped.startswith("%%")
            or stripped.startswith(("accTitle:", "accDescr:"))
            or re.fullmatch(
                rf'subgraph\s+{_ID}(?:\s*\[\s*"(?:[^"\\]|\\.)*"\s*\])?',
                stripped,
            )
            or stripped == "end"
            or quoted_declaration.fullmatch(raw_line)
        ):
            continue
        if edge_signal.search(raw_line):
            raise ReviewCommandError(
                "unsupported_mermaid", "non-plain Mermaid edges are not safely editable"
            )
    return counter


def _apply_mermaid(
    intent: ParsedReviewCommand,
    code: str,
    *,
    before: Mapping[str, Any] | None = None,
) -> str:
    if len(code) > MAX_MERMAID_LENGTH:
        raise ReviewCommandError("input_too_large", "Mermaid source exceeds the safe edit limit")
    if not re.match(r"^\s*(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\b", code):
        raise ReviewCommandError(
            "unsupported_mermaid", "only flowchart Mermaid can be patched safely"
        )
    ids = _mermaid_node_ids(code)

    if intent.operation == "change_diagram_type":
        # The IR is changed and must be serialized again; changing only the header
        # would create invalid cross-dialect Mermaid.
        return code

    if intent.operation == "reverse_edge":
        assert intent.source_id and intent.target_id
        edge_re = re.compile(
            rf"(?m)^(?P<indent>\s*){re.escape(intent.source_id)}\s*"
            rf"(?P<arrow>-->|---|==>|-\.->)\s*{re.escape(intent.target_id)}(?P<tail>\s*)$"
        )
        matches = list(edge_re.finditer(code))
        if len(matches) != 1:
            error = "unresolved_reference" if not matches else "ambiguous_reference"
            raise ReviewCommandError(
                error, "Mermaid edge command must identify exactly one plain edge"
            )
        match = matches[0]
        replacement = (
            f"{match['indent']}{intent.target_id} {match['arrow']} "
            f"{intent.source_id}{match['tail']}"
        )
        return code[: match.start()] + replacement + code[match.end() :]

    if intent.operation == "add_node":
        assert intent.node_id and intent.label is not None
        if intent.node_id in ids or re.search(rf"\b{re.escape(intent.node_id)}\b", code):
            raise ReviewCommandError(
                "ambiguous_reference", "node id already appears in Mermaid source"
            )
        escaped = intent.label.replace("\\", "\\\\").replace('"', '\\"')
        return code.rstrip("\n") + f'\n    {intent.node_id}["{escaped}"]\n'

    if intent.operation in {"relabel_node", "relabel_node_from_evidence"}:
        assert intent.node_id and intent.label is not None
        declaration = re.compile(
            rf'(?m)^(?P<indent>\s*){re.escape(intent.node_id)}\s*\[\s*"(?P<label>(?:[^"\\]|\\.)*)"\s*\](?P<tail>\s*)$'
        )
        matches = list(declaration.finditer(code))
        if len(matches) != 1:
            error = "unresolved_reference" if not matches else "ambiguous_reference"
            raise ReviewCommandError(
                error, "node must have exactly one quoted flowchart declaration"
            )
        match = matches[0]
        escaped = intent.label.replace("\\", "\\\\").replace('"', '\\"')
        replacement = f'{match["indent"]}{intent.node_id}["{escaped}"]{match["tail"]}'
        return code[: match.start()] + replacement + code[match.end() :]

    if intent.operation == "reconnect_edge":
        assert intent.source_id and intent.target_id
        old_source = before.get("source") if before is not None else None
        old_target = before.get("target") if before is not None else None
        if not isinstance(old_source, str) or not isinstance(old_target, str):
            raise ReviewCommandError(
                "unsupported_artifact", "edge reconnection requires matching Scene IR state"
            )
        matches: list[re.Match[str]] = []
        for line in code.splitlines(keepends=True):
            match = _PLAIN_EDGE_RE.fullmatch(line)
            if match and match["src"] == old_source and match["dst"] == old_target:
                matches.append(match)
        if len(matches) != 1:
            error = "unresolved_reference" if not matches else "ambiguous_reference"
            raise ReviewCommandError(
                error, "Scene relation must map to exactly one plain Mermaid edge"
            )
        match = matches[0]
        replacement = (
            f"{match['indent']}{intent.source_id} {match['arrow']} {intent.target_id}"
            f"{match['tail']}{match['newline'] or ''}"
        )
        # ``match`` belongs to an individual line, so locate that exact line once in
        # the full source after uniqueness was established structurally.
        old_line = match.group(0)
        if code.count(old_line) != 1:
            raise ReviewCommandError(
                "ambiguous_reference", "Mermaid edge text is not uniquely addressable"
            )
        return code.replace(old_line, replacement, 1)

    if intent.operation == "add_edge":
        assert intent.source_id and intent.target_id
        declaration_counts = _quoted_rectangle_declaration_counts(
            code, {intent.source_id, intent.target_id}
        )
        endpoints = (intent.source_id, intent.target_id)
        if any(declaration_counts[node_id] != 1 for node_id in endpoints):
            raise ReviewCommandError(
                "unresolved_reference",
                "new edge endpoints require one quoted rectangle Mermaid declaration",
            )
        return code.rstrip("\n") + f"\n    {intent.source_id} --> {intent.target_id}\n"

    if intent.operation == "delete_edge":
        if before is None:
            raise ReviewCommandError(
                "unsupported_artifact", "edge deletion requires matching Scene IR state"
            )
        source = before.get("source_id")
        target = before.get("target_id")
        if not isinstance(source, str) or not isinstance(target, str):
            raise ReviewCommandError(
                "unsupported_artifact", "edge deletion requires explicit Scene endpoints"
            )
        quoted_node = re.compile(
            rf'^\s*{_ID}\s*\[\s*"(?:[^"\\]|\\.)*"\s*\]\s*$'
        )
        unsafe_link_style = any(
            re.search(r"\blinkStyle\b", line)
            and not line.lstrip().startswith("%%")
            and not quoted_node.fullmatch(line)
            for line in code.splitlines()
        )
        if unsafe_link_style:
            raise ReviewCommandError(
                "unsupported_mermaid", "indexed link styles prevent safe edge deletion"
            )
        lines = code.splitlines(keepends=True)
        matches: list[int] = []
        for index, line in enumerate(lines):
            match = _PLAIN_EDGE_RE.fullmatch(line)
            if match and match["src"] == source and match["dst"] == target:
                matches.append(index)
        if len(matches) != 1:
            error = "unresolved_reference" if not matches else "ambiguous_reference"
            raise ReviewCommandError(
                error, "Scene relation must map to exactly one plain Mermaid edge"
            )
        return "".join(line for index, line in enumerate(lines) if index != matches[0])

    if intent.operation == "delete_node":
        assert intent.node_id
        if before is None or not isinstance(before.get("relations"), list):
            raise ReviewCommandError(
                "unsupported_artifact", "node deletion requires matching Scene IR state"
            )
        declaration_re = re.compile(
            rf'^\s*{re.escape(intent.node_id)}\s*\[\s*"(?:[^"\\]|\\.)*"\s*\]\s*(?:\r?\n)?$'
        )
        lines = code.splitlines(keepends=True)
        declarations = [index for index, line in enumerate(lines) if declaration_re.fullmatch(line)]
        if len(declarations) != 1:
            error = "unresolved_reference" if not declarations else "ambiguous_reference"
            raise ReviewCommandError(
                error, "node must have exactly one quoted rectangle declaration"
            )
        expected_pairs: list[tuple[str, str]] = []
        for relation in before["relations"]:
            if not isinstance(relation, Mapping):
                raise ReviewCommandError("unsupported_ir", "deleted relation is malformed")
            source_key, target_key = _edge_keys(relation)
            source = relation.get(source_key)
            target = relation.get(target_key)
            if not isinstance(source, str) or not isinstance(target, str):
                raise ReviewCommandError(
                    "unsupported_ir", "deleted relation endpoints must be explicit"
                )
            expected_pairs.append((source, target))
        if len(expected_pairs) != len(set(expected_pairs)):
            raise ReviewCommandError(
                "ambiguous_reference", "parallel incident edges cannot be deleted safely"
            )
        edge_lines: list[tuple[int, tuple[str, str]]] = []
        other_references: list[int] = []
        node_reference = re.compile(rf"\b{re.escape(intent.node_id)}\b")
        for index, line in enumerate(lines):
            if index in declarations:
                continue
            match = _PLAIN_EDGE_RE.fullmatch(line)
            if match and intent.node_id in {match["src"], match["dst"]}:
                edge_lines.append((index, (match["src"], match["dst"])))
            elif node_reference.search(line):
                other_references.append(index)
        if other_references:
            raise ReviewCommandError(
                "unsupported_mermaid",
                "node has style, group, label, or unsupported Mermaid references",
            )
        actual_pairs = [pair for _, pair in edge_lines]
        if sorted(actual_pairs) != sorted(expected_pairs):
            raise ReviewCommandError(
                "unresolved_reference",
                "Scene incident relations do not map one-to-one to plain Mermaid edges",
            )
        remove = {declarations[0], *(index for index, _ in edge_lines)}
        return "".join(line for index, line in enumerate(lines) if index not in remove)

    if intent.operation == "delete_group":
        assert intent.group_id
        if before is None or before.get("id") != intent.group_id:
            raise ReviewCommandError(
                "unsupported_artifact", "group deletion requires matching Scene group state"
            )
        members = before.get("member_ids")
        if not isinstance(members, list) or not all(isinstance(item, str) for item in members):
            raise ReviewCommandError(
                "unsupported_ir", "deleted group members must be explicit node ids"
            )
        subgraphs = _flat_mermaid_subgraphs(code)
        record = subgraphs.get(intent.group_id)
        if record is None or record[0] != tuple(sorted(members)):
            raise ReviewCommandError(
                "unsupported_artifact", "Scene group does not map to one exact Mermaid subgraph"
            )
        _, start_line, end_line = record
        lines = code.splitlines(keepends=True)
        outside = "".join(
            line for index, line in enumerate(lines) if not start_line <= index <= end_line
        )
        group_reference = re.compile(
            rf"(?<![A-Za-z0-9_-]){re.escape(intent.group_id)}(?![A-Za-z0-9_-])"
        )
        if group_reference.search(outside):
            raise ReviewCommandError(
                "unsupported_mermaid", "group id has Mermaid references outside its subgraph"
            )
        return outside

    assert intent.operation == "group_nodes"
    missing = set(intent.node_ids) - set(ids)
    if missing:
        raise ReviewCommandError(
            "unresolved_reference", f"Mermaid group references missing node ids: {sorted(missing)}"
        )
    group_id = _group_id(intent.node_ids)
    scene_groups = before.get("existing_groups") if before is not None else None
    existing_subgraphs = _mermaid_subgraph_memberships(code)
    if scene_groups is not None and existing_subgraphs != scene_groups:
        raise ReviewCommandError(
            "unsupported_artifact", "Scene groups and Mermaid subgraphs do not match one-to-one"
        )
    if scene_groups is not None:
        existing_members = {
            member for members in scene_groups.values() for member in members
        }
        declaration_counts = _quoted_rectangle_declaration_counts(code, existing_members)
        if any(declaration_counts[member] != 1 for member in existing_members):
            raise ReviewCommandError(
                "unsupported_artifact",
                "existing grouped nodes require one quoted rectangle declaration",
            )
    if re.search(rf"\b{re.escape(group_id)}\b", code):
        raise ReviewCommandError("ambiguous_reference", f"subgraph already exists: {group_id}")
    claimed = {member for members in existing_subgraphs.values() for member in members}
    if set(intent.node_ids).intersection(claimed):
        raise ReviewCommandError(
            "ambiguous_reference", "a selected node already belongs to a Mermaid subgraph"
        )
    declaration_counts = _quoted_rectangle_declaration_counts(code, set(intent.node_ids))
    for node_id in intent.node_ids:
        if declaration_counts[node_id] != 1:
            raise ReviewCommandError(
                "unresolved_reference",
                "each grouped node must have one quoted rectangle Mermaid declaration",
            )
    label = _validated_label(intent.label or ", ".join(intent.node_ids))
    escaped = label.replace("\\", "\\\\").replace('"', '\\"')
    indent = "    "
    block = [f'{indent}subgraph {group_id}["{escaped}"]']
    block.extend(f"{indent * 2}{node_id}" for node_id in intent.node_ids)
    block.append(f"{indent}end")
    return code.rstrip() + "\n" + "\n".join(block) + "\n"


def apply_review_command(
    command: str,
    *,
    ir: Mapping[str, Any] | None = None,
    mermaid_code: str | None = None,
    reason: str | None = None,
) -> ReviewCommandResult:
    """Apply one recognized command without mutating caller-owned objects.

    When both artifacts are supplied, all applicable edits must succeed before either
    result is returned.  A type change updates IR metadata and intentionally leaves
    flowchart code untouched so the caller can regenerate it with the new serializer.
    """

    original_ir = deepcopy(dict(ir)) if ir is not None else None
    original_code = mermaid_code
    try:
        if original_ir is None and original_code is None:
            raise ReviewCommandError("missing_artifact", "an IR or Mermaid artifact is required")
        if original_ir is not None:
            _validate_ir_input(original_ir)

        intent = parse_review_command(command)
        patched_ir = deepcopy(original_ir)
        patched_code = original_code
        target = "diagram"
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}

        if patched_ir is not None:
            patched_ir, target, before, after = _apply_ir(intent, patched_ir)
        elif intent.operation == "change_diagram_type":
            raise ReviewCommandError(
                "unsupported_artifact", "diagram type changes require IR regeneration"
            )

        if patched_code is not None:
            patched_code = _apply_mermaid(intent, patched_code, before=before)
            if patched_ir is None:
                if intent.operation == "reverse_edge":
                    assert intent.source_id and intent.target_id
                    target = f"{intent.source_id}->{intent.target_id}"
                    before = {"source": intent.source_id, "target": intent.target_id}
                    after = {"source": intent.target_id, "target": intent.source_id}
                elif intent.operation == "relabel_node":
                    assert intent.node_id and intent.label is not None
                    target = intent.node_id
                    before = {"label": None}
                    after = {"label": intent.label}
                else:
                    group_id = _group_id(intent.node_ids)
                    target = group_id
                    after = {"id": group_id, "member_ids": intent.node_ids}
        if intent.operation == "group_nodes":
            before = {}

        history = ReviewHistoryEntry(
            operation=intent.operation,
            target=target,
            before=before,
            after=after,
            source="user",
            reason=reason or command,
        )
        regenerate = intent.operation == "change_diagram_type"
        message = (
            "command applied; Mermaid regeneration required" if regenerate else "command applied"
        )
        return ReviewCommandResult(
            applied=True,
            ir=patched_ir,
            mermaid_code=patched_code,
            history_entry=history,
            message=message,
            regeneration_required=regenerate,
        )
    except ReviewCommandError as error:
        return ReviewCommandResult(
            applied=False,
            ir=original_ir,
            mermaid_code=original_code,
            error_code=error.code,
            message=str(error),
        )


def apply_review_operation(
    operation: Mapping[str, Any],
    *,
    ir: Mapping[str, Any] | None,
    mermaid_code: str | None,
    provenance: list[Mapping[str, Any] | VisualEvidence] | None = None,
    user_evidence_id: str | None = None,
    user_relation_id: str | None = None,
    source_block_ids: list[str] | None = None,
    reason: str | None = None,
) -> ReviewCommandResult:
    """Apply a validated structured operation to synchronized review artifacts.

    The public operation surface is intentionally smaller than the natural-language
    intent model.  Every supported operation requires both Scene IR and Mermaid so a
    revision can never persist only one side of the representation.
    """

    original_ir = deepcopy(dict(ir)) if ir is not None else None
    original_code = mermaid_code
    original_provenance: list[dict[str, Any]] = []
    try:
        if provenance is not None and not isinstance(provenance, list):
            raise ReviewCommandError("invalid_evidence", "existing provenance must be a list")
        try:
            normalized_provenance = [
                VisualEvidence.model_validate(item) for item in provenance or []
            ]
        except ValidationError as error:
            raise ReviewCommandError(
                "invalid_evidence", "existing provenance contains invalid evidence"
            ) from error
        evidence_ids = [item.id for item in normalized_provenance]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ReviewCommandError(
                "ambiguous_reference", "existing provenance evidence ids must be unique"
            )
        original_provenance = [item.model_dump(mode="json") for item in normalized_provenance]
        if original_ir is None or original_code is None:
            raise ReviewCommandError(
                "missing_artifact", "structured operations require Scene IR and Mermaid code"
            )
        _validate_ir_input(original_ir)
        try:
            parsed = _STRUCTURED_OPERATION_ADAPTER.validate_python(operation)
        except ValidationError as error:
            raise ReviewCommandError(
                "invalid_operation", "operation payload does not match the supported schema"
            ) from error
        if parsed.operation in {"group_nodes", "delete_group"}:
            scene_groups = _validated_scene_groups(original_ir, set(_node_ids(original_ir)))
            mermaid_groups = _mermaid_subgraph_memberships(original_code)
            if scene_groups != mermaid_groups:
                raise ReviewCommandError(
                    "unsupported_artifact",
                    "Scene groups and Mermaid subgraphs do not match one-to-one",
                )
            existing_members = {
                member for members in scene_groups.values() for member in members
            }
            declaration_counts = _quoted_rectangle_declaration_counts(
                original_code, existing_members
            )
            if any(declaration_counts[member] != 1 for member in existing_members):
                raise ReviewCommandError(
                    "unsupported_artifact",
                    "existing grouped nodes require one quoted rectangle declaration",
                )
        if parsed.operation in {"add_edge", "delete_edge"}:
            ir_edges: Counter[tuple[str, str]] = Counter()
            for relation in _relation_container(original_ir):
                source_key, target_key = _edge_keys(relation)
                source = relation.get(source_key)
                target = relation.get(target_key)
                if not isinstance(source, str) or not isinstance(target, str):
                    raise ReviewCommandError(
                        "unsupported_ir", "editable relations require explicit endpoints"
                    )
                ir_edges[(source, target)] += 1
            mermaid_edges = _plain_mermaid_edge_counter(original_code)
            if ir_edges != mermaid_edges:
                raise ReviewCommandError(
                    "unsupported_artifact",
                    "Scene relations and plain Mermaid edges do not match one-to-one",
                )
        payload = parsed.model_dump()
        for key in ("node_id", "edge_id", "group_id", "source_id", "target_id"):
            value = payload.get(key)
            if value is not None:
                payload[key] = _validated_id(value)
        if payload.get("node_ids") is not None:
            payload["node_ids"] = [_validated_id(value) for value in payload["node_ids"]]
        if payload.get("label") is not None:
            payload["label"] = _validated_label(payload["label"])
        selected_evidence: VisualEvidence | None = None
        if parsed.operation == "relabel_node_from_evidence":
            node_id = payload["node_id"]
            evidence_id = payload["evidence_id"]
            nodes, _ = _node_container(original_ir)
            _node_ids(original_ir)
            node_matches = [node for node in nodes if node.get("id") == node_id]
            if len(node_matches) != 1:
                raise ReviewCommandError(
                    "unresolved_reference", "node id does not identify one Scene node"
                )
            linked_node_ids: list[str] = []
            target_reference_count = 0
            for node in nodes:
                references = node.get("evidence_ids", [])
                if not isinstance(references, list) or not all(
                    isinstance(reference, str) for reference in references
                ):
                    raise ReviewCommandError(
                        "unsupported_ir", "Scene node evidence references must be string lists"
                    )
                reference_count = references.count(evidence_id)
                if node.get("id") == node_id:
                    target_reference_count = reference_count
                if reference_count:
                    linked_node_ids.append(node["id"])
            if target_reference_count == 0:
                raise ReviewCommandError(
                    "unresolved_reference", "selected evidence is not linked to the target node"
                )
            if target_reference_count != 1 or linked_node_ids != [node_id]:
                raise ReviewCommandError(
                    "ambiguous_reference",
                    "selected evidence must be linked exactly once to one Scene node",
                )
            evidence_matches = [
                evidence for evidence in normalized_provenance if evidence.id == evidence_id
            ]
            if len(evidence_matches) != 1:
                raise ReviewCommandError(
                    "unresolved_reference", "selected evidence does not exist in provenance"
                )
            selected_evidence = evidence_matches[0]
            if selected_evidence.kind not in {"ocr_token", "vector_text"}:
                raise ReviewCommandError(
                    "invalid_evidence", "only OCR or vector-text evidence can supply a label"
                )
            payload["label"] = _validated_evidence_label(selected_evidence.text)
        if parsed.operation in {"add_node", "add_edge"}:
            if (
                not isinstance(reason, str)
                or not reason
                or not reason.strip()
                or len(reason) > MAX_REASON_LENGTH
                or any(ord(character) < 32 for character in reason)
            ):
                raise ReviewCommandError(
                    "missing_reason", "user-created nodes and edges require a bounded reason"
                )
            if (
                not isinstance(user_evidence_id, str)
                or not user_evidence_id
                or len(user_evidence_id) > 256
            ):
                raise ReviewCommandError(
                    "invalid_evidence", "server-created user evidence id is required"
                )
            payload["evidence_id"] = user_evidence_id
        if parsed.operation == "add_edge":
            if (
                not isinstance(user_relation_id, str)
                or not user_relation_id
                or len(user_relation_id) > 256
            ):
                raise ReviewCommandError(
                    "invalid_identifier", "server-created relation id is required"
                )
            payload["edge_id"] = _validated_id(user_relation_id)
        intent = ParsedReviewCommand.model_validate(payload)
        patched_ir, target, before, after = _apply_ir(intent, deepcopy(original_ir))
        patched_code = _apply_mermaid(intent, original_code, before=before)
        if intent.operation == "group_nodes":
            before = {}
        patched_provenance = deepcopy(original_provenance)
        provenance_changed = False
        if intent.operation in {"add_node", "add_edge"}:
            if any(item.get("id") == intent.evidence_id for item in patched_provenance):
                raise ReviewCommandError(
                    "ambiguous_reference", "user evidence id already exists in provenance"
                )
            try:
                evidence = VisualEvidence(
                    id=intent.evidence_id,
                    kind="user_edit",
                    bbox=intent.bbox if intent.operation == "add_node" else None,
                    text=intent.label if intent.operation == "add_node" else reason.strip(),
                    score=1.0,
                    source_block_ids=source_block_ids or [],
                )
            except ValidationError as error:
                raise ReviewCommandError(
                    "invalid_evidence", "server-created user evidence is invalid"
                ) from error
            patched_provenance.append(evidence.model_dump(mode="json"))
            provenance_changed = True
        history = ReviewHistoryEntry(
            operation=intent.operation,
            target=target,
            before=before,
            after=after,
            source="user",
            reason=(
                reason
                or (
                    f"selected {selected_evidence.kind} evidence {selected_evidence.id}"
                    if selected_evidence is not None
                    else intent.operation
                )
            ),
        )
        return ReviewCommandResult(
            applied=True,
            ir=patched_ir,
            mermaid_code=patched_code,
            provenance=patched_provenance,
            provenance_changed=provenance_changed,
            history_entry=history,
            message="structured operation applied",
        )
    except ReviewCommandError as error:
        return ReviewCommandResult(
            applied=False,
            ir=original_ir,
            mermaid_code=original_code,
            provenance=original_provenance,
            error_code=error.code,
            message=str(error),
        )
