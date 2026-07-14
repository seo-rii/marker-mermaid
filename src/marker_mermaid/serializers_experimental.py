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
from marker_mermaid.serialization import SerializationResult
from marker_mermaid.serializers import SerializationError, serialize_flowchart

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


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SerializationError(f"{field} must be a non-empty string")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in value):
        raise SerializationError(f"{field} contains unsupported control or format characters")
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


def plan_zenuml_records(
    ir: Mapping[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Validate and normalize the participant/message records the fallback emits."""

    participants = ir.get("participants")
    messages = ir.get("messages")
    if not isinstance(participants, list) or not participants:
        raise SerializationError("zenuml IR requires participants")
    if not isinstance(messages, list) or not messages:
        raise SerializationError("zenuml IR requires messages")
    if len(participants) + len(messages) > MAX_ITEMS:
        raise SerializationError("zenuml IR exceeds the item limit")
    participant_ids: set[str] = set()
    normalized_participants: list[dict[str, str]] = []
    for index, participant in enumerate(participants):
        if isinstance(participant, Mapping):
            participant_id = _identifier(participant.get("id"), f"participants[{index}].id")
            label = participant.get("label", participant_id)
        else:
            participant_id = _identifier(participant, f"participants[{index}]")
            label = participant
        if participant_id in participant_ids:
            raise SerializationError(f"duplicate ZenUML participant: {participant_id}")
        participant_ids.add(participant_id)
        normalized_participants.append(
            {"id": participant_id, "label": _text(label, "participant label")}
        )
    normalized_messages: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise SerializationError("zenuml messages must be objects")
        source = _identifier(message.get("source"), f"messages[{index}].source")
        target = _identifier(message.get("target"), f"messages[{index}].target")
        if source not in participant_ids or target not in participant_ids:
            raise SerializationError(f"ZenUML message {source}->{target} is unresolved")
        label = _text(message.get("label"), f"messages[{index}].label")
        normalized_messages.append({"source": source, "target": target, "label": label})
    return normalized_participants, normalized_messages


def serialize_zenuml(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    """Emit sequence-like ZenUML evidence through the bundled sequence grammar."""

    participants, messages = plan_zenuml_records(ir)
    lines = ["sequenceDiagram", *_accessibility(ir, "zenuml", experimental=experimental)]
    lines.extend(
        f"    participant {participant['id']} as {participant['label']}"
        for participant in participants
    )
    lines.extend(
        f"    {message['source']}->>{message['target']}: {message['label']}" for message in messages
    )
    return SerializationResult.fallback(
        "zenuml",
        "sequence",
        "\n".join(lines) + "\n",
        warnings=("ZenUML is unavailable in Mermaid 11.16 and was emitted as sequence.",),
        stability="experimental",
    )


def serialize_organization(
    ir: Mapping[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> SerializationResult:
    """Represent an organization hierarchy with the native TreeView grammar."""

    from marker_mermaid.serializers_special import serialize_special

    enriched = enrich_accessibility_ir(ir, "organization", experimental=experimental)
    tree = serialize_special(
        "treeview",
        enriched,
        experimental=experimental,
        native_runtime_valid=native_runtime_valid,
    )
    return SerializationResult.fallback(
        "organization",
        tree.emitted_type,
        tree.code,
        via=("treeview",) if tree.emitted_type != "treeview" else (),
        warnings=(
            "Organization chart was emitted as TreeView; reporting-line semantics are retained "
            "but organization-specific notation is unavailable.",
            *tree.warnings,
        ),
        stability="extended",
    )


def serialize_data_lineage(
    ir: Mapping[str, Any], *, experimental: bool = False
) -> SerializationResult:
    """Represent explicit dataset/process relations as a portable flowchart."""

    datasets = ir.get("datasets")
    processes = ir.get("processes", [])
    relations = ir.get("relations")
    if not isinstance(datasets, list) or not datasets:
        raise SerializationError("data lineage IR requires datasets")
    if not isinstance(processes, list) or not isinstance(relations, list) or not relations:
        raise SerializationError("data lineage IR requires process and relation lists")
    if len(datasets) + len(processes) + len(relations) > MAX_ITEMS:
        raise SerializationError("data lineage item limit exceeded")
    nodes: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for kind, items, shape in (
        ("dataset", datasets, "cylinder"),
        ("process", processes, "rectangle"),
    ):
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise SerializationError(f"data lineage {kind}s must be objects")
            item_id = _identifier(item.get("id"), f"{kind}s[{index}].id")
            if item_id in identifiers:
                raise SerializationError(f"duplicate data lineage id: {item_id}")
            identifiers.add(item_id)
            nodes.append(
                {
                    "id": item_id,
                    "label": _text(item.get("label", item_id), f"{kind}s[{index}].label"),
                    "shape": shape,
                }
            )
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for index, relation in enumerate(relations):
        if not isinstance(relation, Mapping):
            raise SerializationError("data lineage relations must be objects")
        source = _identifier(relation.get("source"), f"relations[{index}].source")
        target = _identifier(relation.get("target"), f"relations[{index}].target")
        if source not in identifiers or target not in identifiers or source == target:
            raise SerializationError(f"invalid data lineage relation: {source}->{target}")
        label = (
            _text(relation["label"], f"relations[{index}].label")
            if relation.get("label") is not None
            else None
        )
        key = (source, target, label)
        if key in seen:
            raise SerializationError(f"duplicate data lineage relation: {source}->{target}")
        seen.add(key)
        edges.append({"source": source, "target": target, "label": label})
    code = serialize_flowchart(
        {
            "nodes": nodes,
            "edges": edges,
            "direction": ir.get("direction", "LR"),
            "title": ir.get("title"),
            "description": ir.get("description"),
            "acc_title": ir.get("acc_title"),
            "acc_description": ir.get("acc_description"),
        },
        experimental=experimental,
    )
    return SerializationResult.fallback(
        "data_lineage",
        "flowchart",
        code,
        warnings=("Data lineage was emitted as a portable flowchart.",),
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
