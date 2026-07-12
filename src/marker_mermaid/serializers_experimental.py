"""Evidence-strict Wardley, Cynefin, Railroad, and ZenUML serializers.

The pinned Mermaid 11.16 runtime natively supports the first three grammars.
ZenUML is not bundled, so its sequence-like IR is emitted as an explicit
``sequenceDiagram`` fallback instead of pretending that native syntax rendered.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from marker_mermaid.serialization import SerializationResult
from marker_mermaid.serializers import SerializationError, serialize_flowchart

MAX_ITEMS = 500
MAX_DEPTH = 20
MAX_TEXT_LENGTH = 500
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}\Z")
_DOMAINS = {"complex", "complicated", "clear", "chaotic", "confusion"}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SerializationError(f"{field} must be a non-empty string")
    normalized = " ".join(value.strip().split())
    if len(normalized) > MAX_TEXT_LENGTH or any(ord(char) < 32 for char in normalized):
        raise SerializationError(f"{field} exceeds the safe text limit")
    return normalized


def _quoted(value: Any, field: str) -> str:
    return json.dumps(_text(value, field), ensure_ascii=False)


def _identifier(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _IDENTIFIER.fullmatch(text):
        raise SerializationError(f"{field} must be a safe Mermaid identifier")
    return text


def _accessibility(ir: Mapping[str, Any], *, experimental: bool) -> list[str]:
    lines: list[str] = []
    title = ir.get("acc_title") or ir.get("title")
    description = ir.get("acc_description") or ir.get("description")
    if title is not None:
        lines.append(f"accTitle: {_text(title, 'accessible title')}")
    if experimental:
        suffix = "This reconstruction is experimental and requires review."
        description = (
            f"{_text(description, 'accessible description')} {suffix}" if description else suffix
        )
    if description is not None:
        lines.append(f"accDescr: {_text(description, 'accessible description')}")
    return lines


def _coordinate(value: Any, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SerializationError(f"{field} must be an explicit numeric coordinate")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise SerializationError(f"{field} must be between 0 and 1")
    rendered = format(number, ".15g")
    return rendered if "." in rendered else f"{rendered}.0"


def serialize_wardley(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    """Serialize explicitly positioned components without inferring coordinates."""

    components = ir.get("components")
    if not isinstance(components, list) or not components:
        raise SerializationError("wardley IR requires components")
    if len(components) > MAX_ITEMS:
        raise SerializationError("wardley component limit exceeded")
    lines = ["wardley-beta", *_accessibility(ir, experimental=experimental)]
    if ir.get("title") is not None:
        lines.append(f"title {_text(ir['title'], 'title')}")
    tokens: dict[str, str] = {}
    labels: set[str] = set()
    for index, component in enumerate(components):
        if not isinstance(component, Mapping):
            raise SerializationError("wardley components must be objects")
        component_id = _identifier(component.get("id"), f"components[{index}].id")
        if component_id in tokens:
            raise SerializationError(f"duplicate Wardley component id: {component_id}")
        label = _text(component.get("label", component_id), f"components[{index}].label")
        if label in labels:
            raise SerializationError(f"duplicate Wardley component label: {label}")
        labels.add(label)
        token = json.dumps(label, ensure_ascii=False)
        tokens[component_id] = token
        kind = "anchor" if component.get("anchor") is True else "component"
        x = _coordinate(component.get("x"), f"components[{index}].x")
        y = _coordinate(component.get("y"), f"components[{index}].y")
        lines.append(f"{kind} {token} [{x}, {y}]")
    links = ir.get("links", [])
    if not isinstance(links, list) or len(links) > MAX_ITEMS:
        raise SerializationError("wardley links must be a bounded list")
    seen_links: set[tuple[str, str]] = set()
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
        suffix = ""
        if link.get("label") is not None:
            label = _text(link["label"], f"links[{index}].label")
            if any(char in label for char in ";\r\n"):
                raise SerializationError("Wardley link labels cannot contain separators")
            suffix = f"; {label}"
        lines.append(f"{tokens[source]} -> {tokens[target]}{suffix}")
    return SerializationResult.native("wardley", "\n".join(lines) + "\n", stability="experimental")


def serialize_cynefin(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    domains = ir.get("domains")
    if not isinstance(domains, list) or not domains:
        raise SerializationError("cynefin IR requires domains")
    if len(domains) > len(_DOMAINS):
        raise SerializationError("cynefin has at most five domains")
    lines = ["cynefin-beta", *_accessibility(ir, experimental=experimental)]
    defined: set[str] = set()
    item_count = 0
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
        defined.add(name)
        lines.append(name)
        for item_index, item in enumerate(items):
            label = item.get("label") if isinstance(item, Mapping) else item
            lines.append(f"  {_quoted(label, f'domains[{index}].items[{item_index}]')}")
    transitions = ir.get("transitions", [])
    if not isinstance(transitions, list) or len(transitions) > MAX_ITEMS:
        raise SerializationError("cynefin transitions must be a bounded list")
    seen: set[tuple[str, str, str | None]] = set()
    for index, transition in enumerate(transitions):
        if not isinstance(transition, Mapping):
            raise SerializationError("cynefin transitions must be objects")
        source = _text(transition.get("source"), f"transitions[{index}].source").casefold()
        target = _text(transition.get("target"), f"transitions[{index}].target").casefold()
        if source not in defined or target not in defined or source == target:
            raise SerializationError(f"invalid Cynefin transition: {source}->{target}")
        label = transition.get("label")
        normalized_label = _text(label, f"transitions[{index}].label") if label else None
        key = (source, target, normalized_label)
        if key in seen:
            raise SerializationError(f"duplicate Cynefin transition: {source}->{target}")
        seen.add(key)
        suffix = (
            f" : {json.dumps(normalized_label, ensure_ascii=False)}" if normalized_label else ""
        )
        lines.append(f"{source} --> {target}{suffix}")
    return SerializationResult.native("cynefin", "\n".join(lines) + "\n", stability="experimental")


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
    lines = ["railroad-beta", *_accessibility(ir, experimental=experimental)]
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


def serialize_zenuml(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    """Emit sequence-like ZenUML evidence through the bundled sequence grammar."""

    participants = ir.get("participants")
    messages = ir.get("messages")
    if not isinstance(participants, list) or not participants:
        raise SerializationError("zenuml IR requires participants")
    if not isinstance(messages, list) or not messages:
        raise SerializationError("zenuml IR requires messages")
    if len(participants) + len(messages) > MAX_ITEMS:
        raise SerializationError("zenuml IR exceeds the item limit")
    lines = ["sequenceDiagram", *_accessibility(ir, experimental=experimental)]
    participant_ids: set[str] = set()
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
        lines.append(f"    participant {participant_id} as {_text(label, 'participant label')}")
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise SerializationError("zenuml messages must be objects")
        source = _identifier(message.get("source"), f"messages[{index}].source")
        target = _identifier(message.get("target"), f"messages[{index}].target")
        if source not in participant_ids or target not in participant_ids:
            raise SerializationError(f"ZenUML message {source}->{target} is unresolved")
        label = _text(message.get("label"), f"messages[{index}].label")
        lines.append(f"    {source}->>{target}: {label}")
    return SerializationResult.fallback(
        "zenuml",
        "sequence",
        "\n".join(lines) + "\n",
        warnings=("ZenUML is unavailable in Mermaid 11.16 and was emitted as sequence.",),
        stability="experimental",
    )


def serialize_organization(
    ir: Mapping[str, Any], *, experimental: bool = False
) -> SerializationResult:
    """Represent an organization hierarchy with the native TreeView grammar."""

    from marker_mermaid.serializers_special import serialize_special

    tree = serialize_special("treeview", ir, experimental=experimental)
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
