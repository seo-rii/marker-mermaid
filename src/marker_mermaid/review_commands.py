"""Conservative, deterministic natural-language patches for the review workspace.

This module deliberately recognizes a small command language.  It does not try to
guess when a reference is spatial or otherwise ambiguous; callers can hand those
commands to a human (or an explicitly configured model) without risking a partial
edit.  Applying a command is transactional across the supplied IR and Mermaid
artifacts.
"""

from __future__ import annotations

import json
import math
import re
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
        "group_nodes",
        "change_diagram_type",
        "add_node",
        "delete_node",
        "reconnect_edge",
    ]
    edge_id: str | None = None
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


StructuredReviewOperation = Annotated[
    AddNodeOperation | DeleteNodeOperation | ReconnectEdgeOperation,
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

    if intent.operation == "relabel_node":
        assert intent.node_id and intent.label is not None
        matches = [node for node in _node_container(ir)[0] if node.get("id") == intent.node_id]
        if len(matches) != 1:
            raise ReviewCommandError("unresolved_reference", "node id does not exist in the IR")
        node = matches[0]
        label_key = next((key for key in ("text", "label", "name") if key in node), "label")
        before = {label_key: node.get(label_key)}
        node[label_key] = intent.label
        return ir, intent.node_id, before, {label_key: intent.label}

    if intent.operation == "group_nodes":
        missing = set(intent.node_ids) - set(ids)
        if missing:
            raise ReviewCommandError(
                "unresolved_reference", f"group references missing node ids: {sorted(missing)}"
            )
        group_id = "group_" + "_".join(intent.node_ids)
        if len(group_id) > 128:
            raise ReviewCommandError("input_too_large", "generated group id exceeds the safe limit")
        groups = ir.setdefault("groups", [])
        if not isinstance(groups, list) or not all(isinstance(item, dict) for item in groups):
            raise ReviewCommandError("unsupported_ir", "IR groups must be a list")
        if any(group.get("id") == group_id for group in groups):
            raise ReviewCommandError("ambiguous_reference", f"group id already exists: {group_id}")
        selected_nodes = [
            node for node in _node_container(ir)[0] if node.get("id") in intent.node_ids
        ]
        boxes = [node.get("bbox") for node in selected_nodes]
        if len(boxes) != len(intent.node_ids) or any(
            not isinstance(box, list | tuple)
            or len(box) != 4
            or any(not isinstance(value, int | float) for value in box)
            for box in boxes
        ):
            raise ReviewCommandError(
                "unsupported_ir", "group members require explicit four-number bbox evidence"
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
        groups.append(group)
        return ir, group_id, {}, group

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

    if intent.operation == "relabel_node":
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

    assert intent.operation == "group_nodes"
    missing = set(intent.node_ids) - set(ids)
    if missing:
        raise ReviewCommandError(
            "unresolved_reference", f"Mermaid group references missing node ids: {sorted(missing)}"
        )
    group_id = "group_" + "_".join(intent.node_ids)
    if re.search(rf"(?m)^\s*subgraph\s+{re.escape(group_id)}\b", code):
        raise ReviewCommandError("ambiguous_reference", f"subgraph already exists: {group_id}")
    label = intent.label or ", ".join(intent.node_ids)
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
                    group_id = "group_" + "_".join(intent.node_ids)
                    target = group_id
                    after = {"id": group_id, "member_ids": intent.node_ids}

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
        payload = parsed.model_dump()
        for key in ("node_id", "edge_id", "source_id", "target_id"):
            value = payload.get(key)
            if value is not None:
                payload[key] = _validated_id(value)
        if payload.get("label") is not None:
            payload["label"] = _validated_label(payload["label"])
        if parsed.operation == "add_node":
            if not reason or not reason.strip():
                raise ReviewCommandError(
                    "missing_reason", "source-anchored node addition requires a reason"
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
        intent = ParsedReviewCommand.model_validate(payload)
        patched_ir, target, before, after = _apply_ir(intent, deepcopy(original_ir))
        patched_code = _apply_mermaid(intent, original_code, before=before)
        patched_provenance = deepcopy(original_provenance)
        provenance_changed = False
        if intent.operation == "add_node":
            if any(item.get("id") == intent.evidence_id for item in patched_provenance):
                raise ReviewCommandError(
                    "ambiguous_reference", "user evidence id already exists in provenance"
                )
            try:
                evidence = VisualEvidence(
                    id=intent.evidence_id,
                    kind="user_edit",
                    bbox=intent.bbox,
                    text=intent.label,
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
            reason=reason or intent.operation,
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
