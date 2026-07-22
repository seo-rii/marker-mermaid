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
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from marker_mermaid.accessibility import resolve_accessibility
from marker_mermaid.models import MAX_ID_CHARS, MAX_SCENE_GROUPS
from marker_mermaid.serialization import SerializationResult
from marker_mermaid.serializers import SerializationError

_EXPERIMENTAL_WARNING = (
    "This planning diagram uses an experimental Mermaid reconstruction and requires review."
)
_COMMIT_TYPES = {"NORMAL", "REVERSE", "HIGHLIGHT"}
_GITGRAPH_OPERATION_FIELDS = {
    "commit": frozenset({"type", "id", "branch", "tag", "commit_type", "style"}),
    "branch": frozenset({"type", "id", "name", "from", "order"}),
    "merge": frozenset({"type", "id", "source", "target", "tag", "commit_type", "style"}),
}
_GITGRAPH_KNOWN_OPERATION_FIELDS = frozenset().union(*_GITGRAPH_OPERATION_FIELDS.values())
MAX_PLANNING_RECORDS = 2_000
MAX_PLANNING_OUTPUT_CHARS = 50_000
MAX_PLANNING_OUTPUT_LINES = 5_000
# Backward-compatible name retained for callers that used the fallback-specific
# constant before the source budget became common to every planning serializer.
MAX_FLOWCHART_FALLBACK_LINES = MAX_PLANNING_OUTPUT_LINES
_GITGRAPH_SEPARATOR = "\u200b"
_GITGRAPH_ANGLE_WARNING = (
    "GitGraph text containing angle brackets uses visible single-angle glyphs "
    "(‹ and ›) because Mermaid 11.16 does not preserve literal angle brackets."
)
_KANBAN_GLYPH_WARNING = (
    "Kanban labels use visible double-prime (″) and modifier-grave (ˋ) glyphs "
    "for quote and backtick evidence that Mermaid 11.16 markdown labels cannot "
    "preserve literally."
)
_FLOWCHART_GLYPH_WARNING = (
    "Flowchart fallback text uses visible double-prime (″) and set-minus (∖) "
    "glyphs for quote and backslash evidence that the portable serializer cannot "
    "preserve literally."
)
_JOURNEY_GLYPH_WARNING = (
    "Journey timeline fallback text uses visible ratio-colon (∶) for item colons and "
    "fullwidth entity prefixes (＆ and ＃) for entity-like item text; title angle "
    "brackets use single-angle glyphs (‹ and ›). Mermaid 11.16 timeline cannot "
    "preserve those literal forms."
)


@dataclass(frozen=True, slots=True)
class KanbanColumnPlan:
    """One validated column identity shared by native, fallback, and Scene output."""

    source_record: Mapping[str, Any]
    source_id: str
    emitted_id: str
    label: str
    member_source_ids: tuple[str, ...]
    member_emitted_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KanbanCardPlan:
    """One validated card and its resolved source/output column identities."""

    source_record: Mapping[str, Any]
    source_id: str
    emitted_id: str
    label: str
    column_source_id: str
    column_emitted_id: str


@dataclass(frozen=True, slots=True)
class KanbanPlan:
    """Immutable evidence-complete Kanban serialization plan."""

    columns: tuple[KanbanColumnPlan, ...]
    cards: tuple[KanbanCardPlan, ...]


@dataclass(frozen=True, slots=True)
class GitGraphOperationPlan:
    """One validated operation in source order.

    Commit and merge operations have ``element_id`` and ``semantic_id`` values.
    Branch operations instead carry the created branch in the owning-branch
    fields.  A merge is owned by its target branch and records its source branch
    separately.
    """

    source_record: Mapping[str, Any]
    operation_index: int
    operation_type: str
    checkout_emitted_id: str | None
    owning_branch_source_id: str
    owning_branch_emitted_id: str
    source_branch_source_id: str | None
    source_branch_emitted_id: str | None
    element_id: str | None
    semantic_id: str | None
    parent_element_ids: tuple[str, ...]
    tag: str | None
    commit_type: str | None
    order: int | None


@dataclass(frozen=True, slots=True)
class GitGraphBranchPlan:
    """One branch group and the commits authored on that branch."""

    source_record: Mapping[str, Any] | None
    source_id: str
    emitted_id: str
    parent_source_id: str | None
    order: int | None
    member_element_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GitGraphPlan:
    """Immutable replay and topology plan for GitGraph output."""

    initial_branch_source_id: str
    initial_branch_emitted_id: str
    direction: str | None
    operations: tuple[GitGraphOperationPlan, ...]
    commits: tuple[GitGraphOperationPlan, ...]
    branches: tuple[GitGraphBranchPlan, ...]


def _required_semantic_text(value: Any, *, field: str) -> str:
    """Normalize line breaks without applying a Mermaid grammar encoding."""

    if value is None:
        raise SerializationError(f"{field} requires non-empty text evidence")
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if "\x00" in text:
        raise SerializationError(f"{field} must not contain NUL bytes")
    if not text:
        raise SerializationError(f"{field} requires non-empty text evidence")
    return text


def _compatible_text_alias(
    record: Mapping[str, Any],
    primary: str,
    alias: str,
    *,
    field: str,
) -> Any:
    """Resolve one compatibility alias without hiding conflicting evidence."""

    primary_present = primary in record and record.get(primary) is not None
    alias_present = alias in record and record.get(alias) is not None
    if primary_present and alias_present:
        primary_text = _required_semantic_text(record.get(primary), field=f"{field}.{primary}")
        alias_text = _required_semantic_text(record.get(alias), field=f"{field}.{alias}")
        if primary_text != alias_text:
            raise SerializationError(f"{field} aliases {primary!r} and {alias!r} must agree")
        return primary_text
    if primary_present:
        return record.get(primary)
    return record.get(alias)


def _preflight_planning_code(code: str, *, diagram_type: str) -> str:
    """Apply CandidateValidator's default source budgets before returning code."""

    if code.count("\n") + 1 > MAX_PLANNING_OUTPUT_LINES:
        raise SerializationError(
            f"{diagram_type} output exceeds source-line limit of {MAX_PLANNING_OUTPUT_LINES}"
        )
    if len(code) > MAX_PLANNING_OUTPUT_CHARS:
        raise SerializationError(
            f"{diagram_type} output exceeds source-character limit of {MAX_PLANNING_OUTPUT_CHARS}"
        )
    return code


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


def _visible_semantic_text(value: Any, *, field: str) -> str:
    """Return visible evidence without user-authored hidden controls."""

    text = _required_semantic_text(value, field=field)
    for character in text:
        if unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}:
            raise SerializationError(f"{field} must not contain invisible or control separators")
    return text


def _gitgraph_semantic_text(value: Any, *, field: str) -> str:
    """Return text accepted by GitGraph without user-authored hidden controls."""

    return _visible_semantic_text(value, field=field)


def _split_entity_evidence(match: re.Match[str]) -> str:
    body = match.group("body")
    if body.startswith("#x") or body.startswith("#X"):
        return f"&{body[:2]}{_GITGRAPH_SEPARATOR}{body[2:]};"
    if body.startswith("#"):
        return f"&#{_GITGRAPH_SEPARATOR}{body[1:]};"
    return f"&{body[0]}{_GITGRAPH_SEPARATOR}{body[1:]};"


def _neutralize_active_text(text: str) -> str:
    """Insert renderer-invisible separators only into active/source-like tokens."""

    text = re.sub(
        r"&(?P<body>#[0-9]+|#x[0-9A-F]+|[A-Z][A-Z0-9]+);",
        _split_entity_evidence,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"%%(?=\s*\{)",
        f"%{_GITGRAPH_SEPARATOR}%",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(call|callback)(?=\s*\()",
        rf"\1{_GITGRAPH_SEPARATOR}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(https?|ftp|file|data|javascript)(?=:)",
        rf"\1{_GITGRAPH_SEPARATOR}",
        text,
        flags=re.IGNORECASE,
    )
    text = text.replace("//", f"/{_GITGRAPH_SEPARATOR}/")
    text = re.sub(
        r"@(?=import\b)",
        f"@{_GITGRAPH_SEPARATOR}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(iconify)(?=\b)",
        lambda match: f"{match.group(0)[:4]}{_GITGRAPH_SEPARATOR}{match.group(0)[4:]}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(fa|logos)(?=:)",
        rf"\1{_GITGRAPH_SEPARATOR}",
        text,
        flags=re.IGNORECASE,
    )
    # A semicolon terminates a Mermaid statement even inside GitGraph and
    # Markdown strings.  The separator keeps a following click/style token inert.
    return text.replace(";", f";{_GITGRAPH_SEPARATOR}")


def _gitgraph_text(value: Any, *, field: str) -> tuple[str, bool]:
    """Encode one quoted/directive GitGraph value for Mermaid 11.16.

    Mermaid's GitGraph parser does not decode HTML entities like Flowchart
    labels do.  Backslash escaping therefore preserves quotes and backslashes,
    while invisible separators neutralize scanner syntax without changing the
    visible SVG text.  Literal angle brackets are the one compatibility loss:
    Mermaid renders them as entity text, so visible single-angle glyphs are
    substituted deliberately and reported to the caller.
    """

    text = _gitgraph_semantic_text(value, field=field)
    substituted_angles = "<" in text or ">" in text
    text = text.replace("<", "‹").replace(">", "›")
    text = _neutralize_active_text(text)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return text, substituted_angles


def _gitgraph_directive_text(value: Any, *, field: str) -> tuple[str, bool]:
    """Encode unquoted GitGraph accessibility text without quote/backslash escapes."""

    text = _gitgraph_semantic_text(value, field=field)
    substituted_angles = "<" in text or ">" in text
    text = text.replace("<", "‹").replace(">", "›")
    return _neutralize_active_text(text), substituted_angles


def _kanban_text(value: Any, *, field: str) -> tuple[str, bool]:
    """Encode one Mermaid Markdown-long Kanban label."""

    text = _visible_semantic_text(value, field=field)
    substituted_glyphs = '"' in text or "`" in text
    text = _neutralize_active_text(text)
    text = text.replace("<", f"<{_GITGRAPH_SEPARATOR}")
    text = text.replace('"', "″").replace("`", "ˋ")
    return text, substituted_glyphs


def _flowchart_fallback_text(value: Any, *, field: str) -> tuple[str, bool]:
    """Neutralize active text before the portable Flowchart serializer."""

    text = _visible_semantic_text(value, field=field)
    substituted_glyphs = '"' in text or "\\" in text
    text = _neutralize_active_text(text)
    text = text.replace("<", f"<{_GITGRAPH_SEPARATOR}")
    text = text.replace('"', "″").replace("\\", "∖")
    return text, substituted_glyphs


def _journey_title_text(value: Any, *, field: str) -> tuple[str, bool]:
    text = _visible_semantic_text(value, field=field)
    substituted_glyphs = "<" in text or ">" in text
    text = _neutralize_active_text(text)
    return (
        text.replace("<", "‹").replace(">", "›"),
        substituted_glyphs,
    )


def _journey_item_text(value: Any, *, field: str) -> tuple[str, bool]:
    text = _visible_semantic_text(value, field=field)
    entity_pattern = re.compile(
        r"&(?P<body>#[0-9]+|#x[0-9A-F]+|[A-Z][A-Z0-9]+);",
        flags=re.IGNORECASE,
    )
    substituted_glyphs = ":" in text or entity_pattern.search(text) is not None
    text = re.sub(
        entity_pattern,
        lambda match: (
            f"＆＃{match.group('body')[1:]};"
            if match.group("body").startswith("#")
            else f"＆{match.group('body')};"
        ),
        text,
    )
    text = _neutralize_active_text(text)
    text = text.replace("<", f"<{_GITGRAPH_SEPARATOR}").replace(":", "∶")
    return text, substituted_glyphs


def _gitgraph_branch_id(value: Any, *, field: str) -> tuple[str, str]:
    if not isinstance(value, str):
        return _required_id(value, field=field)
    source_id = _gitgraph_semantic_text(value, field=field)
    return _required_id(source_id, field=field)


def _visible_required_id(value: Any, *, field: str) -> tuple[str, str]:
    if not isinstance(value, str):
        return _required_id(value, field=field)
    return _required_id(_visible_semantic_text(value, field=field), field=field)


def _gitgraph_accessibility(ir: Mapping[str, Any], *, experimental: bool) -> tuple[list[str], bool]:
    resolved = resolve_accessibility(ir, "gitgraph", experimental=experimental)
    title, title_substituted = _gitgraph_directive_text(resolved.title, field="gitgraph accTitle")
    description, description_substituted = _gitgraph_directive_text(
        resolved.description, field="gitgraph accDescr"
    )
    return (
        [f"    accTitle: {title}", f"    accDescr: {description}"],
        title_substituted or description_substituted,
    )


def _native_result(
    diagram_type: str, code: str, *, warnings: tuple[str, ...] = ()
) -> SerializationResult:
    return SerializationResult.native(
        diagram_type,
        code,
        warnings=(_EXPERIMENTAL_WARNING, *warnings),
        stability="experimental",
    )


def serialize_journey(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    """Serialize journey evidence to a strict-safe ``timeline`` fallback."""

    sections = ir.get("sections")
    if not isinstance(sections, list) or not sections:
        raise SerializationError("journey IR requires a non-empty sections list")
    if len(sections) > MAX_PLANNING_RECORDS:
        raise SerializationError(
            f"journey records exceed deterministic limit of {MAX_PLANNING_RECORDS}"
        )
    lines = ["timeline"]
    record_count = len(sections)
    substituted_glyphs = False
    if ir.get("title"):
        title, title_substituted = _journey_title_text(ir["title"], field="journey title")
        substituted_glyphs = substituted_glyphs or title_substituted
        lines.append(f"    title {title}")
    for section_index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise SerializationError("journey sections must be objects")
        title, title_substituted = _journey_item_text(
            _compatible_text_alias(
                section,
                "title",
                "label",
                field=f"journey section {section_index}",
            ),
            field=f"journey section {section_index}.title",
        )
        substituted_glyphs = substituted_glyphs or title_substituted
        tasks = section.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise SerializationError(
                f"journey section {section_index}.tasks requires a non-empty list"
            )
        record_count += len(tasks)
        if record_count > MAX_PLANNING_RECORDS:
            raise SerializationError(
                f"journey records exceed deterministic limit of {MAX_PLANNING_RECORDS}"
            )
        lines.append(f"    section {title}")
        for task_index, task in enumerate(tasks, start=1):
            if not isinstance(task, dict):
                raise SerializationError("journey tasks must be objects")
            label, label_substituted = _journey_item_text(
                _compatible_text_alias(
                    task,
                    "label",
                    "text",
                    field=f"journey section {section_index} task {task_index}",
                ),
                field=f"journey section {section_index} task {task_index}.label",
            )
            substituted_glyphs = substituted_glyphs or label_substituted
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
            actor_records = [
                _visible_semantic_text(
                    actor,
                    field=(
                        f"journey section {section_index} task {task_index} actor {actor_index}"
                    ),
                )
                for actor_index, actor in enumerate(actors, start=1)
            ]
            if len(actor_records) != len(set(actor_records)):
                raise SerializationError("journey task actors must be unique")
            actor_text = []
            for actor_index, actor in enumerate(actor_records, start=1):
                encoded_actor, actor_substituted = _journey_item_text(
                    actor,
                    field=(
                        f"journey section {section_index} task {task_index} actor {actor_index}"
                    ),
                )
                substituted_glyphs = substituted_glyphs or actor_substituted
                actor_text.append(encoded_actor)
            lines.append(f"        {label} : Score {score} : Actors {', '.join(actor_text)}")
    warnings = [
        "Requested journey was emitted as timeline because Mermaid 11.16 journey SVG "
        "uses forbidden foreignObject content.",
        "Journey scores and actors are preserved as timeline event text; journey "
        "scoring layout is not preserved.",
    ]
    if substituted_glyphs:
        warnings.append(_JOURNEY_GLYPH_WARNING)
    code = _preflight_planning_code(
        "\n".join(lines) + "\n",
        diagram_type="journey",
    )
    return SerializationResult.fallback(
        "journey",
        "timeline",
        code,
        warnings=tuple(warnings),
        stability="experimental",
    )


def plan_kanban_records(ir: Mapping[str, Any]) -> KanbanPlan:
    """Validate exact identities and references once for all Kanban consumers."""

    columns = ir.get("columns")
    cards = ir.get("cards", [])
    if not isinstance(columns, list) or not columns:
        raise SerializationError("kanban IR requires a non-empty columns list")
    if not isinstance(cards, list):
        raise SerializationError("kanban cards must be a list")
    if len(columns) + len(cards) > MAX_PLANNING_RECORDS:
        raise SerializationError(
            f"kanban records exceed deterministic limit of {MAX_PLANNING_RECORDS}"
        )

    column_records: list[tuple[Mapping[str, Any], str, str, str]] = []
    column_ids: dict[str, str] = {}
    normalized_ids: set[str] = set()
    for index, column in enumerate(columns, start=1):
        if not isinstance(column, dict):
            raise SerializationError("kanban columns must be objects")
        source_id, normalized_id = _visible_required_id(
            column.get("id"), field=f"kanban column {index}.id"
        )
        if len(source_id) > MAX_ID_CHARS:
            raise SerializationError(
                f"kanban column {index}.id exceeds source identifier limit of {MAX_ID_CHARS}"
            )
        if len(normalized_id) > MAX_ID_CHARS:
            raise SerializationError(
                f"kanban column {index}.id exceeds normalized identifier limit of {MAX_ID_CHARS}"
            )
        if source_id in column_ids:
            raise SerializationError(f"duplicate kanban column ID {source_id!r}")
        if normalized_id in normalized_ids:
            raise SerializationError(
                f"kanban column IDs collide after Mermaid normalization: {normalized_id!r}"
            )
        normalized_ids.add(normalized_id)
        output_id = f"kanban_column_{normalized_id}"
        if len(output_id) > MAX_ID_CHARS:
            raise SerializationError(
                f"kanban column {index}.id exceeds emitted identifier limit of {MAX_ID_CHARS}"
            )
        column_ids[source_id] = output_id
        label = _visible_semantic_text(
            _compatible_text_alias(
                column,
                "label",
                "title",
                field=f"kanban column {index}",
            ),
            field=f"kanban column {index}.label",
        )
        column_records.append((column, source_id, output_id, label))

    card_records: list[KanbanCardPlan] = []
    cards_by_column: dict[str, list[KanbanCardPlan]] = {source_id: [] for source_id in column_ids}
    card_source_ids: set[str] = set()
    for index, card in enumerate(cards, start=1):
        if not isinstance(card, dict):
            raise SerializationError("kanban cards must be objects")
        source_id, normalized_id = _visible_required_id(
            card.get("id"), field=f"kanban card {index}.id"
        )
        if len(source_id) > MAX_ID_CHARS:
            raise SerializationError(
                f"kanban card {index}.id exceeds source identifier limit of {MAX_ID_CHARS}"
            )
        if len(normalized_id) > MAX_ID_CHARS:
            raise SerializationError(
                f"kanban card {index}.id exceeds normalized identifier limit of {MAX_ID_CHARS}"
            )
        if source_id in card_source_ids or source_id in column_ids:
            raise SerializationError(f"duplicate kanban ID {source_id!r}")
        if normalized_id in normalized_ids:
            raise SerializationError(
                f"kanban IDs collide after Mermaid normalization: {normalized_id!r}"
            )
        normalized_ids.add(normalized_id)
        output_id = f"kanban_card_{normalized_id}"
        if len(output_id) > MAX_ID_CHARS:
            raise SerializationError(
                f"kanban card {index}.id exceeds emitted identifier limit of {MAX_ID_CHARS}"
            )
        card_source_ids.add(source_id)
        column_id = card.get("column_id")
        if isinstance(column_id, str):
            column_id = _visible_semantic_text(column_id, field=f"kanban card {index}.column_id")
        if not isinstance(column_id, str) or column_id not in column_ids:
            raise SerializationError(
                f"kanban card {source_id!r} references unknown column {column_id!r}"
            )
        label = _visible_semantic_text(
            _compatible_text_alias(
                card,
                "label",
                "text",
                field=f"kanban card {index}",
            ),
            field=f"kanban card {index}.label",
        )
        record = KanbanCardPlan(
            source_record=card,
            source_id=source_id,
            emitted_id=output_id,
            label=label,
            column_source_id=column_id,
            column_emitted_id=column_ids[column_id],
        )
        card_records.append(record)
        cards_by_column[column_id].append(record)

    planned_columns = tuple(
        KanbanColumnPlan(
            source_record=source_record,
            source_id=source_id,
            emitted_id=emitted_id,
            label=label,
            member_source_ids=tuple(card.source_id for card in cards_by_column[source_id]),
            member_emitted_ids=tuple(card.emitted_id for card in cards_by_column[source_id]),
        )
        for source_record, source_id, emitted_id, label in column_records
    )
    return KanbanPlan(columns=planned_columns, cards=tuple(card_records))


def _flowchart_fallback_code(
    ir: Mapping[str, Any],
    requested_type: str,
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    groups: list[dict[str, Any]] | None,
    direction: str,
    experimental: bool,
) -> tuple[str, bool]:
    """Emit a strict-scanner-safe Flowchart with original accessibility text."""

    from marker_mermaid.serializers import serialize_flowchart

    accessibility = resolve_accessibility(ir, requested_type, experimental=experimental)
    title, title_substituted = _flowchart_fallback_text(
        accessibility.title, field=f"{requested_type} fallback accTitle"
    )
    description, description_substituted = _flowchart_fallback_text(
        accessibility.description, field=f"{requested_type} fallback accDescr"
    )
    substituted_glyphs = title_substituted or description_substituted
    safe_nodes: list[dict[str, Any]] = []
    for index, node in enumerate(nodes, start=1):
        label, label_substituted = _flowchart_fallback_text(
            node.get("label"), field=f"{requested_type} fallback node {index}.label"
        )
        substituted_glyphs = substituted_glyphs or label_substituted
        safe_nodes.append({**node, "label": label})
    safe_groups = None
    if groups is not None:
        safe_groups = []
        for index, group in enumerate(groups, start=1):
            label, label_substituted = _flowchart_fallback_text(
                group.get("label"), field=f"{requested_type} fallback group {index}.label"
            )
            substituted_glyphs = substituted_glyphs or label_substituted
            safe_groups.append({**group, "label": label})
    fallback_ir = {
        **ir,
        "acc_title": title,
        "acc_description": description,
        "nodes": safe_nodes,
        "edges": edges,
        "direction": direction,
    }
    if safe_groups is not None:
        fallback_ir["groups"] = safe_groups
    code = _preflight_planning_code(
        serialize_flowchart(fallback_ir, experimental=experimental),
        diagram_type=requested_type,
    )
    return code, substituted_glyphs


def serialize_kanban(
    ir: Mapping[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> SerializationResult:
    """Serialize a shared Kanban plan to native grammar or portable Flowchart."""

    if not isinstance(native_runtime_valid, bool):
        raise SerializationError("native_runtime_valid must be a boolean")
    plan = plan_kanban_records(ir)
    if not native_runtime_valid:
        nodes = [{"id": column.emitted_id, "label": column.label} for column in plan.columns]
        nodes.extend({"id": card.emitted_id, "label": card.label} for card in plan.cards)
        edges = [
            {"source": card.column_emitted_id, "target": card.emitted_id} for card in plan.cards
        ]
        code, substituted_glyphs = _flowchart_fallback_code(
            ir,
            "kanban",
            nodes=nodes,
            edges=edges,
            groups=None,
            direction="LR",
            experimental=experimental,
        )
        warnings = [
            "CandidateValidator rejected native Kanban; column/card labels and "
            "containment edges were re-emitted as portable Flowchart.",
            "Kanban column layout and card ordering are not preserved by the Flowchart fallback.",
        ]
        if substituted_glyphs:
            warnings.append(_FLOWCHART_GLYPH_WARNING)
        return SerializationResult.fallback(
            "kanban",
            "flowchart",
            code,
            warnings=tuple(warnings),
            stability="experimental",
        )

    lines = ["kanban"]
    substituted_glyphs = False
    cards_by_id = {card.emitted_id: card for card in plan.cards}
    for column_index, column in enumerate(plan.columns, start=1):
        label, label_substituted = _kanban_text(
            column.label, field=f"kanban column {column_index}.label"
        )
        substituted_glyphs = substituted_glyphs or label_substituted
        lines.append(f'    {column.emitted_id}["{label}"]')
        for card_id in column.member_emitted_ids:
            card = cards_by_id[card_id]
            card_label, card_substituted = _kanban_text(
                card.label, field=f"kanban card {card.source_id!r}.label"
            )
            substituted_glyphs = substituted_glyphs or card_substituted
            lines.append(f'        {card.emitted_id}["{card_label}"]')
    warnings = (_KANBAN_GLYPH_WARNING,) if substituted_glyphs else ()
    code = _preflight_planning_code(
        "\n".join(lines) + "\n",
        diagram_type="kanban",
    )
    return _native_result("kanban", code, warnings=warnings)


def _gitgraph_commit_metadata(
    operation: Mapping[str, Any], *, field: str
) -> tuple[str | None, str | None]:
    tag = None
    if operation.get("tag") is not None:
        tag = _gitgraph_semantic_text(operation.get("tag"), field=f"{field}.tag")
    commit_type = None
    commit_type_value = operation.get("commit_type")
    style_value = operation.get("style")
    if commit_type_value is not None or style_value is not None:
        if commit_type_value is not None and style_value is not None:
            normalized_commit_type = str(commit_type_value).upper()
            normalized_style = str(style_value).upper()
            if normalized_commit_type != normalized_style:
                raise SerializationError(f"{field} aliases 'commit_type' and 'style' must agree")
            commit_type = normalized_commit_type
        else:
            commit_type = str(
                commit_type_value if commit_type_value is not None else style_value
            ).upper()
        if commit_type not in _COMMIT_TYPES:
            raise SerializationError(f"{field}.commit_type must be NORMAL, REVERSE, or HIGHLIGHT")
    return tag, commit_type


def plan_gitgraph_records(ir: Mapping[str, Any]) -> GitGraphPlan:
    """Replay GitGraph operations into deterministic branch and parent topology."""

    if ir.get("initial_branch") != "main":
        raise SerializationError(
            "portable gitgraph serialization requires exact initial_branch 'main'"
        )
    initial_source, initial_output = _gitgraph_branch_id(
        ir.get("initial_branch"), field="gitgraph initial_branch"
    )
    operations = ir.get("operations")
    if not isinstance(operations, list) or not operations:
        raise SerializationError("gitgraph IR requires a non-empty operations list")
    if len(operations) > MAX_PLANNING_RECORDS:
        raise SerializationError(
            f"gitgraph operations exceed deterministic limit of {MAX_PLANNING_RECORDS}"
        )
    direction = ir.get("direction")
    if direction is not None:
        direction = str(direction).upper()
        if direction not in {"LR", "TB", "BT"}:
            raise SerializationError("gitgraph direction must be LR, TB, or BT")

    branch_ids = {initial_source: initial_output}
    normalized_branches = {initial_output}
    branch_heads: dict[str, str | None] = {initial_source: None}
    branch_state: dict[str, dict[str, Any]] = {
        initial_source: {
            "source_record": None,
            "emitted_id": initial_output,
            "parent_source_id": None,
            "order": None,
            "member_element_ids": [],
        }
    }
    current_branch = initial_source
    commit_ids: set[str] = set()
    encoded_commit_ids: set[str] = set()
    planned_operations: list[GitGraphOperationPlan] = []
    planned_commits: list[GitGraphOperationPlan] = []

    for index, operation in enumerate(operations, start=1):
        if not isinstance(operation, dict):
            raise SerializationError("gitgraph operations must be objects")
        operation_type = str(operation.get("type") or "").lower()
        field = f"gitgraph operation {index}"
        allowed_fields = _GITGRAPH_OPERATION_FIELDS.get(operation_type)
        if allowed_fields is not None:
            irrelevant_fields = sorted(
                known_field
                for known_field in _GITGRAPH_KNOWN_OPERATION_FIELDS - allowed_fields
                if known_field in operation and operation.get(known_field) is not None
            )
            if irrelevant_fields:
                raise SerializationError(
                    f"{field} type {operation_type!r} does not allow known field "
                    f"{irrelevant_fields[0]!r}"
                )
        if operation_type == "commit":
            branch = operation.get("branch")
            if isinstance(branch, str):
                branch = _gitgraph_semantic_text(branch, field=f"{field}.branch")
            if not isinstance(branch, str) or branch not in branch_ids:
                raise SerializationError(f"{field} references unknown branch {branch!r}")
            commit_id = _gitgraph_semantic_text(operation.get("id"), field=f"{field}.id")
            if commit_id in commit_ids:
                raise SerializationError(f"duplicate gitgraph commit ID {commit_id!r}")
            encoded_commit_id, _ = _gitgraph_text(commit_id, field=f"{field}.id")
            if encoded_commit_id in encoded_commit_ids:
                raise SerializationError(
                    "gitgraph commit IDs collide after Mermaid grammar encoding"
                )
            tag, commit_type = _gitgraph_commit_metadata(operation, field=field)
            element_id = f"git_commit_{index}"
            head = branch_heads[branch]
            planned = GitGraphOperationPlan(
                source_record=operation,
                operation_index=index,
                operation_type="commit",
                checkout_emitted_id=(branch_ids[branch] if current_branch != branch else None),
                owning_branch_source_id=branch,
                owning_branch_emitted_id=branch_ids[branch],
                source_branch_source_id=None,
                source_branch_emitted_id=None,
                element_id=element_id,
                semantic_id=commit_id,
                parent_element_ids=((head,) if head is not None else ()),
                tag=tag,
                commit_type=commit_type,
                order=None,
            )
            planned_operations.append(planned)
            planned_commits.append(planned)
            commit_ids.add(commit_id)
            encoded_commit_ids.add(encoded_commit_id)
            branch_heads[branch] = element_id
            branch_state[branch]["member_element_ids"].append(element_id)
            current_branch = branch
        elif operation_type == "branch":
            source_branch = operation.get("from")
            if isinstance(source_branch, str):
                source_branch = _gitgraph_semantic_text(source_branch, field=f"{field}.from")
            if not isinstance(source_branch, str) or source_branch not in branch_ids:
                raise SerializationError(
                    f"{field} references unknown source branch {source_branch!r}"
                )
            if branch_heads[source_branch] is None:
                raise SerializationError(f"{field} cannot branch from a branch without a commit")
            source_id, output_id = _gitgraph_branch_id(
                _compatible_text_alias(
                    operation,
                    "name",
                    "id",
                    field=field,
                ),
                field=f"{field}.name",
            )
            if source_id in branch_ids:
                raise SerializationError(f"duplicate gitgraph branch ID {source_id!r}")
            if output_id in normalized_branches:
                raise SerializationError(
                    f"gitgraph branch IDs collide after Mermaid normalization: {output_id!r}"
                )
            if len(branch_ids) >= MAX_SCENE_GROUPS:
                raise SerializationError(
                    f"gitgraph branch count exceeds Scene group limit of {MAX_SCENE_GROUPS}"
                )
            order = None
            if "order" in operation:
                order = operation["order"]
                if (
                    isinstance(order, bool)
                    or not isinstance(order, int)
                    or not 0 <= order <= MAX_PLANNING_RECORDS
                ):
                    raise SerializationError(
                        f"{field}.order requires an integer from 0 to {MAX_PLANNING_RECORDS}"
                    )
            planned_operations.append(
                GitGraphOperationPlan(
                    source_record=operation,
                    operation_index=index,
                    operation_type="branch",
                    checkout_emitted_id=(
                        branch_ids[source_branch] if current_branch != source_branch else None
                    ),
                    owning_branch_source_id=source_id,
                    owning_branch_emitted_id=output_id,
                    source_branch_source_id=source_branch,
                    source_branch_emitted_id=branch_ids[source_branch],
                    element_id=None,
                    semantic_id=None,
                    parent_element_ids=(),
                    tag=None,
                    commit_type=None,
                    order=order,
                )
            )
            branch_ids[source_id] = output_id
            normalized_branches.add(output_id)
            branch_heads[source_id] = branch_heads[source_branch]
            branch_state[source_id] = {
                "source_record": operation,
                "emitted_id": output_id,
                "parent_source_id": source_branch,
                "order": order,
                "member_element_ids": [],
            }
            current_branch = source_id
        elif operation_type == "merge":
            source_branch = operation.get("source")
            target_branch = operation.get("target")
            if isinstance(source_branch, str):
                source_branch = _gitgraph_semantic_text(source_branch, field=f"{field}.source")
            if not isinstance(source_branch, str) or source_branch not in branch_ids:
                raise SerializationError(
                    f"{field} references unknown source branch {source_branch!r}"
                )
            if isinstance(target_branch, str):
                target_branch = _gitgraph_semantic_text(target_branch, field=f"{field}.target")
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
            commit_id = _gitgraph_semantic_text(operation.get("id"), field=f"{field}.id")
            if commit_id in commit_ids:
                raise SerializationError(f"duplicate gitgraph commit ID {commit_id!r}")
            encoded_commit_id, _ = _gitgraph_text(commit_id, field=f"{field}.id")
            if encoded_commit_id in encoded_commit_ids:
                raise SerializationError(
                    "gitgraph commit IDs collide after Mermaid grammar encoding"
                )
            tag, commit_type = _gitgraph_commit_metadata(operation, field=field)
            element_id = f"git_commit_{index}"
            planned = GitGraphOperationPlan(
                source_record=operation,
                operation_index=index,
                operation_type="merge",
                checkout_emitted_id=(
                    branch_ids[target_branch] if current_branch != target_branch else None
                ),
                owning_branch_source_id=target_branch,
                owning_branch_emitted_id=branch_ids[target_branch],
                source_branch_source_id=source_branch,
                source_branch_emitted_id=branch_ids[source_branch],
                element_id=element_id,
                semantic_id=commit_id,
                parent_element_ids=(
                    branch_heads[target_branch],
                    branch_heads[source_branch],
                ),
                tag=tag,
                commit_type=commit_type,
                order=None,
            )
            planned_operations.append(planned)
            planned_commits.append(planned)
            commit_ids.add(commit_id)
            encoded_commit_ids.add(encoded_commit_id)
            branch_heads[target_branch] = element_id
            branch_state[target_branch]["member_element_ids"].append(element_id)
            current_branch = target_branch
        else:
            raise SerializationError(f"{field} has unsupported type {operation_type!r}")

    branches = tuple(
        GitGraphBranchPlan(
            source_record=state["source_record"],
            source_id=source_id,
            emitted_id=state["emitted_id"],
            parent_source_id=state["parent_source_id"],
            order=state["order"],
            member_element_ids=tuple(state["member_element_ids"]),
        )
        for source_id, state in branch_state.items()
    )
    return GitGraphPlan(
        initial_branch_source_id=initial_source,
        initial_branch_emitted_id=initial_output,
        direction=direction,
        operations=tuple(planned_operations),
        commits=tuple(planned_commits),
        branches=branches,
    )


def _gitgraph_native_suffix(operation: GitGraphOperationPlan) -> tuple[str, bool]:
    parts: list[str] = []
    substituted_angles = False
    if operation.tag is not None:
        tag, tag_substituted = _gitgraph_text(
            operation.tag,
            field=f"gitgraph operation {operation.operation_index}.tag",
        )
        parts.append(f'tag: "{tag}"')
        substituted_angles = substituted_angles or tag_substituted
    if operation.commit_type is not None:
        parts.append(f"type: {operation.commit_type}")
    return (f" {' '.join(parts)}" if parts else ""), substituted_angles


def serialize_gitgraph(
    ir: Mapping[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> SerializationResult:
    """Serialize shared Git topology to native GitGraph or portable Flowchart."""

    if not isinstance(native_runtime_valid, bool):
        raise SerializationError("native_runtime_valid must be a boolean")
    plan = plan_gitgraph_records(ir)
    accessibility_lines, accessibility_substituted = _gitgraph_accessibility(
        ir, experimental=experimental
    )

    if not native_runtime_valid:
        nodes = []
        edges = []
        for commit in plan.commits:
            assert commit.element_id is not None and commit.semantic_id is not None
            label = commit.semantic_id
            if commit.tag is not None:
                label += f" [tag: {commit.tag}]"
            nodes.append({"id": commit.element_id, "label": label})
            edges.extend(
                {"source": parent_id, "target": commit.element_id}
                for parent_id in commit.parent_element_ids
            )
        groups = [
            {
                "id": f"git_branch_{index}",
                "label": branch.source_id,
                "member_ids": list(branch.member_element_ids),
            }
            for index, branch in enumerate(plan.branches, start=1)
            if branch.member_element_ids
        ]
        code, substituted_glyphs = _flowchart_fallback_code(
            ir,
            "gitgraph",
            nodes=nodes,
            edges=edges,
            groups=groups,
            direction=plan.direction or "LR",
            experimental=experimental,
        )
        warnings = [
            "CandidateValidator rejected native GitGraph; commit IDs, tags, branch "
            "membership, and parent topology were re-emitted as portable Flowchart.",
            "GitGraph branch lanes, exact operation ordering, and commit-type symbols "
            "are not preserved by the Flowchart fallback.",
        ]
        if substituted_glyphs:
            warnings.append(_FLOWCHART_GLYPH_WARNING)
        return SerializationResult.fallback(
            "gitgraph",
            "flowchart",
            code,
            warnings=tuple(warnings),
            stability="experimental",
        )

    header = f"gitGraph {plan.direction}:" if plan.direction else "gitGraph"
    lines = [header, *accessibility_lines]
    substituted_angles = accessibility_substituted
    for operation in plan.operations:
        if operation.checkout_emitted_id is not None:
            lines.append(f"    checkout {operation.checkout_emitted_id}")
        if operation.operation_type == "branch":
            order_suffix = f" order: {operation.order}" if operation.order is not None else ""
            lines.append(f"    branch {operation.owning_branch_emitted_id}{order_suffix}")
            continue
        assert operation.semantic_id is not None
        commit_id, id_substituted = _gitgraph_text(
            operation.semantic_id,
            field=f"gitgraph operation {operation.operation_index}.id",
        )
        suffix, suffix_substituted = _gitgraph_native_suffix(operation)
        substituted_angles = substituted_angles or id_substituted or suffix_substituted
        if operation.operation_type == "commit":
            lines.append(f'    commit id: "{commit_id}"{suffix}')
        else:
            assert operation.source_branch_emitted_id is not None
            lines.append(
                f'    merge {operation.source_branch_emitted_id} id: "{commit_id}"{suffix}'
            )

    warnings = (_GITGRAPH_ANGLE_WARNING,) if substituted_angles else ()
    code = _preflight_planning_code(
        "\n".join(lines) + "\n",
        diagram_type="gitgraph",
    )
    return _native_result("gitgraph", code, warnings=warnings)


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
