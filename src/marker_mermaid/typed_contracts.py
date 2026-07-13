"""Type-specific root contracts and prompt guidance for structured extraction.

Serializers remain the authoritative deep semantic validators.  These contracts form
the earlier extraction boundary: they reject another diagram family's root shape,
validate known Phase 1 nested records without rewriting the original dictionary, and
give the VLM a compact enabled-type-only schema catalog instead of asking it to guess
field names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError

from marker_mermaid.config import ALL_TYPES, PHASE_ONE_TYPES

RootKind = Literal["list", "object", "string"]
BBoxList = Annotated[list[FiniteFloat], Field(min_length=4, max_length=4)]
BBoxValue = BBoxList | tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]


class _TypedIRRecord(BaseModel):
    """Strict known fields with forward-compatible extra evidence metadata."""

    model_config = ConfigDict(extra="allow", strict=True)

    evidence_ids: list[str] | None = None
    bbox: BBoxValue | None = None


class _TypedIRRoot(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    title: str | None = None
    description: str | None = None
    acc_title: str | None = None
    acc_description: str | None = None
    direction: str | None = None


class _FlowNode(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    text: str | None = None
    role: str | None = None
    shape: str | None = None
    fill_color: str | None = None
    border_color: str | None = None
    border_style: str | None = None
    font_weight: str | None = None


class _FlowEdge(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    label: str | None = None
    relation_type: str | None = None
    semantic_relation: str | None = None
    style: str | None = None
    line_style: str | None = None
    line_color: str | None = None
    bidirectional: bool | None = None
    arrow_at_start: bool | None = None
    arrow_at_end: bool | None = None


class _FlowGroup(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    role: str | None = None
    member_ids: list[str] | None = None
    fill_color: str | None = None
    border_color: str | None = None
    border_style: str | None = None


class _FlowchartIR(_TypedIRRoot):
    nodes: list[_FlowNode]
    edges: list[_FlowEdge] = Field(default_factory=list)
    groups: list[_FlowGroup] = Field(default_factory=list)


class _Lane(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    role: str | None = None
    nodes: list[_FlowNode] = Field(default_factory=list)


class _SwimlaneIR(_TypedIRRoot):
    lanes: list[_Lane]
    edges: list[_FlowEdge] = Field(default_factory=list)


class _SequenceParticipant(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    text: str | None = None


class _SequenceMessage(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    label: str | None = None
    style: str | None = None


class _SequenceIR(_TypedIRRoot):
    participants: list[str | _SequenceParticipant]
    messages: list[_SequenceMessage]


class _HierarchyNode(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    text: str | None = None
    children: list[_HierarchyNode] = Field(default_factory=list)


class _MindmapIR(_TypedIRRoot):
    root: _HierarchyNode


class _TimelineEvent(_TypedIRRecord):
    id: str | None = None
    time: str | None = None
    period: str | None = None
    label: str | None = None
    events: list[str] | None = None


class _TimelineIR(_TypedIRRoot):
    events: list[_TimelineEvent]


class _GanttTask(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    text: str | None = None
    status: str | None = None
    start: str | None = None
    end: str | None = None
    duration: str | None = None


class _GanttSection(_TypedIRRecord):
    id: str | None = None
    title: str | None = None
    label: str | None = None
    tasks: list[_GanttTask] = Field(default_factory=list)


class _GanttIR(_TypedIRRoot):
    date_format: str | None = None
    sections: list[_GanttSection]


class _ArchitectureService(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    name: str | None = None
    icon: str | None = None
    group: str | None = None


class _ArchitectureGroup(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    icon: str | None = None


class _ArchitectureEdge(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    label: str | None = None
    source_side: Literal["L", "R", "T", "B"] | None = None
    target_side: Literal["L", "R", "T", "B"] | None = None
    bidirectional: bool | None = None


class _ArchitectureIR(_TypedIRRoot):
    services: list[_ArchitectureService]
    groups: list[_ArchitectureGroup] = Field(default_factory=list)
    edges: list[_ArchitectureEdge] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TypedIRContract:
    required: tuple[tuple[str, RootKind], ...]
    optional: tuple[str, ...] = ()
    guidance: str = ""
    nested_model: type[BaseModel] | None = None
    prompt_records: tuple[str, ...] = ()


_FLOW_NODE_PROMPT = (
    "nodes[]: {id:string,label:string,text:string,role:string,shape:string,"
    "bbox:number[4],evidence_ids:string[]}"
)
_FLOW_EDGE_PROMPT = (
    "edges[]: {id:string,source:string,target:string,label:string,relation_type:string,"
    "semantic_relation:string,style:string,bidirectional:boolean,evidence_ids:string[]}"
)
_FLOW_GROUP_PROMPT = (
    "groups[]: {id:string,label:string,role:string,bbox:number[4],"
    "member_ids:string[],evidence_ids:string[]}"
)
_LANE_PROMPTS = (
    "lanes[]: {id:string,label:string,bbox:number[4],nodes:node[]}",
    "lanes[].nodes[]: {id:string,label:string,text:string,role:string,shape:string,"
    "bbox:number[4],evidence_ids:string[]}",
    _FLOW_EDGE_PROMPT,
)


TYPED_IR_CONTRACTS: dict[str, TypedIRContract] = {
    "flowchart": TypedIRContract(
        (("nodes", "list"),),
        ("edges", "groups"),
        "node/edge graph",
        _FlowchartIR,
        (_FLOW_NODE_PROMPT, _FLOW_EDGE_PROMPT, _FLOW_GROUP_PROMPT),
    ),
    "generic_network": TypedIRContract(
        (("nodes", "list"),),
        ("edges", "groups"),
        "generic node/edge graph",
        _FlowchartIR,
        (_FLOW_NODE_PROMPT, _FLOW_EDGE_PROMPT, _FLOW_GROUP_PROMPT),
    ),
    "swimlane": TypedIRContract(
        (("lanes", "list"),),
        ("edges",),
        "lanes with nested nodes",
        _SwimlaneIR,
        _LANE_PROMPTS,
    ),
    "bpmn": TypedIRContract(
        (("lanes", "list"),),
        ("edges",),
        "BPMN lanes/tasks/events",
        _SwimlaneIR,
        _LANE_PROMPTS,
    ),
    "sequence": TypedIRContract(
        (("participants", "list"), ("messages", "list")),
        guidance="ordered messages",
        nested_model=_SequenceIR,
        prompt_records=(
            "participants[]: string|{id:string,label:string,bbox:number[4],evidence_ids:string[]}",
            "messages[]: {id:string,source:string,target:string,label:string,style:string,"
            "bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "state": TypedIRContract(
        (("states", "list"), ("transitions", "list")), guidance="states and transitions"
    ),
    "class": TypedIRContract((("classes", "list"),), ("relations",), "classes, members, relations"),
    "er": TypedIRContract(
        (("entities", "list"),), ("relationships",), "entities and cardinalities"
    ),
    "architecture": TypedIRContract(
        (("services", "list"),),
        ("groups", "edges"),
        "services, groups, connections",
        _ArchitectureIR,
        (
            "services[]: {id:string,label:string,name:string,icon:string,group:string,"
            "bbox:number[4],evidence_ids:string[]}",
            "groups[]: {id:string,label:string,icon:string,bbox:number[4],evidence_ids:string[]}",
            "edges[]: {id:string,source:string,target:string,source_side:L|R|T|B,"
            "target_side:L|R|T|B,bidirectional:boolean,"
            "bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "c4": TypedIRContract(
        (("elements", "list"),), ("boundaries", "relations", "level"), "C4 elements"
    ),
    "requirement": TypedIRContract(
        (("requirements", "list"),), ("elements", "relations"), "requirements and relations"
    ),
    "block": TypedIRContract((("blocks", "list"),), ("edges", "columns"), "blocks and ports"),
    "deployment": TypedIRContract(
        (("nodes", "list"),), ("artifacts", "links", "edges"), "deployment nodes/artifacts"
    ),
    "component": TypedIRContract(
        (("components", "list"),),
        ("interfaces", "dependencies", "edges"),
        "components and interfaces",
    ),
    "usecase": TypedIRContract(
        (("actors", "list"), ("use_cases", "list")),
        ("relations",),
        "actors and use cases",
    ),
    "mindmap": TypedIRContract(
        (("root", "object"),),
        guidance="rooted hierarchy",
        nested_model=_MindmapIR,
        prompt_records=(
            "root: {id:string,label:string,text:string,bbox:number[4],"
            "evidence_ids:string[],children:self[]}",
        ),
    ),
    "timeline": TypedIRContract(
        (("events", "list"),),
        guidance="time/event records",
        nested_model=_TimelineIR,
        prompt_records=(
            "events[]: {id:string,time:string,period:string,label:string,events:string[],"
            "bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "gantt": TypedIRContract(
        (("sections", "list"),),
        ("date_format",),
        guidance="sections with dated tasks",
        nested_model=_GanttIR,
        prompt_records=(
            "sections[]: {id:string,title:string,bbox:number[4],evidence_ids:string[],"
            "tasks:task[]}",
            "sections[].tasks[]: {id:string,label:string,text:string,status:string,"
            "start:string,end:string,duration:string,bbox:number[4],"
            "evidence_ids:string[]}",
        ),
    ),
    "journey": TypedIRContract((("sections", "list"),), guidance="sections with scored tasks"),
    "kanban": TypedIRContract(
        (("columns", "list"), ("cards", "list")), guidance="columns and assigned cards"
    ),
    "gitgraph": TypedIRContract(
        (("initial_branch", "string"), ("operations", "list")),
        guidance="ordered branch/commit/merge operations",
    ),
    "pie": TypedIRContract((("slices", "list"),), guidance="labels with explicit values"),
    "xychart": TypedIRContract(
        (("x_axis", "object"), ("y_axis", "object"), ("series", "list")),
        guidance="explicit axes and series values",
    ),
    "quadrant": TypedIRContract(
        (("x_axis", "object"), ("y_axis", "object"), ("points", "list")),
        ("quadrants",),
        "normalized positioned points",
    ),
    "sankey": TypedIRContract(
        (("nodes", "list"), ("flows", "list")), ("links",), "weighted directed flows"
    ),
    "radar": TypedIRContract(
        (("dimensions", "list"), ("series", "list")), guidance="dimensions and explicit values"
    ),
    "treemap": TypedIRContract((("root", "object"),), guidance="valued hierarchy"),
    "venn": TypedIRContract(
        (("sets", "list"), ("intersections", "list")), guidance="sets and explicit intersections"
    ),
    "packet": TypedIRContract((("fields", "list"),), guidance="explicit bit ranges"),
    "ishikawa": TypedIRContract(
        (("effect", "object"), ("categories", "list")), guidance="effect/category/cause tree"
    ),
    "wardley": TypedIRContract(
        (("components", "list"),), ("links",), "components with explicit x/y coordinates"
    ),
    "cynefin": TypedIRContract((("domains", "list"),), ("transitions",), "named domains and items"),
    "treeview": TypedIRContract((("root", "object"),), guidance="rooted hierarchy"),
    "eventmodeling": TypedIRContract(
        (("lanes", "list"),), ("relations",), "lanes with command/event frames"
    ),
    "zenuml": TypedIRContract(
        (("participants", "list"), ("messages", "list")), guidance="sequence-like messages"
    ),
    "railroad": TypedIRContract((("rules", "list"),), guidance="grammar rule AST"),
    "organization": TypedIRContract((("root", "object"),), guidance="organization hierarchy"),
    "data_lineage": TypedIRContract(
        (("datasets", "list"), ("relations", "list")),
        ("processes",),
        "datasets, processes, lineage relations",
    ),
}

if set(TYPED_IR_CONTRACTS) != set(ALL_TYPES):  # pragma: no cover - import-time invariant
    missing = sorted(set(ALL_TYPES) - set(TYPED_IR_CONTRACTS))
    extra = sorted(set(TYPED_IR_CONTRACTS) - set(ALL_TYPES))
    raise RuntimeError(f"typed IR contract registry mismatch: missing={missing}, extra={extra}")

PHASE_ONE_NESTED_TYPES = PHASE_ONE_TYPES | {"generic_network"}

for _diagram_type in PHASE_ONE_NESTED_TYPES:  # pragma: no cover - import-time invariant
    _contract = TYPED_IR_CONTRACTS[_diagram_type]
    if _contract.nested_model is None or not _contract.prompt_records:
        raise RuntimeError(f"{_diagram_type} is missing its nested typed IR contract")


def _validation_location(parts: tuple[int | str, ...]) -> str:
    location = ""
    for part in parts:
        if isinstance(part, str) and (part.startswith("_") or part in {"str", "list"}):
            continue
        if isinstance(part, int):
            location += f"[{part}]"
        else:
            location += ("." if location else "") + part
    return location or "root"


def _validate_nested_contract(
    diagram_type: str, contract: TypedIRContract, ir: dict[str, Any]
) -> None:
    if contract.nested_model is None:
        return
    try:
        contract.nested_model.model_validate(ir, strict=True)
    except ValidationError as exc:
        errors = exc.errors(
            include_context=False,
            include_input=False,
            include_url=False,
        )
        error = max(errors, key=lambda item: len(item["loc"]))
        location = _validation_location(error["loc"])
        raise ValueError(
            f"{diagram_type} typed IR violates its nested contract at {location}: {error['msg']}"
        ) from exc


def validate_typed_ir_contract(diagram_type: str, ir: dict[str, Any]) -> None:
    """Validate the diagram-family root shape without duplicating serializer semantics."""

    contract = TYPED_IR_CONTRACTS.get(diagram_type)
    if contract is None:
        raise ValueError(f"typed IR has no registered contract for {diagram_type!r}")
    for field, kind in contract.required:
        if field not in ir:
            raise ValueError(f"{diagram_type} typed IR requires root field {field!r}")
        value = ir[field]
        if kind == "list" and not isinstance(value, list):
            raise ValueError(f"{diagram_type} typed IR field {field!r} must be a list")
        if kind == "object" and not isinstance(value, dict):
            raise ValueError(f"{diagram_type} typed IR field {field!r} must be an object")
        if kind == "string" and not isinstance(value, str):
            raise ValueError(f"{diagram_type} typed IR field {field!r} must be a string")
    if diagram_type in {"flowchart", "generic_network"}:
        record_fields = (
            ("nodes", ("id", "label", "text", "role", "shape")),
            ("edges", ("id", "source", "target", "label", "relation_type")),
            ("groups", ("id", "label", "role")),
        )
        for collection_name, text_fields in record_fields:
            collection = ir.get(collection_name, [])
            if not isinstance(collection, list) or any(
                not isinstance(record, dict) for record in collection
            ):
                raise ValueError(
                    f"{diagram_type} typed IR field {collection_name!r} must contain objects"
                )
            for index, record in enumerate(collection):
                for field in text_fields:
                    value = record.get(field)
                    if value is not None and not isinstance(value, str):
                        raise ValueError(
                            f"{diagram_type} typed IR {collection_name}[{index}].{field} "
                            "must be a string"
                        )
    _validate_nested_contract(diagram_type, contract, ir)


def typed_ir_contract_prompt(enabled_types: set[str]) -> str:
    """Render a compact deterministic schema catalog for the enabled VLM types."""

    lines = [
        "Typed IR root contracts (use these exact field names):",
        "Common optional fields: title, description, acc_title, acc_description, direction.",
        "Every semantic node/relation record must include evidence_ids from Prior evidence.",
        "Nested record fields below are type constraints when present; serializer guidance "
        "still decides semantic requiredness.",
    ]
    for diagram_type in sorted(enabled_types):
        contract = TYPED_IR_CONTRACTS[diagram_type]
        required = ", ".join(f"{field}:{kind}" for field, kind in contract.required)
        optional = f"; optional {', '.join(contract.optional)}" if contract.optional else ""
        guidance = f"; {contract.guidance}" if contract.guidance else ""
        lines.append(f"- {diagram_type}: {required}{optional}{guidance}")
        lines.extend(f"  {diagram_type}.{record}" for record in contract.prompt_records)
    return "\n".join(lines)
