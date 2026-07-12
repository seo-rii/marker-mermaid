"""Evidence-strict serializers for experimental planning diagrams.

The serializers in this module target Mermaid 11.16's ``kanban`` and
``gitGraph`` grammars and the portable ``timeline`` fallback for ``journey``.
Native journey rendering currently emits a forbidden SVG ``foreignObject``, so
the result contract exposes the fallback instead of claiming a strict-safe
native result.  Every score, card/column identifier, commit identifier, and
merge endpoint must be present in typed IR.  Mermaid defaults that would
synthesize identifiers (notably anonymous GitGraph commits and merges) are
never used.

Typed IR shapes
---------------

``journey``
    ``sections`` contains objects with a required ``title`` and non-empty
    ``tasks``.  Each task requires ``label``, an integer ``score`` from 1 to 5,
    and a non-empty list of ``actors``.  Sections become timeline sections and
    score/actor evidence is retained as event text.

``kanban``
    ``columns`` and ``cards`` are separate lists.  Both require explicit IDs;
    each card references its column through ``column_id``.  Keeping the
    reference explicit lets the serializer reject unresolved status/column
    evidence rather than assigning a card heuristically.

``gitgraph``
    ``initial_branch`` must explicitly name Mermaid's portable ``main`` branch.
    Ordered ``operations`` are declarative objects of type ``commit``,
    ``branch``, or ``merge``.  Commits name their branch, branches name their
    source branch, and merges name both source and target branches.  All commit
    and merge IDs are explicit and globally unique.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from marker_mermaid.accessibility import resolve_accessibility
from marker_mermaid.serialization import SerializationResult
from marker_mermaid.serializers import SerializationError

_EXPERIMENTAL_WARNING = (
    "This planning diagram uses an experimental Mermaid reconstruction and requires review."
)
_COMMIT_TYPES = {"NORMAL", "REVERSE", "HIGHLIGHT"}


def _text(value: Any) -> str:
    """Encode evidence as one safe Mermaid text token.

    Entity encoding is intentionally broader than quote escaping.  It prevents
    labels containing URLs, directives, HTML, or callback-like text from
    becoming active Mermaid source while retaining the visible evidence.
    """

    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if "\x00" in text:
        raise SerializationError("text evidence must not contain NUL bytes")
    entities = {
        "&": "&#38;",
        "\\": "&#92;",
        '"': "&#34;",
        "<": "&#60;",
        ">": "&#62;",
        "[": "&#91;",
        "]": "&#93;",
        "{": "&#123;",
        "}": "&#125;",
        "(": "&#40;",
        ")": "&#41;",
        ":": "&#58;",
        "/": "&#47;",
        "%": "&#37;",
        "@": "&#64;",
    }
    return "".join(entities.get(character, character) for character in text)


def _required_text(value: Any, *, field: str) -> str:
    if value is None:
        raise SerializationError(f"{field} requires non-empty text evidence")
    text = _text(value)
    if not text:
        raise SerializationError(f"{field} requires non-empty text evidence")
    return text


def _required_id(value: Any, *, field: str) -> tuple[str, str]:
    """Return the source ID and its deterministic Mermaid identifier."""

    if not isinstance(value, str) or not value.strip():
        raise SerializationError(f"{field} requires an explicit non-empty ID")
    source_id = value.strip()
    if "\x00" in source_id:
        raise SerializationError(f"{field} must not contain NUL bytes")
    output_id = re.sub(r"[^A-Za-z0-9_]", "_", source_id).strip("_")
    if not output_id:
        raise SerializationError(f"{field} cannot be represented as a portable Mermaid ID")
    if output_id[0].isdigit():
        output_id = f"n_{output_id}"
    return source_id, output_id


def _accessibility(ir: Mapping[str, Any], diagram_type: str, *, experimental: bool) -> list[str]:
    resolved = resolve_accessibility(ir, diagram_type, experimental=experimental)
    return [
        f"    accTitle: {_text(resolved.title)}",
        f"    accDescr: {_text(resolved.description)}",
    ]


def _native_result(diagram_type: str, code: str) -> SerializationResult:
    return SerializationResult.native(
        diagram_type,
        code,
        warnings=(_EXPERIMENTAL_WARNING,),
        stability="experimental",
    )


def serialize_journey(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    """Serialize journey evidence to a strict-safe ``timeline`` fallback."""

    sections = ir.get("sections")
    if not isinstance(sections, list) or not sections:
        raise SerializationError("journey IR requires a non-empty sections list")
    lines = ["timeline"]
    if ir.get("title"):
        lines.append(f"    title {_text(ir['title'])}")
    for section_index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise SerializationError("journey sections must be objects")
        title = _required_text(
            section.get("title") or section.get("label"),
            field=f"journey section {section_index}.title",
        )
        tasks = section.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise SerializationError(
                f"journey section {section_index}.tasks requires a non-empty list"
            )
        lines.append(f"    section {title}")
        for task_index, task in enumerate(tasks, start=1):
            if not isinstance(task, dict):
                raise SerializationError("journey tasks must be objects")
            label = _required_text(
                task.get("label") or task.get("text"),
                field=f"journey section {section_index} task {task_index}.label",
            )
            score = task.get("score")
            if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
                raise SerializationError(
                    f"journey section {section_index} task {task_index}.score "
                    "requires an explicit integer from 1 to 5"
                )
            actors = task.get("actors")
            if not isinstance(actors, list) or not actors:
                raise SerializationError(
                    f"journey section {section_index} task {task_index}.actors "
                    "requires a non-empty list"
                )
            actor_text = [
                _required_text(
                    actor,
                    field=(
                        f"journey section {section_index} task {task_index} actor {actor_index}"
                    ),
                )
                for actor_index, actor in enumerate(actors, start=1)
            ]
            if len(actor_text) != len(set(actor_text)):
                raise SerializationError("journey task actors must be unique")
            lines.append(f"        {label} : Score {score} : Actors {', '.join(actor_text)}")
    return SerializationResult.fallback(
        "journey",
        "timeline",
        "\n".join(lines) + "\n",
        warnings=(
            "Requested journey was emitted as timeline because Mermaid 11.16 journey SVG "
            "uses forbidden foreignObject content.",
            "Journey scores and actors are preserved as timeline event text; journey "
            "scoring layout is not preserved.",
        ),
        stability="experimental",
    )


def serialize_kanban(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    """Serialize columns and explicitly referenced cards to native ``kanban``."""

    columns = ir.get("columns")
    cards = ir.get("cards", [])
    if not isinstance(columns, list) or not columns:
        raise SerializationError("kanban IR requires a non-empty columns list")
    if not isinstance(cards, list):
        raise SerializationError("kanban cards must be a list")

    column_records: list[tuple[str, str, str]] = []
    column_ids: dict[str, str] = {}
    output_ids: set[str] = set()
    for index, column in enumerate(columns, start=1):
        if not isinstance(column, dict):
            raise SerializationError("kanban columns must be objects")
        source_id, output_id = _required_id(column.get("id"), field=f"kanban column {index}.id")
        if source_id in column_ids:
            raise SerializationError(f"duplicate kanban column ID {source_id!r}")
        if output_id in output_ids:
            raise SerializationError(
                f"kanban column IDs collide after Mermaid normalization: {output_id!r}"
            )
        output_ids.add(output_id)
        column_ids[source_id] = output_id
        label = _required_text(
            column.get("label") or column.get("title"),
            field=f"kanban column {index}.label",
        )
        column_records.append((source_id, output_id, label))

    cards_by_column: dict[str, list[tuple[str, str]]] = {source_id: [] for source_id in column_ids}
    card_source_ids: set[str] = set()
    for index, card in enumerate(cards, start=1):
        if not isinstance(card, dict):
            raise SerializationError("kanban cards must be objects")
        source_id, output_id = _required_id(card.get("id"), field=f"kanban card {index}.id")
        if source_id in card_source_ids or source_id in column_ids:
            raise SerializationError(f"duplicate kanban ID {source_id!r}")
        if output_id in output_ids:
            raise SerializationError(
                f"kanban IDs collide after Mermaid normalization: {output_id!r}"
            )
        output_ids.add(output_id)
        card_source_ids.add(source_id)
        column_id = card.get("column_id")
        if not isinstance(column_id, str) or column_id not in column_ids:
            raise SerializationError(
                f"kanban card {source_id!r} references unknown column {column_id!r}"
            )
        label = _required_text(
            card.get("label") or card.get("text"), field=f"kanban card {index}.label"
        )
        cards_by_column[column_id].append((output_id, label))

    lines = ["kanban"]
    for source_id, output_id, label in column_records:
        lines.append(f"    {output_id}[{label}]")
        for card_id, card_label in cards_by_column[source_id]:
            lines.append(f"        {card_id}[{card_label}]")
    return _native_result("kanban", "\n".join(lines) + "\n")


def _git_commit_suffix(operation: Mapping[str, Any], *, field: str) -> str:
    parts: list[str] = []
    if "tag" in operation:
        parts.append(f'tag: "{_required_text(operation.get("tag"), field=f"{field}.tag")}"')
    if "commit_type" in operation or "style" in operation:
        commit_type = str(operation.get("commit_type") or operation.get("style")).upper()
        if commit_type not in _COMMIT_TYPES:
            raise SerializationError(f"{field}.commit_type must be NORMAL, REVERSE, or HIGHLIGHT")
        parts.append(f"type: {commit_type}")
    return f" {' '.join(parts)}" if parts else ""


def serialize_gitgraph(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    """Serialize an evidence-complete operation stream to native ``gitGraph``."""

    initial_source, initial_output = _required_id(
        ir.get("initial_branch"), field="gitgraph initial_branch"
    )
    if initial_output != "main":
        raise SerializationError(
            "portable gitgraph serialization requires explicit initial_branch 'main'"
        )
    operations = ir.get("operations")
    if not isinstance(operations, list) or not operations:
        raise SerializationError("gitgraph IR requires a non-empty operations list")
    direction = ir.get("direction")
    if direction is not None:
        direction = str(direction).upper()
        if direction not in {"LR", "TB", "BT"}:
            raise SerializationError("gitgraph direction must be LR, TB, or BT")
    header = f"gitGraph {direction}:" if direction else "gitGraph"
    lines = [header, *_accessibility(ir, "gitgraph", experimental=experimental)]

    branch_ids = {initial_source: initial_output}
    normalized_branches = {initial_output}
    branch_heads: dict[str, str | None] = {initial_source: None}
    current_branch = initial_source
    commit_ids: set[str] = set()

    for index, operation in enumerate(operations, start=1):
        if not isinstance(operation, dict):
            raise SerializationError("gitgraph operations must be objects")
        operation_type = str(operation.get("type") or "").lower()
        field = f"gitgraph operation {index}"
        if operation_type == "commit":
            branch = operation.get("branch")
            if not isinstance(branch, str) or branch not in branch_ids:
                raise SerializationError(f"{field} references unknown branch {branch!r}")
            commit_id = _required_text(operation.get("id"), field=f"{field}.id")
            if commit_id in commit_ids:
                raise SerializationError(f"duplicate gitgraph commit ID {commit_id!r}")
            if current_branch != branch:
                lines.append(f"    checkout {branch_ids[branch]}")
                current_branch = branch
            lines.append(
                f'    commit id: "{commit_id}"{_git_commit_suffix(operation, field=field)}'
            )
            commit_ids.add(commit_id)
            branch_heads[branch] = commit_id
        elif operation_type == "branch":
            source_branch = operation.get("from")
            if not isinstance(source_branch, str) or source_branch not in branch_ids:
                raise SerializationError(
                    f"{field} references unknown source branch {source_branch!r}"
                )
            if branch_heads[source_branch] is None:
                raise SerializationError(f"{field} cannot branch from a branch without a commit")
            source_id, output_id = _required_id(
                operation.get("name") or operation.get("id"), field=f"{field}.name"
            )
            if source_id in branch_ids:
                raise SerializationError(f"duplicate gitgraph branch ID {source_id!r}")
            if output_id in normalized_branches:
                raise SerializationError(
                    f"gitgraph branch IDs collide after Mermaid normalization: {output_id!r}"
                )
            if current_branch != source_branch:
                lines.append(f"    checkout {branch_ids[source_branch]}")
            order_suffix = ""
            if "order" in operation:
                order = operation["order"]
                if isinstance(order, bool) or not isinstance(order, int) or order < 0:
                    raise SerializationError(f"{field}.order requires a non-negative integer")
                order_suffix = f" order: {order}"
            lines.append(f"    branch {output_id}{order_suffix}")
            branch_ids[source_id] = output_id
            normalized_branches.add(output_id)
            branch_heads[source_id] = branch_heads[source_branch]
            current_branch = source_id
        elif operation_type == "merge":
            source_branch = operation.get("source")
            target_branch = operation.get("target")
            if not isinstance(source_branch, str) or source_branch not in branch_ids:
                raise SerializationError(
                    f"{field} references unknown source branch {source_branch!r}"
                )
            if not isinstance(target_branch, str) or target_branch not in branch_ids:
                raise SerializationError(
                    f"{field} references unknown target branch {target_branch!r}"
                )
            if source_branch == target_branch:
                raise SerializationError(f"{field} cannot merge a branch into itself")
            if branch_heads[source_branch] is None or branch_heads[target_branch] is None:
                raise SerializationError(f"{field} requires both branches to contain commits")
            if branch_heads[source_branch] == branch_heads[target_branch]:
                raise SerializationError(f"{field} branches have the same head")
            commit_id = _required_text(operation.get("id"), field=f"{field}.id")
            if commit_id in commit_ids:
                raise SerializationError(f"duplicate gitgraph commit ID {commit_id!r}")
            if current_branch != target_branch:
                lines.append(f"    checkout {branch_ids[target_branch]}")
                current_branch = target_branch
            lines.append(
                f'    merge {branch_ids[source_branch]} id: "{commit_id}"'
                f"{_git_commit_suffix(operation, field=field)}"
            )
            commit_ids.add(commit_id)
            branch_heads[target_branch] = commit_id
        else:
            raise SerializationError(f"{field} has unsupported type {operation_type!r}")

    return _native_result("gitgraph", "\n".join(lines) + "\n")


PLANNING_SERIALIZERS: dict[str, Callable[[Mapping[str, Any]], SerializationResult]] = {
    "journey": serialize_journey,
    "kanban": serialize_kanban,
    "gitgraph": serialize_gitgraph,
}


def serialize_planning(
    diagram_type: str, ir: Mapping[str, Any], *, experimental: bool = False
) -> SerializationResult:
    """Dispatch a planning typed IR through the result-aware contract."""

    serializer = PLANNING_SERIALIZERS.get(diagram_type)
    if serializer is None:
        raise SerializationError(f"no planning typed serializer for {diagram_type}")
    return serializer(ir, experimental=experimental)
