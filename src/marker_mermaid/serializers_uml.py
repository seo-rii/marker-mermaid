"""Evidence-strict Mermaid serializers for UML and entity-relationship diagrams.

The serializers in this module intentionally reject incomplete relationships.  A
missing endpoint or cardinality is not repaired here because doing so would turn a
deterministic serializer into a source of semantic guesses.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from marker_mermaid.accessibility import resolve_accessibility
from marker_mermaid.models import MAX_TEXT_CHARS
from marker_mermaid.serializers import SerializationError

_ZERO_WIDTH_SPACE = "\u200b"
_CSS_IMPORT = re.compile(r"@import\b", re.IGNORECASE)
_ACTIVE_CALLBACK = re.compile(r"\b(?:call|callback)\s*\(", re.IGNORECASE)
_DANGEROUS_SCHEME = re.compile(r"(?:https?|ftp|file|data|javascript):", re.IGNORECASE)
_REMOTE_ICON = re.compile(r"iconify|fa:|logos:", re.IGNORECASE)
_DIRECTIVE_OPEN = re.compile(r"%%(?P<gap>\s*)\{")
_CONTROL_WORD = re.compile(
    r"\b(?:accTitle|accDescr|choice|class|classDef|click|direction|fork|hide|join|"
    r"linkStyle|note|scale|state|style)\b",
    re.IGNORECASE,
)
_CONFIG = re.compile(r"\bconfig\s*:", re.IGNORECASE)
_ENTITY_LITERAL = re.compile(
    r"&(?P<body>#[0-9]+|#x[0-9A-F]+|[A-Z][A-Z0-9]+);",
    re.IGNORECASE,
)
_NUMERIC_ENTITY_LITERAL = re.compile(r"&(?P<body>#[0-9]+|#x[0-9A-F]+);", re.IGNORECASE)
_MARKDOWN_WWW_AUTOLINK = re.compile(r"www\.", re.IGNORECASE)

STATE_TEXT_COMPATIBILITY_WARNING = (
    "State canvas or accessibility text used visible compatibility glyphs for "
    "grammar-active quote, backslash, Markdown, entity, or less-than text that "
    "Mermaid 11.16 cannot preserve literally; semantic text remains in typed IR."
)
_STATE_METADATA_FIELDS = ("title", "description", "acc_title", "acc_description")
_COMMONMARK_ESCAPABLE = frozenset(r"!\"#$%&'()*+,-./:;<=>?@[\]^_`{|}~")
_STATE_RESERVED_IDENTIFIERS = frozenset(
    {
        "accdescr",
        "acctitle",
        "as",
        "class",
        "classdef",
        "click",
        "default",
        "direction",
        "href",
        "linkstyle",
        "note",
        "scale",
        "state",
        "statediagram",
        "statediagram_v2",
        "style",
    }
)


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


def validated_state_accessibility_ir(ir: Mapping[str, Any]) -> dict[str, Any]:
    """Validate raw State metadata and treat exact-empty fields as omitted."""

    for field in _STATE_METADATA_FIELDS:
        value = ir.get(field)
        if value is None:
            continue
        if type(value) is not str:
            raise SerializationError(f"state {field} must be text when provided")
        if value == "":
            continue
        if len(value) > MAX_TEXT_CHARS:
            raise SerializationError(f"state {field} must be bounded non-empty text")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for character in value
        ):
            raise SerializationError(f"state {field} contains unsupported text")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > MAX_TEXT_CHARS:
            raise SerializationError(f"state {field} must be bounded non-empty text")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:  # pragma: no cover - Cs is rejected above
            raise SerializationError(f"state {field} is not valid UTF-8 text") from exc
    return {
        key: value for key, value in ir.items() if key not in _STATE_METADATA_FIELDS or value != ""
    }


def _state_terminal_text(
    value: Any,
    *,
    context: str,
    quoted_node: bool,
    markdown_label: bool,
    accessibility_directive: bool,
) -> tuple[str, str, str, bool]:
    """Freeze semantic, source, and Mermaid 11.16 canvas text for State."""

    if type(value) is not str:
        raise SerializationError(f"{context} must be text")
    if len(value) > MAX_TEXT_CHARS:
        raise SerializationError(f"{context} must be bounded non-empty text")
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cf", "Cs"} or (category == "Cc" and not character.isspace()):
            raise SerializationError(f"{context} contains unsupported text")
    semantic = " ".join(value.split())
    if not semantic or len(semantic) > MAX_TEXT_CHARS:
        raise SerializationError(f"{context} must be bounded non-empty text")
    try:
        semantic.encode("utf-8")
    except UnicodeEncodeError as exc:  # pragma: no cover - Cs is rejected above
        raise SerializationError(f"{context} is not valid UTF-8 text") from exc

    canvas = semantic
    if markdown_label:
        canvas = _ENTITY_LITERAL.sub(
            lambda match: (
                f"＆＃{match.group('body')[1:]};"
                if match.group("body").startswith("#")
                else f"＆{match.group('body')};"
            ),
            canvas,
        )
    if accessibility_directive:
        canvas = _NUMERIC_ENTITY_LITERAL.sub(
            lambda match: f"＆＃{match.group('body')[1:]};",
            canvas,
        )
        canvas = canvas.replace("<", "‹")
    if markdown_label:
        # Mermaid State labels use Markdown. Identify only active delimiter pairs,
        # in linear passes, so malformed bounded OCR text cannot trigger regex
        # backtracking or lose ordinary punctuation from the canvas.
        characters = list(canvas)
        replacements: dict[int, str] = {}

        backtick_runs: list[tuple[int, int]] = []
        index = 0
        while index < len(characters):
            if characters[index] != "`":
                index += 1
                continue
            end = index + 1
            while end < len(characters) and characters[end] == "`":
                end += 1
            backtick_runs.append((index, end - index))
            index = end
        next_same_run: list[int | None] = [None] * len(backtick_runs)
        next_by_width: dict[int, int] = {}
        for run_index in range(len(backtick_runs) - 1, -1, -1):
            width = backtick_runs[run_index][1]
            next_same_run[run_index] = next_by_width.get(width)
            next_by_width[width] = run_index
        run_index = 0
        while run_index < len(backtick_runs):
            closing_index = next_same_run[run_index]
            if closing_index is None:
                run_index += 1
                continue
            opening_start, opening_width = backtick_runs[run_index]
            closing_start, closing_width = backtick_runs[closing_index]
            opening_end = opening_start + opening_width
            if closing_start > opening_end:
                closing_end = closing_start + closing_width
                for position in range(opening_start, closing_end):
                    if characters[position] == "`":
                        replacements[position] = "｀"
                run_index = closing_index + 1
            else:  # pragma: no cover - adjacent runs are collected as one run
                run_index += 1

        bracket_stack: list[int] = []
        bracket_open_by_close: dict[int, int] = {}
        parenthesis_stack: list[int] = []
        parenthesis_close_by_open: dict[int, int] = {}
        for index, character in enumerate(characters):
            if character == "[":
                bracket_stack.append(index)
            elif character == "]" and bracket_stack:
                bracket_open_by_close[index] = bracket_stack.pop()
            elif character == "(":
                parenthesis_stack.append(index)
            elif character == ")" and parenthesis_stack:
                parenthesis_close_by_open[parenthesis_stack.pop()] = index
        for closing_bracket, opening_bracket in bracket_open_by_close.items():
            opening_parenthesis = closing_bracket + 1
            if opening_parenthesis in parenthesis_close_by_open:
                replacements[opening_bracket] = "［"
                replacements[closing_bracket] = "］"

        for delimiter, compatibility in (("*", "∗"), ("_", "＿"), ("~", "～")):
            content_prefix = [0]
            for character in characters:
                content_prefix.append(
                    content_prefix[-1] + int(not character.isspace() and character != delimiter)
                )
            runs: list[tuple[int, int]] = []
            index = 0
            while index < len(characters):
                if characters[index] != delimiter:
                    index += 1
                    continue
                end = index + 1
                while end < len(characters) and characters[end] == delimiter:
                    end += 1
                runs.append((index, end))
                index = end

            pending_by_width: dict[int, list[int]] = {}
            pending_all: list[int] = []
            consumed_openers: set[int] = set()
            for current_index, (start, end) in enumerate(runs):
                width = end - start
                before = characters[start - 1] if start else None
                following = characters[end] if end < len(characters) else None
                can_open = following is not None and not following.isspace()
                can_close = before is not None and not before.isspace()
                if delimiter == "_":
                    can_open = can_open and (
                        before is None or (not before.isalnum() and before != "_")
                    )
                    can_close = can_close and (
                        following is None or (not following.isalnum() and following != "_")
                    )
                elif delimiter == "~":
                    can_open = can_open and width in {1, 2}
                    can_close = can_close and width in {1, 2}

                opener_index: int | None = None
                if can_close:
                    same_width = pending_by_width.get(width, [])
                    while same_width and same_width[-1] in consumed_openers:
                        same_width.pop()
                    if same_width:
                        candidate_index = same_width[-1]
                        candidate_end = runs[candidate_index][1]
                        if content_prefix[start] > content_prefix[candidate_end]:
                            opener_index = same_width.pop()
                    if opener_index is None:
                        while pending_all and pending_all[-1] in consumed_openers:
                            pending_all.pop()
                        if pending_all:
                            candidate_index = pending_all[-1]
                            candidate_end = runs[candidate_index][1]
                            if content_prefix[start] > content_prefix[candidate_end]:
                                opener_index = pending_all.pop()
                if opener_index is not None:
                    consumed_openers.add(opener_index)
                    opening_start, opening_end = runs[opener_index]
                    for position in range(opening_start, opening_end):
                        replacements[position] = compatibility
                    for position in range(start, end):
                        replacements[position] = compatibility
                elif can_open:
                    pending_by_width.setdefault(width, []).append(current_index)
                    pending_all.append(current_index)

        canvas = "".join(
            replacements.get(index, character) for index, character in enumerate(characters)
        )
    if quoted_node:
        canvas = canvas.replace('"', "″")
    if markdown_label:
        characters = list(canvas)
        original_characters = tuple(characters)
        final_index = len(characters) - 1
        for index, character in enumerate(original_characters):
            if character != "\\":
                continue
            previous = original_characters[index - 1] if index else None
            following = original_characters[index + 1] if index < final_index else None
            if (
                index == 0
                or index == final_index
                or previous == "\\"
                or following == "\\"
                or following in _COMMONMARK_ESCAPABLE
            ):
                characters[index] = "∖"
        canvas = "".join(characters)
    source = canvas
    if markdown_label:
        source = source.replace("@", f"@{_ZERO_WIDTH_SPACE}")
        source = _MARKDOWN_WWW_AUTOLINK.sub(
            lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
            source,
        )
    source = _DIRECTIVE_OPEN.sub(
        lambda match: f"%{_ZERO_WIDTH_SPACE}%{match.group('gap')}{{",
        source,
    )
    source = source.replace("//", f"/{_ZERO_WIDTH_SPACE}/")
    source = source.replace("<", f"<{_ZERO_WIDTH_SPACE}")
    source = _CSS_IMPORT.sub(
        lambda match: f"{match.group(0)[:3]}{_ZERO_WIDTH_SPACE}{match.group(0)[3:]}",
        source,
    )
    source = _DANGEROUS_SCHEME.sub(
        lambda match: f"{match.group(0)[:-1]}{_ZERO_WIDTH_SPACE}:",
        source,
    )
    source = _REMOTE_ICON.sub(
        lambda match: (
            f"{match.group(0)[:4]}{_ZERO_WIDTH_SPACE}{match.group(0)[4:]}"
            if match.group(0).casefold() == "iconify"
            else match.group(0).replace(":", f"{_ZERO_WIDTH_SPACE}:")
        ),
        source,
    )
    source = _ACTIVE_CALLBACK.sub(
        lambda match: match.group(0).replace("(", f"{_ZERO_WIDTH_SPACE}("),
        source,
    )
    source = _CONTROL_WORD.sub(
        lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
        source,
    )
    source = _CONFIG.sub(
        lambda match: match.group(0).replace(":", f"{_ZERO_WIDTH_SPACE}:"),
        source,
    )
    source = source.replace("---", f"-{_ZERO_WIDTH_SPACE}--")
    terminal_canvas = source.replace(_ZERO_WIDTH_SPACE, "")
    return semantic, source, terminal_canvas, terminal_canvas != semantic


@dataclass(frozen=True, slots=True)
class StateAccessibilityPlan:
    title_semantic: str
    title_source: str
    title_canvas: str
    description_semantic: str
    description_source: str
    description_canvas: str
    compatibility_substitutions: bool


def plan_state_accessibility(
    ir: dict[str, Any],
    *,
    experimental: bool,
    state_plan: StatePlan | None = None,
) -> StateAccessibilityPlan:
    validated_ir = validated_state_accessibility_ir(ir)
    if state_plan is None:
        state_plan = plan_state_records(validated_ir)
    accessibility_ir = {
        field: validated_ir[field] for field in _STATE_METADATA_FIELDS if field in validated_ir
    }
    accessibility_ir["states"] = [
        {"id": node.emitted_id, "label": node.semantic_label} for node in state_plan.nodes
    ]
    accessibility_ir["transitions"] = [
        {"source": transition.source_id, "target": transition.target_id}
        for transition in state_plan.transitions
    ]
    resolved = resolve_accessibility(accessibility_ir, "state", experimental=experimental)
    _, title_source, title_canvas, title_substituted = _state_terminal_text(
        resolved.title,
        context="state accessible title",
        quoted_node=False,
        markdown_label=False,
        accessibility_directive=True,
    )
    _, description_source, description_canvas, description_substituted = _state_terminal_text(
        resolved.description,
        context="state accessible description",
        quoted_node=False,
        markdown_label=False,
        accessibility_directive=True,
    )
    return StateAccessibilityPlan(
        title_semantic=resolved.title,
        title_source=title_source,
        title_canvas=title_canvas,
        description_semantic=resolved.description,
        description_source=description_source,
        description_canvas=description_canvas,
        compatibility_substitutions=title_substituted or description_substituted,
    )


def enrich_state_accessibility_ir(
    ir: dict[str, Any],
    *,
    experimental: bool,
    state_plan: StatePlan | None = None,
) -> dict[str, Any]:
    """Return a validated State snapshot with hook-free resolved metadata."""

    validated_ir = validated_state_accessibility_ir(ir)
    if state_plan is None:
        state_plan = plan_state_records(validated_ir)
    accessibility = plan_state_accessibility(
        validated_ir,
        experimental=experimental,
        state_plan=state_plan,
    )
    return {
        **validated_ir,
        "acc_title": accessibility.title_semantic,
        "acc_description": accessibility.description_semantic,
    }


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


def _state_id_map(items: list[dict[str, Any]]) -> dict[str, str]:
    """Avoid Mermaid State lexer keywords without losing source identities."""

    normalized = _id_map(items, context="state")
    protected_ids = set(normalized.values())
    result: dict[str, str] = {}
    used_ids: set[str] = set()
    for index, (source_id, rendered) in enumerate(normalized.items(), start=1):
        emitted = rendered
        if rendered.casefold() in _STATE_RESERVED_IDENTIFIERS or "iconify" in rendered.casefold():
            # Do not retain the unsafe token inside the alias: the strict remote-icon
            # scanner intentionally rejects ``iconify`` as a substring.
            base = f"mmx_state_id_{index}"
            emitted = base
            suffix = 2
            while emitted in protected_ids or emitted in used_ids:
                emitted = f"{base}_{suffix}"
                suffix += 1
        result[source_id] = emitted
        used_ids.add(emitted)
    return result


def _accessibility(ir: dict[str, Any], diagram_type: str, *, experimental: bool) -> list[str]:
    resolved = resolve_accessibility(ir, diagram_type, experimental=experimental)
    return [
        f"    accTitle: {_text(resolved.title)}",
        f"    accDescr: {_text(resolved.description)}",
    ]


@dataclass(frozen=True, slots=True)
class StateNodePlan:
    source_record: dict[str, Any]
    source_id: str
    emitted_id: str
    kind: str
    semantic_label: str
    code_label: str
    visible_label: str


@dataclass(frozen=True, slots=True)
class StateTransitionPlan:
    source_record: dict[str, Any]
    emitted_id: str
    source_id: str
    target_id: str
    code_label: str | None
    visible_label: str | None


@dataclass(frozen=True, slots=True)
class StatePlan:
    nodes: tuple[StateNodePlan, ...]
    transitions: tuple[StateTransitionPlan, ...]
    direction: str | None
    compatibility_substitutions: bool


def plan_state_records(ir: dict[str, Any]) -> StatePlan:
    """Validate and freeze the exact State identities and visible text."""

    states = _objects(ir.get("states"), context="state IR", required=True)
    transitions = _objects(ir.get("transitions", []), context="state transitions")
    id_map = _state_id_map(states)
    direction = ir.get("direction")
    if direction is not None and (
        not isinstance(direction, str) or direction not in {"TB", "BT", "LR", "RL"}
    ):
        raise SerializationError("state direction must be TB, BT, LR, or RL")

    planned_nodes: list[StateNodePlan] = []
    compatibility_substitutions = False
    for index, state in enumerate(states, start=1):
        source_id = str(state["id"]).strip()
        kind = str(state.get("kind") or "state").lower()
        if kind not in {"state", "choice", "fork", "join"}:
            raise SerializationError(f"state {index} has unsupported kind: {kind}")
        if kind == "state":
            raw_label = state.get("label")
            source_label = (
                source_id
                if raw_label is None or (type(raw_label) is str and raw_label == "")
                else raw_label
            )
            semantic_label, code_label, visible_label, substituted = _state_terminal_text(
                source_label,
                context=f"state {index} label",
                quoted_node=True,
                markdown_label=True,
                accessibility_directive=False,
            )
            compatibility_substitutions |= substituted
        else:
            semantic_label = source_id
            code_label = source_id
            visible_label = source_id
        planned_nodes.append(
            StateNodePlan(
                source_record=state,
                source_id=source_id,
                emitted_id=id_map[source_id],
                kind=kind,
                semantic_label=semantic_label,
                code_label=code_label,
                visible_label=visible_label,
            )
        )

    planned_transitions: list[StateTransitionPlan] = []
    for index, transition in enumerate(transitions, start=1):
        _evidence(transition, context=f"state transition {index}")
        source_raw = str(transition.get("source") or "").strip()
        target_raw = str(transition.get("target") or "").strip()
        source = "[*]" if source_raw == "[*]" else id_map.get(source_raw)
        target = "[*]" if target_raw == "[*]" else id_map.get(target_raw)
        if source is None or target is None:
            raise SerializationError(f"state transition {index} references an unknown endpoint")
        code_label = None
        visible_label = None
        raw_label = transition.get("label")
        if raw_label is not None and not (type(raw_label) is str and raw_label == ""):
            semantic_label, code_label, visible_label, substituted = _state_terminal_text(
                raw_label,
                context=f"state transition {index} label",
                quoted_node=False,
                markdown_label=True,
                accessibility_directive=False,
            )
            if any(character in semantic_label for character in (":", ";")):
                raise SerializationError(
                    f"state transition {index} label contains unsupported ':' or ';'"
                )
            compatibility_substitutions |= substituted
        planned_transitions.append(
            StateTransitionPlan(
                source_record=transition,
                emitted_id=f"state_transition_{index}",
                source_id=source,
                target_id=target,
                code_label=code_label,
                visible_label=visible_label,
            )
        )
    return StatePlan(
        nodes=tuple(planned_nodes),
        transitions=tuple(planned_transitions),
        direction=direction,
        compatibility_substitutions=compatibility_substitutions,
    )


def serialize_state(ir: dict[str, Any], *, experimental: bool = False) -> str:
    """Serialize evidence-backed states and transitions to ``stateDiagram-v2``.

    Initial and terminal states are represented only when a transition explicitly
    uses ``[*]`` as its source or target.
    """

    accessibility_ir = validated_state_accessibility_ir(ir)
    plan = plan_state_records(accessibility_ir)
    accessibility = plan_state_accessibility(
        accessibility_ir,
        experimental=experimental,
        state_plan=plan,
    )
    lines = [
        "stateDiagram-v2",
        f"    accTitle: {accessibility.title_source}",
        f"    accDescr: {accessibility.description_source}",
    ]
    if plan.direction is not None:
        lines.append(f"    direction {plan.direction}")

    for state in plan.nodes:
        if state.kind == "state":
            lines.append(f'    state "{state.code_label}" as {state.emitted_id}')
        else:
            lines.append(f"    state {state.emitted_id} <<{state.kind}>>")

    for transition in plan.transitions:
        suffix = f" : {transition.code_label}" if transition.code_label is not None else ""
        lines.append(f"    {transition.source_id} --> {transition.target_id}{suffix}")
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
    lines = ["classDiagram", *_accessibility(ir, "class", experimental=experimental)]
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
    lines = ["erDiagram", *_accessibility(ir, "er", experimental=experimental)]
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
