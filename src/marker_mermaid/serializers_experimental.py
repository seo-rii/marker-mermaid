"""Evidence-strict Wardley, Cynefin, Railroad, and ZenUML serializers.

The pinned Mermaid 11.16 runtime natively supports the first three grammars.
ZenUML is not bundled, so its sequence-like IR is emitted as an explicit
``sequenceDiagram`` fallback instead of pretending that native syntax rendered.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from marker_mermaid.accessibility import enrich_accessibility_ir, resolve_accessibility
from marker_mermaid.models import MAX_ID_CHARS
from marker_mermaid.serialization import SerializationResult
from marker_mermaid.serializers import SerializationError, serialize_flowchart
from marker_mermaid.serializers_special import _neutralize_active_text

MAX_ITEMS = 500
MAX_DEPTH = 20
MAX_TEXT_LENGTH = 500
MAX_EXPERIMENTAL_OUTPUT_CHARS = 50_000
MAX_EXPERIMENTAL_OUTPUT_LINES = 5_000
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}\Z")
_DOMAINS = {"complex", "complicated", "clear", "chaotic", "confusion"}
CYNEFIN_RUNTIME_TEMPLATE_ELEMENTS: tuple[tuple[str, str, str], ...] = (
    ("cynefin_domain_complex", "domain", "Complex"),
    ("cynefin_domain_complicated", "domain", "Complicated"),
    ("cynefin_domain_chaotic", "domain", "Chaotic"),
    ("cynefin_domain_clear", "domain", "Clear"),
    ("cynefin_domain_confusion", "domain", "Confusion"),
    ("cynefin_runtime_complex_flow", "runtime_template", "Probe → Sense → Respond"),
    ("cynefin_runtime_complex_practice", "runtime_template", "Emergent Practices"),
    (
        "cynefin_runtime_complicated_flow",
        "runtime_template",
        "Sense → Analyse → Respond",
    ),
    ("cynefin_runtime_complicated_practice", "runtime_template", "Good Practices"),
    ("cynefin_runtime_chaotic_flow", "runtime_template", "Act → Sense → Respond"),
    ("cynefin_runtime_chaotic_practice", "runtime_template", "Novel Practices"),
    ("cynefin_runtime_clear_flow", "runtime_template", "Sense → Categorise → Respond"),
    ("cynefin_runtime_clear_practice", "runtime_template", "Best Practices"),
    ("cynefin_runtime_confusion_label", "runtime_template", "Disorder"),
)
_ENTITY_LITERAL = re.compile(
    r"&(?P<body>#[0-9]+|#x[0-9A-F]+|[A-Z][A-Z0-9]+);",
    re.IGNORECASE,
)
_ENTITY_COMPATIBILITY_WARNING = (
    "Entity-like literal text uses visible fullwidth ampersand and number-sign glyphs "
    "(＆ and ＃) because Mermaid 11.16 cannot preserve every literal entity form."
)
_ZENUML_COMPATIBILITY_WARNING = (
    "ZenUML sequence fallback uses visible compatibility glyphs for "
    "grammar-conflicting label characters."
)
_DATA_LINEAGE_COMPATIBILITY_WARNING = (
    "Data Lineage Flowchart fallback uses visible compatibility glyphs for "
    "grammar-conflicting label characters."
)


@dataclass(frozen=True, slots=True)
class WardleyComponentPlan:
    """One explicitly positioned Wardley component and its visible identity token."""

    source_record: Mapping[str, Any]
    source_id: str
    label: str
    semantic_label: str
    kind: str
    x: float
    y: float
    x_token: str
    y_token: str
    token: str


@dataclass(frozen=True, slots=True)
class WardleyLinkPlan:
    """One resolved Wardley link using the exact component tokens Mermaid receives."""

    source_record: Mapping[str, Any]
    source_id: str
    target_id: str
    source_token: str
    target_token: str
    label: str | None
    semantic_label: str | None


@dataclass(frozen=True, slots=True)
class WardleyPlan:
    """Validated Wardley records shared by serialization and generated Scene projection."""

    title: str | None
    semantic_title: str | None
    components: tuple[WardleyComponentPlan, ...]
    links: tuple[WardleyLinkPlan, ...]
    compatibility_substituted: bool


@dataclass(frozen=True, slots=True)
class CynefinItemPlan:
    """One Cynefin item with stable emitted identity and source-visible text."""

    source_record: Mapping[str, Any] | None
    emitted_id: str
    label: str
    semantic_label: str


@dataclass(frozen=True, slots=True)
class CynefinDomainPlan:
    """One canonical Cynefin domain and its ordered items."""

    source_record: Mapping[str, Any]
    name: str
    emitted_id: str
    group_id: str
    items: tuple[CynefinItemPlan, ...]


@dataclass(frozen=True, slots=True)
class CynefinTransitionPlan:
    """One explicit transition between two emitted Cynefin domain identities."""

    source_record: Mapping[str, Any]
    emitted_id: str
    source_name: str
    target_name: str
    source_emitted_id: str
    target_emitted_id: str
    label: str | None
    semantic_label: str | None


@dataclass(frozen=True, slots=True)
class CynefinPlan:
    """Validated Cynefin records shared by serialization and generated Scene projection."""

    domains: tuple[CynefinDomainPlan, ...]
    transitions: tuple[CynefinTransitionPlan, ...]
    compatibility_substituted: bool


@dataclass(frozen=True, slots=True)
class CynefinRuntimeItemPlan:
    """One item label that Mermaid 11.16 actually exposes in the Cynefin SVG."""

    source_record: Mapping[str, Any] | None
    emitted_id: str
    label: str
    implicit: bool = False


@dataclass(frozen=True, slots=True)
class ZenUMLParticipantPlan:
    """One sequence-fallback participant with a grammar-safe emitted identity."""

    source_record: Mapping[str, Any] | None
    source_id: str
    emitted_id: str
    label: str
    semantic_label: str


@dataclass(frozen=True, slots=True)
class ZenUMLMessagePlan:
    """One ordered message resolved to exact sequence-fallback endpoints."""

    source_record: Mapping[str, Any]
    emitted_id: str
    source_id: str
    target_id: str
    source_emitted_id: str
    target_emitted_id: str
    label: str
    semantic_label: str


@dataclass(frozen=True, slots=True)
class ZenUMLPlan:
    """Validated ZenUML evidence shared by sequence serialization and Scene projection."""

    participants: tuple[ZenUMLParticipantPlan, ...]
    messages: tuple[ZenUMLMessagePlan, ...]
    compatibility_substituted: bool


@dataclass(frozen=True, slots=True)
class OrganizationNodePlan:
    """One explicit reporting node projected to TreeView and Flowchart fallbacks."""

    source_record: Mapping[str, Any]
    source_id: str
    emitted_id: str
    label: str
    semantic_label: str
    depth: int
    parent_source_id: str | None
    parent_emitted_id: str | None


@dataclass(frozen=True, slots=True)
class OrganizationRelationPlan:
    """One parent-child relation encoded by hierarchy nesting in source evidence."""

    source_record: Mapping[str, Any]
    emitted_id: str
    source_id: str
    target_id: str
    source_emitted_id: str
    target_emitted_id: str


@dataclass(frozen=True, slots=True)
class OrganizationPlan:
    """Bounded hierarchy shared by Organization serialization and Scene projection."""

    nodes: tuple[OrganizationNodePlan, ...]
    relations: tuple[OrganizationRelationPlan, ...]
    direction: str
    compatibility_substituted: bool


@dataclass(frozen=True, slots=True)
class DataLineageNodePlan:
    """One dataset or process with its exact Flowchart fallback identity and label."""

    source_record: Mapping[str, Any]
    source_id: str
    emitted_id: str
    kind: str
    shape: str
    label: str
    semantic_label: str


@dataclass(frozen=True, slots=True)
class DataLineageRelationPlan:
    """One explicit lineage relation resolved to exact fallback endpoints."""

    source_record: Mapping[str, Any]
    emitted_id: str
    source_id: str
    target_id: str
    source_emitted_id: str
    target_emitted_id: str
    label: str | None
    semantic_label: str | None


@dataclass(frozen=True, slots=True)
class DataLineagePlan:
    """Bounded Flowchart projection shared by serialization and Scene consumers."""

    nodes: tuple[DataLineageNodePlan, ...]
    relations: tuple[DataLineageRelationPlan, ...]
    direction: str
    compatibility_substituted: bool


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SerializationError(f"{field} must be a non-empty string")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value):
        raise SerializationError(
            f"{field} contains unsupported control or format characters, including surrogates"
        )
    normalized = " ".join(value.strip().split())
    if len(normalized) > MAX_TEXT_LENGTH:
        raise SerializationError(f"{field} exceeds the safe text limit")
    return normalized


def _quoted(value: Any, field: str) -> str:
    return json.dumps(_text(value, field), ensure_ascii=False)


def _identifier(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _IDENTIFIER.fullmatch(text):
        raise SerializationError(f"{field} must be a safe Mermaid identifier")
    return text


def _exact_identifier(value: Any, field: str) -> str:
    text = _identifier(value, field)
    if value != text:
        raise SerializationError(f"{field} must not require whitespace normalization")
    return text


def _register_emitted_identifier(
    source_id: str,
    prefix: str,
    source_ids: set[str],
    emitted_ids: set[str],
    *,
    field: str,
) -> str:
    if source_id in source_ids:
        raise SerializationError(f"duplicate {field} id: {source_id}")
    emitted_id = f"{prefix}{source_id.replace('-', '_')}"
    if emitted_id in emitted_ids:
        raise SerializationError(
            f"{field} ids are ambiguous after Mermaid normalization: {source_id}"
        )
    if len(emitted_id) > MAX_ID_CHARS:
        raise SerializationError(f"{field} id exceeds the emitted identifier limit")
    source_ids.add(source_id)
    emitted_ids.add(emitted_id)
    return emitted_id


def _validate_accessibility_inputs(ir: Mapping[str, Any]) -> None:
    for field in ("title", "description", "acc_title", "acc_description"):
        if ir.get(field) is not None:
            _text(ir[field], field)


def _entity_compatibility_text(text: str) -> tuple[str, bool]:
    """Keep entity-like evidence visible instead of allowing SVG entity decoding."""

    substituted = _ENTITY_LITERAL.search(text) is not None
    return (
        _ENTITY_LITERAL.sub(
            lambda match: (
                f"＆＃{match.group('body')[1:]};"
                if match.group("body").startswith("#")
                else f"＆{match.group('body')};"
            ),
            text,
        ),
        substituted,
    )


def _flowchart_visible_text(
    value: Any,
    field: str,
    *,
    edge_label: bool = False,
    accessibility: bool = False,
) -> tuple[str, str, bool]:
    """Return source semantics and exact visible fallback glyphs for one label."""

    semantic = _text(value, field)
    compatible, entity_substituted = _entity_compatibility_text(semantic)
    visible = compatible.replace('"', "″").replace("\\", "∖")
    if edge_label:
        visible = (
            visible.replace("|", "∣")
            .replace(";", "⁏")
            .replace("(", "❨")
            .replace(")", "❩")
            .replace("[", "⟦")
            .replace("]", "⟧")
            .replace("{", "⦃")
            .replace("}", "⦄")
            .replace("@", "＠")
        )
    if accessibility:
        visible = visible.replace("<", "〈").replace(">", "〉")
    return semantic, visible, entity_substituted or visible != semantic


def _zenuml_visible_text(
    value: Any, field: str, *, sequence_statement: bool = True
) -> tuple[str, str, bool]:
    """Return normalized evidence and the exact glyphs visible in sequence SVG text."""

    semantic = _text(value, field)
    compatible, entity_substituted = _entity_compatibility_text(semantic)
    visible = compatible.replace("#", "＃")
    if sequence_statement:
        visible = visible.replace(";", "⁏")
    else:
        visible = visible.replace("<", "〈").replace(">", "〉")
    return semantic, visible, entity_substituted or visible != semantic


def _accessibility(ir: Mapping[str, Any], diagram_type: str, *, experimental: bool) -> list[str]:
    resolved = resolve_accessibility(ir, diagram_type, experimental=experimental)
    return [
        f"accTitle: {_text(resolved.title, 'accessible title')}",
        f"accDescr: {_text(resolved.description, 'accessible description')}",
    ]


def _compatible_accessibility(
    ir: Mapping[str, Any], diagram_type: str, *, experimental: bool
) -> tuple[list[str], bool]:
    resolved = resolve_accessibility(ir, diagram_type, experimental=experimental)
    title, title_substituted = _entity_compatibility_text(_text(resolved.title, "accessible title"))
    description, description_substituted = _entity_compatibility_text(
        _text(resolved.description, "accessible description")
    )
    return (
        [f"accTitle: {title}", f"accDescr: {description}"],
        title_substituted or description_substituted,
    )


def _preflight_experimental_code(code: str, *, diagram_type: str) -> str:
    """Apply CandidateValidator's default source budgets before returning code."""

    if code.count("\n") + 1 > MAX_EXPERIMENTAL_OUTPUT_LINES:
        raise SerializationError(
            f"{diagram_type} output exceeds source-line limit of {MAX_EXPERIMENTAL_OUTPUT_LINES}"
        )
    if len(code) > MAX_EXPERIMENTAL_OUTPUT_CHARS:
        raise SerializationError(
            f"{diagram_type} output exceeds source-character limit of "
            f"{MAX_EXPERIMENTAL_OUTPUT_CHARS}"
        )
    return code


def _coordinate(value: Any, field: str) -> tuple[float, str]:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SerializationError(f"{field} must be an explicit numeric coordinate")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise SerializationError(f"{field} must be between 0 and 1")
    if number == 0:
        number = 0.0
    rendered = format(number, ".15g")
    if "e" in rendered.casefold():
        rendered = format(Decimal(rendered), "f")
    if "." not in rendered:
        rendered = f"{rendered}.0"
    return float(rendered), rendered


def plan_wardley_records(
    ir: Mapping[str, Any],
) -> WardleyPlan:
    """Validate and normalize the exact title, components, and links Wardley emits."""

    semantic_title = _text(ir["title"], "title") if ir.get("title") is not None else None
    title, title_substituted = (
        _entity_compatibility_text(semantic_title) if semantic_title is not None else (None, False)
    )
    components = ir.get("components")
    if not isinstance(components, list) or not components:
        raise SerializationError("wardley IR requires components")
    if len(components) > MAX_ITEMS:
        raise SerializationError("wardley component limit exceeded")
    tokens: dict[str, str] = {}
    labels: set[str] = set()
    normalized_components: list[WardleyComponentPlan] = []
    compatibility_substituted = title_substituted
    for index, component in enumerate(components):
        if not isinstance(component, Mapping):
            raise SerializationError("wardley components must be objects")
        component_id = _identifier(component.get("id"), f"components[{index}].id")
        if component_id in tokens:
            raise SerializationError(f"duplicate Wardley component id: {component_id}")
        semantic_label = _text(component.get("label", component_id), f"components[{index}].label")
        label, label_substituted = _entity_compatibility_text(semantic_label)
        if label in labels:
            raise SerializationError(f"duplicate Wardley component label: {label}")
        labels.add(label)
        compatibility_substituted = compatibility_substituted or label_substituted
        token = json.dumps(label, ensure_ascii=False)
        tokens[component_id] = token
        anchor = component.get("anchor")
        if anchor is not None and type(anchor) is not bool:
            raise SerializationError(f"components[{index}].anchor must be a boolean or null")
        kind = "anchor" if anchor is True else "component"
        x, x_token = _coordinate(component.get("x"), f"components[{index}].x")
        y, y_token = _coordinate(component.get("y"), f"components[{index}].y")
        normalized_components.append(
            WardleyComponentPlan(
                source_record=component,
                source_id=component_id,
                label=label,
                semantic_label=semantic_label,
                kind=kind,
                x=x,
                y=y,
                x_token=x_token,
                y_token=y_token,
                token=token,
            )
        )
    links = ir.get("links", [])
    if not isinstance(links, list) or len(links) > MAX_ITEMS:
        raise SerializationError("wardley links must be a bounded list")
    seen_links: set[tuple[str, str]] = set()
    normalized_links: list[WardleyLinkPlan] = []
    for index, link in enumerate(links):
        if not isinstance(link, Mapping):
            raise SerializationError("wardley links must be objects")
        source = _identifier(link.get("source"), f"links[{index}].source")
        target = _identifier(link.get("target"), f"links[{index}].target")
        if source not in tokens or target not in tokens:
            raise SerializationError(f"Wardley link {source}->{target} has an unresolved endpoint")
        if source == target or (source, target) in seen_links:
            raise SerializationError(f"duplicate or self Wardley link: {source}->{target}")
        seen_links.add((source, target))
        label = None
        semantic_label = None
        if link.get("label") is not None:
            semantic_label = _text(link["label"], f"links[{index}].label")
            if ";" in _ENTITY_LITERAL.sub("", semantic_label):
                raise SerializationError("Wardley link labels cannot contain separators")
            label, label_substituted = _entity_compatibility_text(semantic_label)
            compatibility_substituted = compatibility_substituted or label_substituted
        normalized_links.append(
            WardleyLinkPlan(
                source_record=link,
                source_id=source,
                target_id=target,
                source_token=tokens[source],
                target_token=tokens[target],
                label=label,
                semantic_label=semantic_label,
            )
        )
    return WardleyPlan(
        title=title,
        semantic_title=semantic_title,
        components=tuple(normalized_components),
        links=tuple(normalized_links),
        compatibility_substituted=compatibility_substituted,
    )


def serialize_wardley(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    """Serialize explicitly positioned components without inferring coordinates."""

    plan = plan_wardley_records(ir)
    accessibility_ir = dict(ir)
    accessibility_ir["links"] = []
    accessibility_lines, accessibility_substituted = _compatible_accessibility(
        accessibility_ir, "wardley", experimental=experimental
    )
    lines = ["wardley-beta", *accessibility_lines]
    if plan.title is not None:
        lines.append(f"title {plan.title}")
    lines.extend(
        f"{component.kind} {component.token} [{component.y_token}, {component.x_token}]"
        for component in plan.components
    )
    for link in plan.links:
        suffix = f"; {link.label}" if link.label is not None else ""
        lines.append(f"{link.source_token} -> {link.target_token}{suffix}")
    code = _preflight_experimental_code("\n".join(lines) + "\n", diagram_type="wardley")
    warnings = (
        (_ENTITY_COMPATIBILITY_WARNING,)
        if plan.compatibility_substituted or accessibility_substituted
        else ()
    )
    return SerializationResult.native("wardley", code, warnings=warnings, stability="experimental")


def plan_cynefin_records(ir: Mapping[str, Any]) -> CynefinPlan:
    """Validate and normalize the exact domains, items, and transitions Cynefin emits."""

    domains = ir.get("domains")
    if not isinstance(domains, list) or not domains:
        raise SerializationError("cynefin IR requires domains")
    if len(domains) > len(_DOMAINS):
        raise SerializationError("cynefin has at most five domains")
    defined: dict[str, str] = {}
    normalized_domains: list[CynefinDomainPlan] = []
    item_count = 0
    compatibility_substituted = False
    for index, domain in enumerate(domains):
        if not isinstance(domain, Mapping):
            raise SerializationError("cynefin domains must be objects")
        name = _text(domain.get("name"), f"domains[{index}].name").casefold()
        if name not in _DOMAINS or name in defined:
            raise SerializationError(f"invalid or duplicate Cynefin domain: {name}")
        items = domain.get("items")
        if not isinstance(items, list) or not items:
            raise SerializationError(f"Cynefin domain {name} requires at least one item")
        item_count += len(items)
        if item_count > MAX_ITEMS:
            raise SerializationError("cynefin item limit exceeded")
        emitted_id = f"cynefin_domain_{name}"
        defined[name] = emitted_id
        normalized_items: list[CynefinItemPlan] = []
        for item_index, item in enumerate(items, start=1):
            source_record = item if isinstance(item, Mapping) else None
            value = item.get("label") if source_record is not None else item
            semantic_label = _text(value, f"domains[{index}].items[{item_index - 1}]")
            label, label_substituted = _entity_compatibility_text(semantic_label)
            compatibility_substituted = compatibility_substituted or label_substituted
            normalized_items.append(
                CynefinItemPlan(
                    source_record=source_record,
                    emitted_id=f"cynefin_item_{name}_{item_index}",
                    label=label,
                    semantic_label=semantic_label,
                )
            )
        normalized_domains.append(
            CynefinDomainPlan(
                source_record=domain,
                name=name,
                emitted_id=emitted_id,
                group_id=f"cynefin_group_{name}",
                items=tuple(normalized_items),
            )
        )
    transitions = ir.get("transitions", [])
    if not isinstance(transitions, list) or len(transitions) > MAX_ITEMS:
        raise SerializationError("cynefin transitions must be a bounded list")
    seen: set[tuple[str, str, str | None]] = set()
    normalized_transitions: list[CynefinTransitionPlan] = []
    for index, transition in enumerate(transitions, start=1):
        if not isinstance(transition, Mapping):
            raise SerializationError("cynefin transitions must be objects")
        source = _text(transition.get("source"), f"transitions[{index - 1}].source").casefold()
        target = _text(transition.get("target"), f"transitions[{index - 1}].target").casefold()
        if source not in defined or target not in defined or source == target:
            raise SerializationError(f"invalid Cynefin transition: {source}->{target}")
        label = None
        semantic_label = None
        if transition.get("label") is not None:
            semantic_label = _text(transition.get("label"), f"transitions[{index - 1}].label")
            label, label_substituted = _entity_compatibility_text(semantic_label)
            compatibility_substituted = compatibility_substituted or label_substituted
        key = (source, target, label)
        if key in seen:
            raise SerializationError(f"duplicate Cynefin transition: {source}->{target}")
        seen.add(key)
        normalized_transitions.append(
            CynefinTransitionPlan(
                source_record=transition,
                emitted_id=f"cynefin_transition_{index}",
                source_name=source,
                target_name=target,
                source_emitted_id=defined[source],
                target_emitted_id=defined[target],
                label=label,
                semantic_label=semantic_label,
            )
        )
    return CynefinPlan(
        domains=tuple(normalized_domains),
        transitions=tuple(normalized_transitions),
        compatibility_substituted=compatibility_substituted,
    )


def plan_cynefin_runtime_items(
    plan: CynefinPlan,
) -> dict[str, tuple[CynefinRuntimeItemPlan, ...]]:
    """Project source items to the exact item labels Mermaid 11.16 renders."""

    projected: dict[str, tuple[CynefinRuntimeItemPlan, ...]] = {}
    for domain in plan.domains:
        visible_items = domain.items[:3] if domain.name == "confusion" else domain.items
        items = [
            CynefinRuntimeItemPlan(
                source_record=item.source_record,
                emitted_id=item.emitted_id,
                label=item.label,
            )
            for item in visible_items
        ]
        hidden_count = len(domain.items) - len(visible_items)
        if hidden_count:
            items.append(
                CynefinRuntimeItemPlan(
                    source_record=None,
                    emitted_id="cynefin_runtime_confusion_more",
                    label=f"+{hidden_count} more",
                    implicit=True,
                )
            )
        projected[domain.emitted_id] = tuple(items)
    return projected


def serialize_cynefin(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    plan = plan_cynefin_records(ir)
    accessibility_lines, accessibility_substituted = _compatible_accessibility(
        ir, "cynefin", experimental=experimental
    )
    lines = ["cynefin-beta", *accessibility_lines]
    for domain in plan.domains:
        lines.append(domain.name)
        lines.extend(f"  {json.dumps(item.label, ensure_ascii=False)}" for item in domain.items)
    for transition in plan.transitions:
        suffix = (
            f" : {json.dumps(transition.label, ensure_ascii=False)}" if transition.label else ""
        )
        lines.append(f"{transition.source_name} --> {transition.target_name}{suffix}")
    code = _preflight_experimental_code("\n".join(lines) + "\n", diagram_type="cynefin")
    warnings = (
        (_ENTITY_COMPATIBILITY_WARNING,)
        if plan.compatibility_substituted or accessibility_substituted
        else ()
    )
    return SerializationResult.native("cynefin", code, warnings=warnings, stability="experimental")


def _railroad_expression(
    value: Any,
    *,
    rule_names: set[str],
    depth: int,
    counter: list[int],
) -> str:
    if depth > MAX_DEPTH:
        raise SerializationError("railroad expression nesting is too deep")
    if not isinstance(value, Mapping):
        raise SerializationError("railroad expressions must be objects")
    counter[0] += 1
    if counter[0] > MAX_ITEMS:
        raise SerializationError("railroad expression limit exceeded")
    kind = value.get("type")
    if kind == "terminal":
        return f"terminal({_quoted(value.get('value'), 'terminal value')})"
    if kind == "nonterminal":
        name = _identifier(value.get("name"), "nonterminal name")
        if name not in rule_names:
            raise SerializationError(f"unresolved railroad nonterminal: {name}")
        return f"nonterminal({json.dumps(name)})"
    if kind == "special":
        return f"special({_quoted(value.get('text'), 'special text')})"
    collection_key = {"sequence": "elements", "choice": "alternatives"}.get(str(kind))
    if collection_key is not None:
        children = value.get(collection_key)
        if not isinstance(children, list) or not children:
            raise SerializationError(f"railroad {kind} requires {collection_key}")
        rendered = [
            _railroad_expression(child, rule_names=rule_names, depth=depth + 1, counter=counter)
            for child in children
        ]
        return f"{kind}({', '.join(rendered)})"
    unary_name = {
        "optional": "optional",
        "one_or_more": "oneOrMore",
        "zero_or_more": "zeroOrMore",
    }.get(str(kind))
    if unary_name is not None:
        child = _railroad_expression(
            value.get("element"), rule_names=rule_names, depth=depth + 1, counter=counter
        )
        return f"{unary_name}({child})"
    raise SerializationError(f"unsupported railroad expression type: {kind!r}")


def serialize_railroad(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    rules = ir.get("rules")
    if not isinstance(rules, list) or not rules or len(rules) > MAX_ITEMS:
        raise SerializationError("railroad IR requires a bounded non-empty rules list")
    names: list[str] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            raise SerializationError("railroad rules must be objects")
        names.append(_identifier(rule.get("name"), f"rules[{index}].name"))
    if len(names) != len(set(names)):
        raise SerializationError("railroad rule names must be unique")
    lines = ["railroad-beta", *_accessibility(ir, "railroad", experimental=experimental)]
    if ir.get("title") is not None:
        lines.append(f"title {_text(ir['title'], 'title')}")
    counter = [0]
    known = set(names)
    for name, rule in zip(names, rules, strict=True):
        expression = _railroad_expression(
            rule.get("definition"), rule_names=known, depth=0, counter=counter
        )
        lines.append(f"{name} = {expression};")
    return SerializationResult.native("railroad", "\n".join(lines) + "\n", stability="experimental")


def plan_zenuml_structure(
    ir: Mapping[str, Any],
) -> ZenUMLPlan:
    """Validate exact sequence-fallback actors and messages without mutating source IR."""

    participants = ir.get("participants")
    messages = ir.get("messages")
    if not isinstance(participants, list) or not participants:
        raise SerializationError("zenuml IR requires participants")
    if not isinstance(messages, list) or not messages:
        raise SerializationError("zenuml IR requires messages")
    if len(participants) + len(messages) > MAX_ITEMS:
        raise SerializationError("zenuml IR exceeds the item limit")
    emitted_by_source: dict[str, str] = {}
    normalized_participants: list[ZenUMLParticipantPlan] = []
    compatibility_substituted = False
    for index, participant in enumerate(participants):
        if isinstance(participant, Mapping):
            source_participant_id = participant.get("id")
            participant_id = _identifier(source_participant_id, f"participants[{index}].id")
            label = participant.get("label", participant_id)
            source_record: Mapping[str, Any] | None = participant
        else:
            source_participant_id = participant
            participant_id = _identifier(source_participant_id, f"participants[{index}]")
            label = participant
            source_record = None
        if participant_id != source_participant_id:
            raise SerializationError(
                f"participants[{index}].id must not require whitespace normalization"
            )
        if participant_id in emitted_by_source:
            raise SerializationError(f"duplicate ZenUML participant: {participant_id}")
        semantic_label, visible_label, label_substituted = _zenuml_visible_text(
            label, f"participants[{index}].label"
        )
        compatibility_substituted = compatibility_substituted or label_substituted
        emitted_id = f"zenuml_participant_{participant_id}"
        if len(emitted_id) > MAX_ID_CHARS:
            raise SerializationError(
                f"participants[{index}].id exceeds the emitted identifier limit"
            )
        emitted_by_source[participant_id] = emitted_id
        normalized_participants.append(
            ZenUMLParticipantPlan(
                source_record=source_record,
                source_id=participant_id,
                emitted_id=emitted_id,
                label=visible_label,
                semantic_label=semantic_label,
            )
        )
    normalized_messages: list[ZenUMLMessagePlan] = []
    for index, message in enumerate(messages, start=1):
        if not isinstance(message, Mapping):
            raise SerializationError("zenuml messages must be objects")
        source_value = message.get("source")
        target_value = message.get("target")
        source = _identifier(source_value, f"messages[{index - 1}].source")
        target = _identifier(target_value, f"messages[{index - 1}].target")
        if source != source_value or target != target_value:
            raise SerializationError(
                f"messages[{index - 1}] endpoints must not require whitespace normalization"
            )
        source_emitted_id = emitted_by_source.get(source)
        target_emitted_id = emitted_by_source.get(target)
        if source_emitted_id is None or target_emitted_id is None:
            raise SerializationError(f"ZenUML message {source}->{target} is unresolved")
        semantic_label, label, label_substituted = _zenuml_visible_text(
            message.get("label"), f"messages[{index - 1}].label"
        )
        compatibility_substituted = compatibility_substituted or label_substituted
        normalized_messages.append(
            ZenUMLMessagePlan(
                source_record=message,
                emitted_id=f"zenuml_message_{index}",
                source_id=source,
                target_id=target,
                source_emitted_id=source_emitted_id,
                target_emitted_id=target_emitted_id,
                label=label,
                semantic_label=semantic_label,
            )
        )
    return ZenUMLPlan(
        participants=tuple(normalized_participants),
        messages=tuple(normalized_messages),
        compatibility_substituted=compatibility_substituted,
    )


def plan_zenuml_records(
    ir: Mapping[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return the exact legacy semantic tuple-of-dicts view for existing callers."""

    plan = plan_zenuml_structure(ir)
    participants = [
        {"id": participant.source_id, "label": participant.semantic_label}
        for participant in plan.participants
    ]
    messages = [
        {
            "source": message.source_id,
            "target": message.target_id,
            "label": message.semantic_label,
        }
        for message in plan.messages
    ]
    return participants, messages


def serialize_zenuml(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    """Emit sequence-like ZenUML evidence through the bundled sequence grammar."""

    plan = plan_zenuml_structure(ir)
    accessibility = resolve_accessibility(ir, "zenuml", experimental=experimental)
    _semantic_title, title, title_substituted = _zenuml_visible_text(
        accessibility.title, "accessible title", sequence_statement=False
    )
    _semantic_description, description, description_substituted = _zenuml_visible_text(
        accessibility.description, "accessible description", sequence_statement=False
    )
    accessibility_substituted = title_substituted or description_substituted
    lines = [
        "sequenceDiagram",
        f"accTitle: {_neutralize_active_text(title)}",
        f"accDescr: {_neutralize_active_text(description)}",
    ]
    lines.extend(
        f"    participant {participant.emitted_id} as {_neutralize_active_text(participant.label)}"
        for participant in plan.participants
    )
    lines.extend(
        f"    {message.source_emitted_id}->>{message.target_emitted_id}: "
        f"{_neutralize_active_text(message.label)}"
        for message in plan.messages
    )
    code = _preflight_experimental_code("\n".join(lines) + "\n", diagram_type="zenuml")
    warnings = ["ZenUML is unavailable in Mermaid 11.16 and was emitted as sequence."]
    if plan.compatibility_substituted or accessibility_substituted:
        warnings.append(_ZENUML_COMPATIBILITY_WARNING)
    return SerializationResult.fallback(
        "zenuml",
        "sequence",
        code,
        warnings=tuple(warnings),
        stability="experimental",
    )


def plan_organization_hierarchy(ir: Mapping[str, Any]) -> OrganizationPlan:
    """Validate one exact, bounded reporting hierarchy without deriving nodes."""

    if not isinstance(ir, Mapping):
        raise SerializationError("organization IR must be an object")
    _validate_accessibility_inputs(ir)
    root = ir.get("root")
    if not isinstance(root, Mapping):
        raise SerializationError("organization IR requires a root object")

    nodes: list[OrganizationNodePlan] = []
    relations: list[OrganizationRelationPlan] = []
    source_ids: set[str] = set()
    emitted_ids: set[str] = set()
    active_records: set[int] = set()
    seen_records: set[int] = set()
    compatibility_substituted = False

    def visit(
        node: Mapping[str, Any],
        depth: int,
        parent_source_id: str | None,
        parent_emitted_id: str | None,
    ) -> None:
        nonlocal compatibility_substituted
        if depth > MAX_DEPTH or len(nodes) >= MAX_ITEMS:
            raise SerializationError("organization hierarchy exceeds deterministic record limits")
        identity = id(node)
        if identity in active_records:
            raise SerializationError("organization hierarchy contains a cycle")
        if identity in seen_records:
            raise SerializationError("organization hierarchy reuses a node object")
        active_records.add(identity)
        seen_records.add(identity)
        try:
            node_index = len(nodes)
            if node.get("id") is None:
                source_id = f"node_{node_index + 1}"
            else:
                source_id_value = node["id"]
                if (
                    not isinstance(source_id_value, str)
                    or source_id_value != source_id_value.strip()
                    or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", source_id_value) is None
                ):
                    raise SerializationError(
                        f"nodes[{node_index}].id must be a safe Mermaid identifier"
                    )
                source_id = source_id_value
            emitted_id = _register_emitted_identifier(
                source_id,
                "treeview_node_",
                source_ids,
                emitted_ids,
                field="organization node",
            )
            label_present = node.get("label") is not None
            name_present = node.get("name") is not None
            if label_present and name_present:
                semantic_label = _text(node["label"], f"nodes[{node_index}].label")
                semantic_name = _text(node["name"], f"nodes[{node_index}].name")
                if semantic_label != semantic_name:
                    raise SerializationError("organization node label and name aliases must agree")
            elif label_present:
                semantic_label = _text(node["label"], f"nodes[{node_index}].label")
            elif name_present:
                semantic_label = _text(node["name"], f"nodes[{node_index}].name")
            else:
                raise SerializationError(f"nodes[{node_index}].label must be a non-empty string")
            _semantic_label, visible_label, label_substituted = _flowchart_visible_text(
                semantic_label, f"nodes[{node_index}].label"
            )
            compatibility_substituted = compatibility_substituted or label_substituted
            nodes.append(
                OrganizationNodePlan(
                    source_record=node,
                    source_id=source_id,
                    emitted_id=emitted_id,
                    label=visible_label,
                    semantic_label=semantic_label,
                    depth=depth,
                    parent_source_id=parent_source_id,
                    parent_emitted_id=parent_emitted_id,
                )
            )
            if parent_source_id is not None and parent_emitted_id is not None:
                relations.append(
                    OrganizationRelationPlan(
                        source_record=node,
                        emitted_id=f"organization_relation_{len(relations) + 1}",
                        source_id=parent_source_id,
                        target_id=source_id,
                        source_emitted_id=parent_emitted_id,
                        target_emitted_id=emitted_id,
                    )
                )
            children = node.get("children", [])
            if not isinstance(children, list):
                raise SerializationError(f"organization node {source_id!r} children must be a list")
            for child in children:
                if not isinstance(child, Mapping):
                    raise SerializationError("organization children must be objects")
                visit(child, depth + 1, source_id, emitted_id)
        finally:
            active_records.remove(identity)

    visit(root, 0, None, None)
    if not relations:
        raise SerializationError("organization requires an explicit hierarchy below the root")
    plan = OrganizationPlan(
        nodes=tuple(nodes),
        relations=tuple(relations),
        direction="LR",
        compatibility_substituted=compatibility_substituted,
    )
    for experimental in (False, True):
        for native_runtime_valid in (True, False):
            _organization_tree_result(
                ir,
                plan,
                experimental=experimental,
                native_runtime_valid=native_runtime_valid,
            )
    return plan


def _organization_tree_result(
    ir: Mapping[str, Any],
    plan: OrganizationPlan,
    *,
    experimental: bool,
    native_runtime_valid: bool,
) -> SerializationResult:
    from marker_mermaid.serializers_special import serialize_special

    normalized_by_id = {
        node.source_id: {
            "id": node.source_id,
            "label": node.semantic_label,
            "children": [],
        }
        for node in plan.nodes
    }
    for node in plan.nodes:
        if node.parent_source_id is not None:
            normalized_by_id[node.parent_source_id]["children"].append(
                normalized_by_id[node.source_id]
            )
    enriched = enrich_accessibility_ir(dict(ir), "organization", experimental=experimental)
    enriched["root"] = normalized_by_id[plan.nodes[0].source_id]
    tree = serialize_special(
        "treeview",
        enriched,
        experimental=experimental,
        native_runtime_valid=native_runtime_valid,
    )
    _preflight_experimental_code(tree.code, diagram_type="organization")
    return tree


def serialize_organization(
    ir: Mapping[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> SerializationResult:
    """Represent an explicit organization hierarchy through the TreeView fallback."""

    plan = plan_organization_hierarchy(ir)
    tree = _organization_tree_result(
        ir,
        plan,
        experimental=experimental,
        native_runtime_valid=native_runtime_valid,
    )
    return SerializationResult.fallback(
        "organization",
        tree.emitted_type,
        tree.code,
        via=("treeview",) if tree.emitted_type != "treeview" else (),
        warnings=(
            (
                "Organization chart was emitted as TreeView; reporting-line semantics are "
                "retained but organization-specific notation is unavailable."
                if tree.emitted_type == "treeview"
                else "Organization chart was projected through TreeView semantics and emitted "
                "as a portable Flowchart; organization-specific notation is unavailable."
            ),
            *tree.warnings,
        ),
        stability="extended",
    )


def plan_data_lineage_records(ir: Mapping[str, Any]) -> DataLineagePlan:
    """Validate exact datasets, processes, and directed lineage relations."""

    if not isinstance(ir, Mapping):
        raise SerializationError("data lineage IR must be an object")
    _validate_accessibility_inputs(ir)
    datasets = ir.get("datasets")
    processes = ir.get("processes", [])
    relations = ir.get("relations")
    if not isinstance(datasets, list) or not datasets:
        raise SerializationError("data lineage IR requires datasets")
    if not isinstance(processes, list) or not isinstance(relations, list) or not relations:
        raise SerializationError("data lineage IR requires process and relation lists")
    if len(datasets) + len(processes) + len(relations) > MAX_ITEMS:
        raise SerializationError("data lineage item limit exceeded")
    direction = ir.get("direction", "LR")
    if not isinstance(direction, str) or direction not in {"TB", "BT", "LR", "RL"}:
        raise SerializationError("data lineage direction must be TB, BT, LR, or RL")

    normalized_nodes: list[DataLineageNodePlan] = []
    emitted_by_source: dict[str, str] = {}
    source_ids: set[str] = set()
    emitted_ids: set[str] = set()
    compatibility_substituted = False
    for kind, items, shape in (
        ("dataset", datasets, "cylinder"),
        ("process", processes, "rectangle"),
    ):
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise SerializationError(f"data lineage {kind}s must be objects")
            item_id = _exact_identifier(item.get("id"), f"{kind}s[{index}].id")
            emitted_id = _register_emitted_identifier(
                item_id,
                f"data_lineage_{kind}_",
                source_ids,
                emitted_ids,
                field="data lineage",
            )
            label_value = item["label"] if item.get("label") is not None else item_id
            semantic_label, visible_label, label_substituted = _flowchart_visible_text(
                label_value, f"{kind}s[{index}].label"
            )
            compatibility_substituted = compatibility_substituted or label_substituted
            emitted_by_source[item_id] = emitted_id
            normalized_nodes.append(
                DataLineageNodePlan(
                    source_record=item,
                    source_id=item_id,
                    emitted_id=emitted_id,
                    kind=kind,
                    shape=shape,
                    label=visible_label,
                    semantic_label=semantic_label,
                )
            )

    normalized_relations: list[DataLineageRelationPlan] = []
    seen_relations: set[tuple[str, str, str | None]] = set()
    for index, relation in enumerate(relations):
        if not isinstance(relation, Mapping):
            raise SerializationError("data lineage relations must be objects")
        source = _exact_identifier(relation.get("source"), f"relations[{index}].source")
        target = _exact_identifier(relation.get("target"), f"relations[{index}].target")
        source_emitted_id = emitted_by_source.get(source)
        target_emitted_id = emitted_by_source.get(target)
        if source_emitted_id is None or target_emitted_id is None or source == target:
            raise SerializationError(f"invalid data lineage relation: {source}->{target}")
        label = None
        semantic_label = None
        if relation.get("label") is not None:
            semantic_label, label, label_substituted = _flowchart_visible_text(
                relation["label"], f"relations[{index}].label", edge_label=True
            )
            compatibility_substituted = compatibility_substituted or label_substituted
        key = (source, target, label)
        if key in seen_relations:
            raise SerializationError(f"duplicate data lineage relation: {source}->{target}")
        seen_relations.add(key)
        normalized_relations.append(
            DataLineageRelationPlan(
                source_record=relation,
                emitted_id=f"data_lineage_relation_{index + 1}",
                source_id=source,
                target_id=target,
                source_emitted_id=source_emitted_id,
                target_emitted_id=target_emitted_id,
                label=label,
                semantic_label=semantic_label,
            )
        )
    plan = DataLineagePlan(
        nodes=tuple(normalized_nodes),
        relations=tuple(normalized_relations),
        direction=direction,
        compatibility_substituted=compatibility_substituted,
    )
    for experimental in (False, True):
        _data_lineage_code(ir, plan, experimental=experimental)
    return plan


def _data_lineage_code(
    ir: Mapping[str, Any],
    plan: DataLineagePlan,
    *,
    experimental: bool,
) -> tuple[str, bool]:
    accessibility = resolve_accessibility(ir, "data_lineage", experimental=experimental)
    _semantic_title, title, title_substituted = _flowchart_visible_text(
        accessibility.title, "accessible title", accessibility=True
    )
    _semantic_description, description, description_substituted = _flowchart_visible_text(
        accessibility.description, "accessible description", accessibility=True
    )
    code = serialize_flowchart(
        {
            "nodes": [
                {
                    "id": node.emitted_id,
                    "label": _neutralize_active_text(node.label),
                    "shape": node.shape,
                }
                for node in plan.nodes
            ],
            "edges": [
                {
                    "source": relation.source_emitted_id,
                    "target": relation.target_emitted_id,
                    "label": (
                        _neutralize_active_text(
                            relation.label.replace("＠", "＠\N{ZERO WIDTH SPACE}")
                        )
                        if relation.label is not None
                        else None
                    ),
                }
                for relation in plan.relations
            ],
            "direction": plan.direction,
            "acc_title": _neutralize_active_text(title),
            "acc_description": _neutralize_active_text(description),
        },
        experimental=experimental,
    )
    return (
        _preflight_experimental_code(code, diagram_type="data_lineage"),
        title_substituted or description_substituted,
    )


def serialize_data_lineage(
    ir: Mapping[str, Any], *, experimental: bool = False
) -> SerializationResult:
    """Represent explicit dataset/process relations as a portable flowchart."""

    plan = plan_data_lineage_records(ir)
    code, accessibility_substituted = _data_lineage_code(ir, plan, experimental=experimental)
    warnings = ["Data lineage was emitted as a portable flowchart."]
    if plan.compatibility_substituted or accessibility_substituted:
        warnings.append(_DATA_LINEAGE_COMPATIBILITY_WARNING)
    return SerializationResult.fallback(
        "data_lineage",
        "flowchart",
        code,
        warnings=tuple(warnings),
        stability="extended",
    )


EXPERIMENTAL_SERIALIZERS = {
    "wardley": serialize_wardley,
    "cynefin": serialize_cynefin,
    "railroad": serialize_railroad,
    "zenuml": serialize_zenuml,
    "organization": serialize_organization,
    "data_lineage": serialize_data_lineage,
}


def serialize_experimental(
    diagram_type: str,
    ir: Mapping[str, Any],
    *,
    experimental: bool = False,
) -> SerializationResult:
    serializer = EXPERIMENTAL_SERIALIZERS.get(diagram_type)
    if serializer is None:
        raise SerializationError(f"no experimental serializer for {diagram_type}")
    return serializer(ir, experimental=experimental)
