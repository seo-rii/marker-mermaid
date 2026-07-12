"""Evidence-strict Mermaid serializers for UML and entity-relationship diagrams.

The serializers in this module intentionally reject incomplete relationships.  A
missing endpoint or cardinality is not repaired here because doing so would turn a
deterministic serializer into a source of semantic guesses.
"""

from __future__ import annotations

import re
from typing import Any

from marker_mermaid.serializers import SerializationError


def _identifier(value: Any, *, context: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SerializationError(f"{context} requires an id")
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", raw).strip("_")
    if not normalized:
        raise SerializationError(f"{context} id cannot be represented in Mermaid")
    if normalized[0].isdigit():
        normalized = f"n_{normalized}"
    return normalized


def _text(value: Any) -> str:
    """Return single-line text safe for Mermaid double-quoted labels."""

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', "&quot;")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _relation_label(value: Any, *, context: str) -> str:
    label = _text(value)
    if any(character in label for character in (":", ";")):
        raise SerializationError(f"{context} label contains unsupported ':' or ';'")
    return label


def _evidence(item: dict[str, Any], *, context: str) -> None:
    evidence_ids = item.get("evidence_ids")
    if (
        not isinstance(evidence_ids, list)
        or not evidence_ids
        or any(not isinstance(value, str) or not value.strip() for value in evidence_ids)
    ):
        raise SerializationError(f"{context} requires at least one evidence id")


def _objects(value: Any, *, context: str, required: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (required and not value):
        suffix = " at least one item" if required else " a list"
        raise SerializationError(f"{context} requires{suffix}")
    if any(not isinstance(item, dict) for item in value):
        raise SerializationError(f"{context} items must be objects")
    return value


def _id_map(items: list[dict[str, Any]], *, context: str) -> dict[str, str]:
    result: dict[str, str] = {}
    rendered_ids: set[str] = set()
    for index, item in enumerate(items, start=1):
        _evidence(item, context=f"{context} {index}")
        source_id = str(item.get("id") or "").strip()
        rendered = _identifier(source_id, context=f"{context} {index}")
        if source_id in result:
            raise SerializationError(f"duplicate {context} id: {source_id}")
        if rendered in rendered_ids:
            raise SerializationError(f"{context} ids collide after Mermaid normalization")
        result[source_id] = rendered
        rendered_ids.add(rendered)
    return result


def _accessibility(ir: dict[str, Any], *, experimental: bool) -> list[str]:
    title = ir.get("acc_title") or ir.get("title")
    description = ir.get("acc_description") or ir.get("description")
    if experimental:
        suffix = "This reconstruction is experimental and requires review."
        description = f"{description} {suffix}" if description else suffix
    lines: list[str] = []
    if title:
        lines.append(f"    accTitle: {_text(title)}")
    if description:
        lines.append(f"    accDescr: {_text(description)}")
    return lines


def serialize_state(ir: dict[str, Any], *, experimental: bool = False) -> str:
    """Serialize evidence-backed states and transitions to ``stateDiagram-v2``.

    Initial and terminal states are represented only when a transition explicitly
    uses ``[*]`` as its source or target.
    """

    states = _objects(ir.get("states"), context="state IR", required=True)
    transitions = _objects(ir.get("transitions", []), context="state transitions")
    id_map = _id_map(states, context="state")
    lines = ["stateDiagram-v2", *_accessibility(ir, experimental=experimental)]
    direction = ir.get("direction")
    if direction is not None:
        if direction not in {"TB", "BT", "LR", "RL"}:
            raise SerializationError("state direction must be TB, BT, LR, or RL")
        lines.append(f"    direction {direction}")

    supported_kinds = {"state", "choice", "fork", "join"}
    for index, state in enumerate(states, start=1):
        source_id = str(state["id"]).strip()
        state_id = id_map[source_id]
        label = _text(state.get("label") or source_id)
        kind = str(state.get("kind") or "state").lower()
        if kind not in supported_kinds:
            raise SerializationError(f"state {index} has unsupported kind: {kind}")
        if kind == "state":
            lines.append(f'    state "{label}" as {state_id}')
        else:
            lines.append(f"    state {state_id} <<{kind}>>")

    for index, transition in enumerate(transitions, start=1):
        _evidence(transition, context=f"state transition {index}")
        source_raw = str(transition.get("source") or "").strip()
        target_raw = str(transition.get("target") or "").strip()
        source = "[*]" if source_raw == "[*]" else id_map.get(source_raw)
        target = "[*]" if target_raw == "[*]" else id_map.get(target_raw)
        if source is None or target is None:
            raise SerializationError(f"state transition {index} references an unknown endpoint")
        suffix = ""
        if transition.get("label") not in {None, ""}:
            suffix = (
                f" : {_relation_label(transition['label'], context=f'state transition {index}')}"
            )
        lines.append(f"    {source} --> {target}{suffix}")
    return "\n".join(lines) + "\n"


def _class_member(member: dict[str, Any], *, context: str) -> str:
    _evidence(member, context=context)
    name = _text(member.get("name") or "")
    if not name:
        raise SerializationError(f"{context} requires a name")
    if any(character in name for character in ("{", "}", ";")):
        raise SerializationError(f"{context} name contains unsupported syntax")
    visibility = member.get("visibility", "")
    if visibility not in {"", "+", "-", "#", "~"}:
        raise SerializationError(f"{context} has invalid visibility")
    kind = member.get("kind", "field")
    type_name = _text(member.get("type") or "")
    if any(character in type_name for character in ("{", "}", ";")):
        raise SerializationError(f"{context} type contains unsupported syntax")
    if kind == "field":
        body = " ".join(part for part in (type_name, name) if part)
    elif kind == "method":
        parameters = member.get("parameters", [])
        if not isinstance(parameters, list):
            raise SerializationError(f"{context} parameters must be a list")
        rendered_parameters = [_text(parameter) for parameter in parameters]
        if any(
            any(character in value for character in ("{", "}", ";"))
            for value in rendered_parameters
        ):
            raise SerializationError(f"{context} parameter contains unsupported syntax")
        return_type = _text(member.get("return_type") or type_name)
        body = f"{name}({', '.join(rendered_parameters)})"
        if return_type:
            body = f"{body} {return_type}"
    else:
        raise SerializationError(f"{context} kind must be field or method")
    classifier = member.get("classifier")
    if classifier not in {None, "", "static", "abstract"}:
        raise SerializationError(f"{context} classifier must be static or abstract")
    suffix = "$" if classifier == "static" else "*" if classifier == "abstract" else ""
    return f"{visibility}{body}{suffix}"


_CLASS_RELATIONS = {
    "association": "--",
    "dependency": "..>",
    "aggregation": "o--",
    "composition": "*--",
    "link": "-->",
}


def serialize_class(ir: dict[str, Any], *, experimental: bool = False) -> str:
    """Serialize classes while preserving source-to-target relation semantics.

    For ``inheritance`` and ``realization`` the input source is the child and the
    input target is the parent/interface. Other relation operators are emitted in
    source-to-target order.
    """

    classes = _objects(ir.get("classes"), context="class IR", required=True)
    relations = _objects(ir.get("relations", []), context="class relations")
    id_map = _id_map(classes, context="class")
    lines = ["classDiagram", *_accessibility(ir, experimental=experimental)]
    direction = ir.get("direction")
    if direction is not None:
        if direction not in {"TB", "BT", "LR", "RL"}:
            raise SerializationError("class direction must be TB, BT, LR, or RL")
        lines.append(f"    direction {direction}")

    for index, class_item in enumerate(classes, start=1):
        source_id = str(class_item["id"]).strip()
        class_id = id_map[source_id]
        label = _text(class_item.get("label") or source_id)
        members = _objects(class_item.get("members", []), context=f"class {index} members")
        if members:
            lines.append(f'    class {class_id}["{label}"] {{')
            for member_index, member in enumerate(members, start=1):
                lines.append(
                    "        "
                    + _class_member(member, context=f"class {index} member {member_index}")
                )
            lines.append("    }")
        else:
            lines.append(f'    class {class_id}["{label}"]')

    for index, relation in enumerate(relations, start=1):
        _evidence(relation, context=f"class relation {index}")
        source = id_map.get(str(relation.get("source") or "").strip())
        target = id_map.get(str(relation.get("target") or "").strip())
        if source is None or target is None:
            raise SerializationError(f"class relation {index} references an unknown endpoint")
        relation_type = relation.get("type")
        source_cardinality = relation.get("source_cardinality")
        target_cardinality = relation.get("target_cardinality")
        if relation_type == "inheritance":
            left, operator, right = target, "<|--", source
            left_cardinality, right_cardinality = target_cardinality, source_cardinality
        elif relation_type == "realization":
            left, operator, right = target, "<|..", source
            left_cardinality, right_cardinality = target_cardinality, source_cardinality
        elif relation_type in _CLASS_RELATIONS:
            left, operator, right = source, _CLASS_RELATIONS[relation_type], target
            left_cardinality, right_cardinality = source_cardinality, target_cardinality
        else:
            raise SerializationError(f"class relation {index} has unsupported type")
        if left_cardinality not in {None, ""}:
            left += f' "{_text(left_cardinality)}"'
        if right_cardinality not in {None, ""}:
            right = f'"{_text(right_cardinality)}" {right}'
        label = relation.get("label")
        suffix = (
            f" : {_relation_label(label, context=f'class relation {index}')}"
            if label not in {None, ""}
            else ""
        )
        lines.append(f"    {left} {operator} {right}{suffix}")
    return "\n".join(lines) + "\n"


_SOURCE_CARDINALITY = {
    "one": "||",
    "only_one": "||",
    "zero_or_one": "o|",
    "one_or_more": "}|",
    "zero_or_more": "}o",
}
_TARGET_CARDINALITY = {
    "one": "||",
    "only_one": "||",
    "zero_or_one": "|o",
    "one_or_more": "|{",
    "zero_or_more": "o{",
}


def _er_token(value: Any, *, context: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise SerializationError(f"{context} requires a value")
    if re.fullmatch(r"[A-Za-z_*\u00C0-\uFFFF][A-Za-z0-9_.\-\[\](),*\u00C0-\uFFFF]*", token):
        return token
    if "`" in token or any(character in token for character in ("\r", "\n")):
        raise SerializationError(f"{context} cannot be represented safely")
    return f"`{token}`"


def serialize_er(ir: dict[str, Any], *, experimental: bool = False) -> str:
    """Serialize ER entities and only relationships with explicit cardinalities."""

    entities = _objects(ir.get("entities"), context="ER IR", required=True)
    relationships = _objects(ir.get("relationships", []), context="ER relationships")
    id_map = _id_map(entities, context="ER entity")
    lines = ["erDiagram", *_accessibility(ir, experimental=experimental)]
    direction = ir.get("direction")
    if direction is not None:
        if direction not in {"TB", "BT", "LR", "RL"}:
            raise SerializationError("ER direction must be TB, BT, LR, or RL")
        lines.append(f"    direction {direction}")

    for index, entity in enumerate(entities, start=1):
        source_id = str(entity["id"]).strip()
        entity_id = id_map[source_id]
        label = _text(entity.get("label") or source_id)
        attributes = _objects(entity.get("attributes", []), context=f"ER entity {index} attributes")
        declaration = entity_id if label == entity_id else f'{entity_id}["{label}"]'
        if attributes:
            lines.append(f"    {declaration} {{")
            for attribute_index, attribute in enumerate(attributes, start=1):
                context = f"ER entity {index} attribute {attribute_index}"
                _evidence(attribute, context=context)
                attribute_type = _er_token(attribute.get("type"), context=f"{context} type")
                name = _er_token(attribute.get("name"), context=f"{context} name")
                keys = attribute.get("keys", [])
                if not isinstance(keys, list) or any(key not in {"PK", "FK", "UK"} for key in keys):
                    raise SerializationError(f"{context} keys must contain only PK, FK, or UK")
                fields = [attribute_type, name]
                if keys:
                    fields.append(",".join(keys))
                comment = attribute.get("comment")
                if comment not in {None, ""}:
                    fields.append(f'"{_text(comment)}"')
                lines.append("        " + " ".join(fields))
            lines.append("    }")
        else:
            lines.append(f"    {declaration}")

    for index, relationship in enumerate(relationships, start=1):
        _evidence(relationship, context=f"ER relationship {index}")
        source = id_map.get(str(relationship.get("source") or "").strip())
        target = id_map.get(str(relationship.get("target") or "").strip())
        if source is None or target is None:
            raise SerializationError(f"ER relationship {index} references an unknown endpoint")
        source_cardinality = _SOURCE_CARDINALITY.get(relationship.get("source_cardinality"))
        target_cardinality = _TARGET_CARDINALITY.get(relationship.get("target_cardinality"))
        if source_cardinality is None or target_cardinality is None:
            raise SerializationError(f"ER relationship {index} requires explicit cardinalities")
        identifying = relationship.get("identifying")
        if not isinstance(identifying, bool):
            raise SerializationError(f"ER relationship {index} requires identifying=true or false")
        label = _relation_label(relationship.get("label") or "", context=f"ER relationship {index}")
        if not label:
            raise SerializationError(f"ER relationship {index} requires a label")
        connector = "--" if identifying else ".."
        lines.append(
            f"    {source} {source_cardinality}{connector}{target_cardinality} {target} : {label}"
        )
    return "\n".join(lines) + "\n"


UML_SERIALIZERS = {
    "state": serialize_state,
    "class": serialize_class,
    "er": serialize_er,
}
