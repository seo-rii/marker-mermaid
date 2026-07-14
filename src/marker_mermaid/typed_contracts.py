"""Type-specific root contracts and prompt guidance for structured extraction.

Serializers remain the authoritative deep semantic validators.  These contracts form
the earlier extraction boundary: they reject another diagram family's root shape,
progressively validate implemented nested records (including native Phase 2 records)
without rewriting the original dictionary, and give the VLM a compact
enabled-type-only schema catalog instead of asking it to guess field names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError, field_validator

from marker_mermaid.config import ALL_TYPES, PHASE_ONE_TYPES

RootKind = Literal["list", "object", "string"]
BBoxList = Annotated[list[FiniteFloat], Field(min_length=4, max_length=4)]
BBoxValue = BBoxList | tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
_ChartNumber = int | FiniteFloat


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


class _StateNode(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    kind: Literal["state", "choice", "fork", "join"] | None = None


class _StateTransition(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    label: str | None = None


class _StateIR(_TypedIRRoot):
    states: list[_StateNode]
    transitions: list[_StateTransition]


class _ClassMember(_TypedIRRecord):
    name: str | None = None
    type: str | None = None
    visibility: Literal["", "+", "-", "#", "~"] = ""
    kind: Literal["field", "method"] = "field"
    parameters: list[str] = Field(default_factory=list)
    return_type: str | None = None
    classifier: Literal["", "static", "abstract"] | None = None


class _ClassNode(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    members: list[_ClassMember] = Field(default_factory=list)


class _ClassRelation(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    type: (
        Literal[
            "association",
            "dependency",
            "aggregation",
            "composition",
            "link",
            "inheritance",
            "realization",
        ]
        | None
    ) = None
    label: str | None = None
    source_cardinality: str | None = None
    target_cardinality: str | None = None


class _ClassIR(_TypedIRRoot):
    classes: list[_ClassNode]
    relations: list[_ClassRelation] = Field(default_factory=list)


class _ERAttribute(_TypedIRRecord):
    type: str | None = None
    name: str | None = None
    keys: list[Literal["PK", "FK", "UK"]] = Field(default_factory=list)
    comment: str | None = None


class _EREntity(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    attributes: list[_ERAttribute] = Field(default_factory=list)


class _ERRelationship(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    source_cardinality: (
        Literal["one", "only_one", "zero_or_one", "one_or_more", "zero_or_more"] | None
    ) = None
    target_cardinality: (
        Literal["one", "only_one", "zero_or_one", "one_or_more", "zero_or_more"] | None
    ) = None
    identifying: bool | None = None
    label: str | None = None


class _ERIR(_TypedIRRoot):
    entities: list[_EREntity]
    relationships: list[_ERRelationship] = Field(default_factory=list)


_REQUIREMENT_TYPES = frozenset(
    {
        "requirement",
        "functional",
        "functional_requirement",
        "interface",
        "interface_requirement",
        "performance",
        "performance_requirement",
        "physical",
        "physical_requirement",
        "design_constraint",
    }
)
_REQUIREMENT_RISKS = frozenset({"low", "medium", "high"})
_REQUIREMENT_VERIFY_METHODS = frozenset({"analysis", "demonstration", "inspection", "test"})
_REQUIREMENT_RELATION_TYPES = frozenset(
    {"contains", "copies", "derives", "satisfies", "verifies", "refines", "traces"}
)
_BLOCK_SHAPES = frozenset(
    {
        "rectangle",
        "round",
        "stadium",
        "circle",
        "diamond",
        "hexagon",
        "cylinder",
        "subroutine",
    }
)
_C4_LEVELS = frozenset({"context", "container", "component"})
_C4_ELEMENT_KINDS = frozenset(
    {
        "person",
        "external_person",
        "system",
        "external_system",
        "database",
        "external_database",
        "queue",
        "external_queue",
        "container",
        "container_database",
        "container_queue",
        "component",
        "component_database",
        "component_queue",
    }
)
_GITGRAPH_OPERATION_TYPES = frozenset({"commit", "branch", "merge"})
_GITGRAPH_COMMIT_TYPES = frozenset({"normal", "reverse", "highlight"})
_GITGRAPH_DIRECTIONS = frozenset({"lr", "tb", "bt"})


def _validate_casefolded_token(
    value: str | None,
    allowed: frozenset[str],
    *,
    field: str,
) -> str | None:
    """Validate a serializer-normalized token without rewriting the source IR."""

    if value not in {None, ""} and value.lower() not in allowed:
        raise ValueError(f"{field} has an unsupported token")
    return value


class _RequirementRecord(_TypedIRRecord):
    id: str | None = None
    requirement_id: str | None = None
    text: str | None = None
    label: str | None = None
    type: str | None = None
    risk: str | None = None
    verify_method: str | None = None
    verifymethod: str | None = None

    @field_validator("type", "risk", "verify_method", "verifymethod")
    @classmethod
    def closed_token_is_supported(cls, value: str | None, info: Any) -> str | None:
        allowed = {
            "type": _REQUIREMENT_TYPES,
            "risk": _REQUIREMENT_RISKS,
            "verify_method": _REQUIREMENT_VERIFY_METHODS,
            "verifymethod": _REQUIREMENT_VERIFY_METHODS,
        }[info.field_name]
        return _validate_casefolded_token(
            value,
            allowed,
            field=f"requirement {info.field_name}",
        )


class _RequirementElement(_TypedIRRecord):
    id: str | None = None
    type: str | None = None
    label: str | None = None
    docref: str | None = None


class _RequirementRelation(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    label: str | None = None
    type: str | None = None

    @field_validator("type")
    @classmethod
    def relation_type_is_supported(cls, value: str | None) -> str | None:
        return _validate_casefolded_token(
            value,
            _REQUIREMENT_RELATION_TYPES,
            field="requirement relation type",
        )


class _RequirementIR(_TypedIRRoot):
    requirements: list[_RequirementRecord]
    elements: list[_RequirementElement] = Field(default_factory=list)
    relations: list[_RequirementRelation] = Field(default_factory=list)


class _BlockNode(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    text: str | None = None
    shape: str | None = None

    @field_validator("shape")
    @classmethod
    def shape_is_supported(cls, value: str | None) -> str | None:
        return _validate_casefolded_token(value, _BLOCK_SHAPES, field="block shape")


class _BlockEdge(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    label: str | None = None
    style: str | None = None
    bidirectional: bool | None = None


class _BlockIR(_TypedIRRoot):
    blocks: list[_BlockNode]
    edges: list[_BlockEdge] = Field(default_factory=list)
    columns: str | int | None = None


class _C4Element(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    name: str | None = None
    kind: str | None = None
    type: str | None = None
    boundary: str | None = None
    description: str | None = None
    technology: str | None = None

    @field_validator("kind", "type")
    @classmethod
    def kind_is_supported(cls, value: str | None) -> str | None:
        return _validate_casefolded_token(value, _C4_ELEMENT_KINDS, field="C4 element kind")


class _C4Boundary(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    type: str | None = None


class _C4Relation(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    label: str | None = None
    technology: str | None = None
    bidirectional: bool | None = None
    source_side: Literal["L", "R", "T", "B"] | None = None
    target_side: Literal["L", "R", "T", "B"] | None = None


class _C4IR(_TypedIRRoot):
    level: str | None = None
    elements: list[_C4Element]
    boundaries: list[_C4Boundary] = Field(default_factory=list)
    relations: list[_C4Relation] = Field(default_factory=list)

    @field_validator("level")
    @classmethod
    def level_is_supported(cls, value: str | None) -> str | None:
        return _validate_casefolded_token(value, _C4_LEVELS, field="C4 level")


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


class _JourneyTask(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    text: str | None = None
    score: int | None = None
    actors: list[str] | None = None


class _JourneySection(_TypedIRRecord):
    title: str | None = None
    label: str | None = None
    tasks: list[_JourneyTask] = Field(default_factory=list)


class _JourneyIR(_TypedIRRoot):
    sections: list[_JourneySection]


class _KanbanColumn(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    title: str | None = None


class _KanbanCard(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    text: str | None = None
    column_id: str | None = None


class _KanbanIR(_TypedIRRoot):
    columns: list[_KanbanColumn]
    cards: list[_KanbanCard]


class _GitGraphOperation(_TypedIRRecord):
    type: str | None = None
    id: str | None = None
    branch: str | None = None
    tag: str | None = None
    commit_type: str | None = None
    style: str | None = None
    name: str | None = None
    from_: str | None = Field(default=None, alias="from")
    source: str | None = None
    target: str | None = None
    order: int | None = None

    @field_validator("type", "commit_type", "style")
    @classmethod
    def closed_token_is_supported(cls, value: str | None, info: Any) -> str | None:
        allowed = {
            "type": _GITGRAPH_OPERATION_TYPES,
            "commit_type": _GITGRAPH_COMMIT_TYPES,
            "style": _GITGRAPH_COMMIT_TYPES,
        }[info.field_name]
        return _validate_casefolded_token(
            value,
            allowed,
            field=f"gitgraph operation {info.field_name}",
        )


class _GitGraphIR(_TypedIRRoot):
    initial_branch: str
    operations: list[_GitGraphOperation]

    @field_validator("direction")
    @classmethod
    def direction_is_supported(cls, value: str | None) -> str | None:
        return _validate_casefolded_token(
            value,
            _GITGRAPH_DIRECTIONS,
            field="gitgraph direction",
        )


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


class _ArchitectureFallbackRecord(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    name: str | None = None
    icon: str | None = None
    group: str | None = None


class _ArchitectureFallbackGroup(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    icon: str | None = None


class _ArchitectureFallbackEdge(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    label: str | None = None
    bidirectional: bool | None = None
    source_side: Literal["L", "R", "T", "B"] | None = None
    target_side: Literal["L", "R", "T", "B"] | None = None


class _DeploymentIR(_TypedIRRoot):
    nodes: list[_ArchitectureFallbackRecord]
    artifacts: list[_ArchitectureFallbackRecord] = Field(default_factory=list)
    groups: list[_ArchitectureFallbackGroup] = Field(default_factory=list)
    links: list[_ArchitectureFallbackEdge] = Field(default_factory=list)
    edges: list[_ArchitectureFallbackEdge] = Field(default_factory=list)


class _ComponentIR(_TypedIRRoot):
    components: list[_ArchitectureFallbackRecord]
    interfaces: list[_ArchitectureFallbackRecord] = Field(default_factory=list)
    groups: list[_ArchitectureFallbackGroup] = Field(default_factory=list)
    dependencies: list[_ArchitectureFallbackEdge] = Field(default_factory=list)
    edges: list[_ArchitectureFallbackEdge] = Field(default_factory=list)


class _UsecaseNode(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    name: str | None = None


class _UsecaseRelation(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    type: str | None = None
    label: str | None = None


class _UsecaseIR(_TypedIRRoot):
    actors: list[_UsecaseNode]
    use_cases: list[_UsecaseNode]
    relations: list[_UsecaseRelation] = Field(default_factory=list)


class _PieSlice(_TypedIRRecord):
    label: str | None = None
    value: _ChartNumber | None = None


class _PieIR(_TypedIRRoot):
    slices: list[_PieSlice]
    show_data: bool = False


class _XYXAxis(_TypedIRRecord):
    label: str | None = None
    categories: list[str] | None = None
    min: _ChartNumber | None = None
    max: _ChartNumber | None = None


class _XYYAxis(_TypedIRRecord):
    label: str | None = None
    min: _ChartNumber | None = None
    max: _ChartNumber | None = None


class _XYPoint(_TypedIRRecord):
    x: _ChartNumber | None = None
    y: _ChartNumber | None = None


class _XYSeries(_TypedIRRecord):
    kind: str | None = None
    values: list[_ChartNumber] | None = None
    points: list[_XYPoint] | None = None

    @field_validator("kind")
    @classmethod
    def kind_is_supported(cls, value: str | None) -> str | None:
        return _validate_casefolded_token(
            value,
            frozenset({"line", "bar"}),
            field="xychart series kind",
        )


class _XYChartIR(_TypedIRRoot):
    x_axis: _XYXAxis
    y_axis: _XYYAxis
    series: list[_XYSeries]


class _QuadrantAxis(_TypedIRRecord):
    low: str | None = None
    high: str | None = None


class _QuadrantPoint(_TypedIRRecord):
    label: str | None = None
    x: _ChartNumber | None = None
    y: _ChartNumber | None = None


class _QuadrantIR(_TypedIRRoot):
    x_axis: _QuadrantAxis
    y_axis: _QuadrantAxis
    points: list[_QuadrantPoint]
    quadrants: list[str] | dict[str, str] | None = None


class _SankeyNode(_TypedIRRecord):
    id: str | None = None
    label: str | None = None


class _SankeyFlow(_TypedIRRecord):
    id: str | None = None
    source: str | None = None
    target: str | None = None
    value: _ChartNumber | None = None


class _SankeyIR(_TypedIRRoot):
    nodes: list[_SankeyNode]
    flows: list[_SankeyFlow]
    links: list[_SankeyFlow] = Field(default_factory=list)


class _RadarDimension(_TypedIRRecord):
    id: str | None = None
    label: str | None = None


class _RadarSeries(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    values: list[_ChartNumber] | None = None


class _RadarIR(_TypedIRRoot):
    dimensions: list[_RadarDimension]
    series: list[_RadarSeries]
    axes: list[_RadarDimension] = Field(default_factory=list)
    min: _ChartNumber | None = None
    max: _ChartNumber | None = None
    ticks: int | None = None
    show_legend: bool | None = None
    graticule: Literal["circle", "polygon"] | None = None


class _TreemapNode(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    name: str | None = None
    value: _ChartNumber | None = None
    children: list[_TreemapNode] | None = None


class _TreemapIR(_TypedIRRoot):
    root: _TreemapNode


class _VennSet(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    name: str | None = None
    value: _ChartNumber | None = None


class _VennIntersection(_TypedIRRecord):
    id: str | None = None
    sets: list[str] | None = None
    label: str | None = None
    name: str | None = None
    value: _ChartNumber | None = None


class _VennIR(_TypedIRRoot):
    sets: list[_VennSet]
    intersections: list[_VennIntersection]


class _PacketField(_TypedIRRecord):
    id: str | None = None
    start: int | None = None
    end: int | None = None
    label: str | None = None
    name: str | None = None


class _PacketIR(_TypedIRRoot):
    fields: list[_PacketField]


class _SpecialHierarchyLeaf(_TypedIRRecord):
    id: str | None = None
    label: str | None = None
    name: str | None = None


class _SpecialHierarchyNode(_SpecialHierarchyLeaf):
    children: list[_SpecialHierarchyNode] = Field(default_factory=list)


class _IshikawaIR(_TypedIRRoot):
    effect: _SpecialHierarchyLeaf
    categories: list[_SpecialHierarchyNode]


class _TreeViewIR(_TypedIRRoot):
    root: _SpecialHierarchyNode


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
        (("states", "list"), ("transitions", "list")),
        guidance="states and transitions",
        nested_model=_StateIR,
        prompt_records=(
            "states[]: {id:string,label:string,kind:state|choice|fork|join,"
            "bbox:number[4],evidence_ids:string[]}",
            "transitions[]: {id:string,source:string,target:string,label:string,"
            "bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "class": TypedIRContract(
        (("classes", "list"),),
        ("relations",),
        "classes, members, relations",
        _ClassIR,
        (
            "classes[]: {id:string,label:string,bbox:number[4],evidence_ids:string[],"
            "members:member[]}",
            "classes[].members[]: {name:string,type:string,visibility:+|-|#|~,"
            "kind:field|method,parameters:string[],return_type:string,"
            "classifier:static|abstract,bbox:number[4],evidence_ids:string[]}",
            "relations[]: {id:string,source:string,target:string,"
            "type:association|dependency|aggregation|composition|link|inheritance|realization,"
            "label:string,source_cardinality:string,target_cardinality:string,"
            "bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "er": TypedIRContract(
        (("entities", "list"),),
        ("relationships",),
        "entities and cardinalities",
        _ERIR,
        (
            "entities[]: {id:string,label:string,bbox:number[4],evidence_ids:string[],"
            "attributes:attribute[]}",
            "entities[].attributes[]: {type:string,name:string,keys:(PK|FK|UK)[],"
            "comment:string,bbox:number[4],evidence_ids:string[]}",
            "relationships[]: {id:string,source:string,target:string,"
            "source_cardinality:one|only_one|zero_or_one|one_or_more|zero_or_more,"
            "target_cardinality:one|only_one|zero_or_one|one_or_more|zero_or_more,"
            "identifying:boolean,label:string,bbox:number[4],evidence_ids:string[]}",
        ),
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
        (("elements", "list"),),
        ("boundaries", "relations", "level"),
        "C4 elements",
        _C4IR,
        (
            "level: context|container|component",
            "elements[]: {id:string,label:string,name:string,kind:person|external_person|"
            "system|external_system|database|external_database|queue|external_queue|"
            "container|container_database|container_queue|component|component_database|"
            "component_queue,boundary:string,description:string,technology:string,"
            "bbox:number[4],evidence_ids:string[]}",
            "boundaries[]: {id:string,label:string,type:string,bbox:number[4],"
            "evidence_ids:string[]}",
            "relations[]: {id:string,source:string,target:string,label:string,"
            "technology:string,bidirectional:boolean,source_side:L|R|T|B,"
            "target_side:L|R|T|B,bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "requirement": TypedIRContract(
        (("requirements", "list"),),
        ("elements", "relations"),
        "requirements and relations",
        _RequirementIR,
        (
            "requirements[]: {id:string,requirement_id:string,text:string,label:string,"
            "type:requirement|functional|functional_requirement|interface|"
            "interface_requirement|performance|performance_requirement|physical|"
            "physical_requirement|design_constraint,risk:low|medium|high,"
            "verify_method:analysis|demonstration|inspection|test,bbox:number[4],"
            "evidence_ids:string[]}",
            "elements[]: {id:string,type:string,label:string,docref:string,bbox:number[4],"
            "evidence_ids:string[]}",
            "relations[]: {id:string,source:string,target:string,"
            "type:contains|copies|derives|satisfies|verifies|refines|traces,"
            "bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "block": TypedIRContract(
        (("blocks", "list"),),
        ("edges", "columns"),
        "blocks and connections",
        _BlockIR,
        (
            "columns: auto|integer",
            "blocks[]: {id:string,label:string,text:string,"
            "shape:rectangle|round|stadium|circle|diamond|hexagon|cylinder|subroutine,"
            "bbox:number[4],evidence_ids:string[]}",
            "edges[]: {id:string,source:string,target:string,label:string,style:string,"
            "bidirectional:boolean,bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "deployment": TypedIRContract(
        (("nodes", "list"),),
        ("artifacts", "groups", "links"),
        "deployment nodes/artifacts",
        _DeploymentIR,
        (
            "nodes[]: {id:string,label:string,name:string,icon:string,group:string,"
            "bbox:number[4],evidence_ids:string[]}",
            "artifacts[]: {id:string,label:string,name:string,icon:string,group:string,"
            "bbox:number[4],evidence_ids:string[]}",
            "groups[]: {id:string,label:string,icon:string,bbox:number[4],evidence_ids:string[]}",
            "links[]: {id:string,source:string,target:string,label:string,"
            "bidirectional:boolean,source_side:L|R|T|B,target_side:L|R|T|B,"
            "bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "component": TypedIRContract(
        (("components", "list"),),
        ("interfaces", "groups", "dependencies"),
        "components and interfaces",
        _ComponentIR,
        (
            "components[]: {id:string,label:string,name:string,icon:string,group:string,"
            "bbox:number[4],evidence_ids:string[]}",
            "interfaces[]: {id:string,label:string,name:string,icon:string,group:string,"
            "bbox:number[4],evidence_ids:string[]}",
            "groups[]: {id:string,label:string,icon:string,bbox:number[4],evidence_ids:string[]}",
            "dependencies[]: {id:string,source:string,target:string,label:string,"
            "bidirectional:boolean,source_side:L|R|T|B,target_side:L|R|T|B,"
            "bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "usecase": TypedIRContract(
        (("actors", "list"), ("use_cases", "list")),
        ("relations",),
        "actors and use cases",
        _UsecaseIR,
        (
            "actors[]: {id:string,label:string,name:string,bbox:number[4],evidence_ids:string[]}",
            "use_cases[]: {id:string,label:string,name:string,bbox:number[4],"
            "evidence_ids:string[]}",
            "relations[]: {id:string,source:string,target:string,type:string,label:string,"
            "bbox:number[4],evidence_ids:string[]}",
        ),
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
    "journey": TypedIRContract(
        (("sections", "list"),),
        guidance="sections with scored tasks",
        nested_model=_JourneyIR,
        prompt_records=(
            "sections[]: {title:string,bbox:number[4],evidence_ids:string[],tasks:task[]}",
            "sections[].tasks[]: {id:string,label:string,score:integer,actors:string[],"
            "bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "kanban": TypedIRContract(
        (("columns", "list"), ("cards", "list")),
        guidance="columns and assigned cards",
        nested_model=_KanbanIR,
        prompt_records=(
            "columns[]: {id:string,label:string,bbox:number[4],evidence_ids:string[]}",
            "cards[]: {id:string,label:string,column_id:string,bbox:number[4],"
            "evidence_ids:string[]}",
        ),
    ),
    "gitgraph": TypedIRContract(
        (("initial_branch", "string"), ("operations", "list")),
        guidance="ordered branch/commit/merge operations",
        nested_model=_GitGraphIR,
        prompt_records=(
            "initial_branch: main",
            "direction: LR|TB|BT",
            "operations[]: {type:commit|branch|merge,id:string,branch:string,name:string,"
            "from:string,source:string,target:string,tag:string,"
            "commit_type:NORMAL|REVERSE|HIGHLIGHT,order:integer,bbox:number[4],"
            "evidence_ids:string[]}",
        ),
    ),
    "pie": TypedIRContract(
        (("slices", "list"),),
        ("show_data",),
        "labels with explicit values",
        _PieIR,
        (
            "show_data: boolean",
            "slices[]: {label:string,value:number,bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "xychart": TypedIRContract(
        (("x_axis", "object"), ("y_axis", "object"), ("series", "list")),
        guidance="explicit axes and series values",
        nested_model=_XYChartIR,
        prompt_records=(
            "x_axis: {label:string,categories:string[],min:number,max:number,"
            "bbox:number[4],evidence_ids:string[]}",
            "y_axis: {label:string,min:number,max:number,bbox:number[4],evidence_ids:string[]}",
            "series[]: {kind:line|bar,values:number[],points:point[],bbox:number[4],"
            "evidence_ids:string[]}",
            "series[].points[]: {x:number,y:number,bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "quadrant": TypedIRContract(
        (("x_axis", "object"), ("y_axis", "object"), ("points", "list")),
        ("quadrants",),
        "normalized positioned points",
        _QuadrantIR,
        (
            "x_axis: {low:string,high:string,bbox:number[4],evidence_ids:string[]}",
            "y_axis: {low:string,high:string,bbox:number[4],evidence_ids:string[]}",
            "quadrants: string[4]|{quadrant-1:string,quadrant-2:string,"
            "quadrant-3:string,quadrant-4:string}",
            "points[]: {label:string,x:number,y:number,bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "sankey": TypedIRContract(
        (("nodes", "list"), ("flows", "list")),
        guidance="weighted directed flows",
        nested_model=_SankeyIR,
        prompt_records=(
            "nodes[]: {id:string,label:string,bbox:number[4],evidence_ids:string[]}",
            "flows[]: {id:string,source:string,target:string,value:number,bbox:number[4],"
            "evidence_ids:string[]}",
        ),
    ),
    "radar": TypedIRContract(
        (("dimensions", "list"), ("series", "list")),
        ("min", "max", "ticks", "show_legend", "graticule"),
        "dimensions and explicit values",
        _RadarIR,
        (
            "dimensions[]: {id:string,label:string,bbox:number[4],evidence_ids:string[]}",
            "series[]: {id:string,label:string,values:number[],bbox:number[4],"
            "evidence_ids:string[]}",
            "min: number",
            "max: number",
            "ticks: integer",
            "show_legend: boolean",
            "graticule: circle|polygon",
        ),
    ),
    "treemap": TypedIRContract(
        (("root", "object"),),
        guidance="valued hierarchy",
        nested_model=_TreemapIR,
        prompt_records=(
            "root: {id:string,label:string,value:number,bbox:number[4],"
            "evidence_ids:string[],children:self[]}",
        ),
    ),
    "venn": TypedIRContract(
        (("sets", "list"), ("intersections", "list")),
        guidance="sets and explicit intersections",
        nested_model=_VennIR,
        prompt_records=(
            "sets[]: {id:string,label:string,value:number,bbox:number[4],evidence_ids:string[]}",
            "intersections[]: {id:string,sets:string[],label:string,value:number,"
            "bbox:number[4],evidence_ids:string[]}",
        ),
    ),
    "packet": TypedIRContract(
        (("fields", "list"),),
        guidance="explicit bit ranges",
        nested_model=_PacketIR,
        prompt_records=(
            "fields[]: {id:string,start:integer,end:integer,label:string,bbox:number[4],"
            "evidence_ids:string[]}",
        ),
    ),
    "ishikawa": TypedIRContract(
        (("effect", "object"), ("categories", "list")),
        guidance="effect/category/cause tree",
        nested_model=_IshikawaIR,
        prompt_records=(
            "effect: {id:string,label:string,bbox:number[4],evidence_ids:string[]}",
            "categories[]: {id:string,label:string,bbox:number[4],"
            "evidence_ids:string[],children:self[]}",
        ),
    ),
    "wardley": TypedIRContract(
        (("components", "list"),), ("links",), "components with explicit x/y coordinates"
    ),
    "cynefin": TypedIRContract((("domains", "list"),), ("transitions",), "named domains and items"),
    "treeview": TypedIRContract(
        (("root", "object"),),
        guidance="rooted hierarchy",
        nested_model=_TreeViewIR,
        prompt_records=(
            "root: {id:string,label:string,bbox:number[4],evidence_ids:string[],children:self[]}",
        ),
    ),
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
CORE_UML_NESTED_TYPES = frozenset({"state", "class", "er"})
PHASE_TWO_NATIVE_NESTED_TYPES = frozenset({"requirement", "block"})
PHASE_TWO_FALLBACK_NESTED_TYPES = frozenset({"c4", "component", "deployment", "usecase"})
PHASE_THREE_CORE_NESTED_TYPES = frozenset({"pie", "quadrant", "xychart"})
PHASE_THREE_EXTENDED_NESTED_TYPES = frozenset({"sankey", "radar", "treemap", "venn"})
PLANNING_NESTED_TYPES = frozenset({"journey", "kanban", "gitgraph"})
SPECIAL_NATIVE_NESTED_TYPES = frozenset({"packet", "ishikawa", "treeview"})
NESTED_TYPED_IR_TYPES = (
    PHASE_ONE_NESTED_TYPES
    | CORE_UML_NESTED_TYPES
    | PHASE_TWO_NATIVE_NESTED_TYPES
    | PHASE_TWO_FALLBACK_NESTED_TYPES
    | PHASE_THREE_CORE_NESTED_TYPES
    | PHASE_THREE_EXTENDED_NESTED_TYPES
    | PLANNING_NESTED_TYPES
    | SPECIAL_NATIVE_NESTED_TYPES
)

for _diagram_type in NESTED_TYPED_IR_TYPES:  # pragma: no cover - import-time invariant
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
