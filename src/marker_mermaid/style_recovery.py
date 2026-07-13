"""Compatibility- and security-aware Mermaid flowchart style recovery."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from marker_mermaid.config import CompatibilityProfile, SecurityProfile
from marker_mermaid.flowchart_structure import ambiguous_portable_ids
from marker_mermaid.models import DiagramSceneIR, SceneElement, VisualEvidence

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\Z")
_NAMED_COLORS = {
    "black",
    "white",
    "red",
    "green",
    "blue",
    "yellow",
    "orange",
    "purple",
    "gray",
    "grey",
    "cyan",
    "magenta",
    "transparent",
}
_NODE_DECLARATION = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[\[({]", re.MULTILINE)
_EDGE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(?:<-->|-->|-.->|==>|---|--?>\|[^|\n]*\|)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*$",
    re.MULTILINE,
)
_EDGE_OPERATOR = re.compile(r"<-->|-->|-\.->|==>|---|--?>\|")
_SUBGRAPH_DECLARATION = re.compile(r"^\s*subgraph\s+([A-Za-z_][A-Za-z0-9_]*)\s*\[", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class StyleAttribution:
    source_element_id: str
    emitted_element_id: str
    evidence_ids: tuple[str, ...]
    match_method: str


@dataclass(frozen=True, slots=True)
class GroupStyleAttribution:
    source_group_id: str
    emitted_group_id: str
    evidence_ids: tuple[str, ...]
    match_method: str


@dataclass(frozen=True, slots=True)
class StyleRecoveryResult:
    code: str
    applied_element_ids: tuple[str, ...] = ()
    applied_link_indexes: tuple[int, ...] = ()
    applied_group_ids: tuple[str, ...] = ()
    attributions: tuple[StyleAttribution, ...] = ()
    group_attributions: tuple[GroupStyleAttribution, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.applied_element_ids or self.applied_link_indexes or self.applied_group_ids)


def _identifier(value: str, fallback: str = "node") -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = f"n_{normalized}"
    return normalized


def _color(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if _HEX_COLOR.fullmatch(normalized) and len(normalized) in {4, 5, 7, 9}:
        return normalized
    if normalized in _NAMED_COLORS:
        return normalized
    return None


def _warning_value(value: str, limit: int = 160) -> str:
    bounded = value if len(value) <= limit else value[: limit - 3] + "..."
    return repr(bounded)


def _has_style_evidence(scene: DiagramSceneIR) -> bool:
    return any(_element_has_style(element) for element in scene.elements) or any(
        relation.line_color or relation.line_style in {"dashed", "thick"}
        for relation in scene.relations
    )


def _element_has_style(element: SceneElement) -> bool:
    return bool(
        element.fill_color
        or element.border_color
        or element.border_style
        or element.font_weight == "bold"
    )


def _normalized_label(value: str | None) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _map_elements(
    source_scene: DiagramSceneIR,
    generated_scene: DiagramSceneIR,
    known_evidence_ids: set[str],
) -> tuple[
    list[tuple[SceneElement, SceneElement, str, tuple[str, ...]]],
    list[str],
]:
    ambiguous_source_ids, ambiguous_emitted_ids = ambiguous_portable_ids(
        [element.id for element in source_scene.elements]
    )
    generated_by_id = {element.id: element for element in generated_scene.elements}
    generated_by_label: dict[str, list[SceneElement]] = {}
    generated_by_evidence: dict[str, list[SceneElement]] = {}
    for element in generated_scene.elements:
        label = _normalized_label(element.text)
        if label:
            generated_by_label.setdefault(label, []).append(element)
        for evidence_id in set(element.evidence_ids) & known_evidence_ids:
            generated_by_evidence.setdefault(evidence_id, []).append(element)

    proposals: list[tuple[SceneElement, SceneElement, str, tuple[str, ...]]] = []
    warnings: list[str] = []
    for source in source_scene.elements:
        source_evidence = tuple(sorted(set(source.evidence_ids) & known_evidence_ids))
        source_evidence_set = set(source_evidence)
        exact = generated_by_id.get(source.id)
        if (
            exact is not None
            and source.id not in ambiguous_source_ids
            and exact.id not in ambiguous_emitted_ids
        ):
            exact_overlap = tuple(sorted(set(exact.evidence_ids) & source_evidence_set))
            if _normalized_label(source.text) == _normalized_label(exact.text) or exact_overlap:
                proposals.append((source, exact, "exact_id", exact_overlap or source_evidence))
                continue
            warnings.append(f"style mapping exact ID content mismatch for {source.id}")
        elif exact is not None:
            warnings.append(
                f"style mapping exact ID has ambiguous normalized identity for {source.id}"
            )
        evidence_match_by_id: dict[str, SceneElement] = {}
        for evidence_id in source_evidence:
            for candidate in generated_by_evidence.get(evidence_id, ()):  # bounded index lookup
                evidence_match_by_id[candidate.id] = candidate
        evidence_matches = list(evidence_match_by_id.values())
        if len(evidence_matches) == 1:
            overlap = tuple(sorted(set(evidence_matches[0].evidence_ids) & set(source_evidence)))
            proposals.append((source, evidence_matches[0], "evidence_overlap", overlap))
            continue
        if len(evidence_matches) > 1:
            warnings.append(f"style mapping was ambiguous by evidence for {source.id}")
            continue
        label = _normalized_label(source.text)
        label_matches = generated_by_label.get(label, []) if label and source_evidence else []
        if len(label_matches) == 1:
            proposals.append((source, label_matches[0], "unique_label", source_evidence))
        elif len(label_matches) > 1:
            warnings.append(f"style mapping was ambiguous by label for {source.id}")
        elif _element_has_style(source):
            warnings.append(f"style mapping was unavailable for {source.id}")

    target_counts: dict[str, int] = {}
    for _source, target, _method, _evidence in proposals:
        target_counts[target.id] = target_counts.get(target.id, 0) + 1
    safe = []
    for proposal in proposals:
        source, target, _method, _evidence = proposal
        if target_counts[target.id] != 1:
            warnings.append(f"style mapping collision for emitted node {target.id}")
            continue
        safe.append(proposal)
    return safe, list(dict.fromkeys(warnings))


def _bold_evidence_supports(
    source: SceneElement,
    emitted: SceneElement,
    evidence_ids: tuple[str, ...],
    registry: Mapping[str, VisualEvidence],
) -> bool:
    source_label = _normalized_label(source.text)
    if not source_label or source_label != _normalized_label(emitted.text):
        return False
    spans: list[tuple[tuple[float, float, float, float], VisualEvidence]] = []
    for evidence_id in evidence_ids:
        evidence = registry.get(evidence_id)
        if (
            evidence is None
            or evidence.kind != "vector_text"
            or evidence.font_weight != "bold"
            or evidence.bbox is None
            or not evidence.text
        ):
            continue
        center = (
            (evidence.bbox[0] + evidence.bbox[2]) / 2,
            (evidence.bbox[1] + evidence.bbox[3]) / 2,
        )
        if not (
            source.bbox[0] <= center[0] <= source.bbox[2]
            and source.bbox[1] <= center[1] <= source.bbox[3]
        ):
            return False
        spans.append((evidence.bbox, evidence))
    spans.sort(key=lambda item: (item[0][1], item[0][0], item[1].id))
    reconstructed = _normalized_label(" ".join(item.text or "" for _bbox, item in spans))
    return bool(spans) and reconstructed == source_label


def recover_flowchart_styles(
    code: str,
    scene: DiagramSceneIR | None,
    generated_scene: DiagramSceneIR | None = None,
    *,
    compatibility_profile: CompatibilityProfile,
    security_profile: SecurityProfile,
    known_evidence_ids: set[str] | frozenset[str] = frozenset(),
    known_bold_evidence: Mapping[str, VisualEvidence] | None = None,
    known_group_style_evidence: Mapping[str, SceneElement] | None = None,
) -> StyleRecoveryResult:
    """Append an allowlisted style subset when both product profiles permit it.

    No style is emitted under strict security or portable-basic compatibility.
    Unsupported colors remain in Scene IR and are disclosed through warnings.
    """

    if scene is None or not (
        _has_style_evidence(scene) or (scene.groups and known_group_style_evidence)
    ):
        return StyleRecoveryResult(code=code)
    if generated_scene is None:
        return StyleRecoveryResult(
            code=code,
            warnings=("style evidence was retained because candidate Scene is unavailable",),
        )
    first_line = next((line.strip() for line in code.splitlines() if line.strip()), "")
    if not first_line.casefold().startswith(("flowchart ", "graph ")):
        return StyleRecoveryResult(
            code=code,
            warnings=("style evidence was retained because the emitted grammar is not flowchart",),
        )
    compatible = compatibility_profile in {
        CompatibilityProfile.PORTABLE_RICH,
        CompatibilityProfile.STYLE_RICH,
        CompatibilityProfile.TRUSTED_LOCAL,
    }
    if security_profile == SecurityProfile.STRICT or not compatible:
        return StyleRecoveryResult(
            code=code,
            warnings=(
                "style evidence remains in Scene IR because compatibility/security profiles "
                "do not permit Mermaid style statements",
            ),
        )

    mappings, mapping_warnings = _map_elements(
        scene,
        generated_scene,
        set(known_evidence_ids),
    )
    ambiguous_scene_source_ids, _ambiguous_scene_emitted_ids = ambiguous_portable_ids(
        [element.id for element in scene.elements]
    )
    declarations = set(_NODE_DECLARATION.findall(code))
    normalized_sources: dict[str, list[str]] = {}
    for element in generated_scene.elements:
        normalized_sources.setdefault(_identifier(element.id), []).append(element.id)
    lines: list[str] = []
    applied_elements: list[str] = []
    applied_groups: list[str] = []
    attributions: list[StyleAttribution] = []
    group_attributions: list[GroupStyleAttribution] = []
    warnings: list[str] = list(mapping_warnings)
    for element, emitted, method, evidence_ids in mappings:
        node_id = _identifier(emitted.id)
        if node_id not in declarations:
            continue
        if len(normalized_sources[node_id]) != 1:
            warnings.append(f"style skipped for ambiguous normalized node id {node_id}")
            continue
        attributes: list[str] = []
        fill = _color(element.fill_color)
        border = _color(element.border_color)
        if element.fill_color and fill is None:
            warnings.append(
                f"unsupported fill color for {element.id}: {_warning_value(element.fill_color)}"
            )
        if element.border_color and border is None:
            warnings.append(
                f"unsupported border color for {element.id}: {_warning_value(element.border_color)}"
            )
        if fill is not None:
            attributes.append(f"fill:{fill}")
        if border is not None:
            attributes.append(f"stroke:{border}")
        if element.border_style == "dashed":
            attributes.append("stroke-dasharray:5 5")
        elif element.border_style == "thick":
            attributes.append("stroke-width:3px")
        elif element.border_style not in {None, "solid"}:
            warnings.append(f"unsupported border style for {element.id}: {element.border_style}")
        bold_supported = _bold_evidence_supports(
            element,
            emitted,
            evidence_ids,
            known_bold_evidence or {},
        )
        if element.font_weight == "bold" and bold_supported:
            attributes.append("font-weight:bold")
        elif element.font_weight == "bold":
            warnings.append(
                f"bold emphasis omitted without registered vector evidence for {element.id}"
            )
        if attributes:
            lines.append(f"    style {node_id} {','.join(attributes)}")
            applied_elements.append(element.id)
            attributions.append(
                StyleAttribution(
                    source_element_id=element.id,
                    emitted_element_id=emitted.id,
                    evidence_ids=evidence_ids,
                    match_method=method,
                )
            )

    mapped_ids = {source.id: emitted.id for source, emitted, _method, _ids in mappings}
    used_group_evidence: set[str] = set()
    trusted_group_styles = dict(known_group_style_evidence or {})
    total_group_members = sum(len(group.member_ids) for group in scene.groups)
    group_style_work = len(trusted_group_styles) * (
        len(scene.groups) + (len(scene.elements) + 1) * total_group_members
    )
    if group_style_work > 2_000_000:
        warnings.append("group style matching exceeded the deterministic work budget")
        trusted_group_styles = {}
    source_group_member_counts: dict[tuple[str, ...], int] = {}
    if scene.groups and trusted_group_styles and scene.coordinate_space != "pixels":
        warnings.append("group styles require source groups in pixel coordinates")
    elif scene.groups and trusted_group_styles:
        emitted_groups_by_members: dict[tuple[str, ...], list] = {}
        for group in generated_scene.groups:
            emitted_groups_by_members.setdefault(tuple(sorted(group.member_ids)), []).append(group)
        group_declaration_counts: dict[str, int] = {}
        for group_id in _SUBGRAPH_DECLARATION.findall(code):
            group_declaration_counts[group_id] = group_declaration_counts.get(group_id, 0) + 1
        source_elements_by_id = {element.id: element for element in scene.elements}
        for group in scene.groups:
            if all(member_id in mapped_ids for member_id in group.member_ids):
                key = tuple(sorted(mapped_ids[member_id] for member_id in group.member_ids))
                source_group_member_counts[key] = source_group_member_counts.get(key, 0) + 1
        for source_group in scene.groups:
            if any(member_id not in mapped_ids for member_id in source_group.member_ids):
                warnings.append(f"group style could not map every member for {source_group.id}")
                continue
            emitted_members = tuple(
                sorted(mapped_ids[member_id] for member_id in source_group.member_ids)
            )
            if source_group_member_counts.get(emitted_members) != 1:
                warnings.append(f"source group membership was ambiguous for {source_group.id}")
                continue
            emitted_group_matches = emitted_groups_by_members.get(emitted_members, [])
            if len(emitted_group_matches) != 1:
                warnings.append(f"group style target was ambiguous for {source_group.id}")
                continue
            emitted_group = emitted_group_matches[0]
            if emitted_group.id in declarations:
                warnings.append(f"group style id collided with a node for {source_group.id}")
                continue
            if group_declaration_counts.get(emitted_group.id) != 1:
                warnings.append(f"group style declaration was unavailable for {source_group.id}")
                continue
            trusted_matches: list[tuple[str, SceneElement]] = []
            group_box = source_group.bbox
            group_area = max(0.0, group_box[2] - group_box[0]) * max(
                0.0, group_box[3] - group_box[1]
            )
            if group_area == 0:
                warnings.append(f"group style bbox was empty for {source_group.id}")
                continue
            member_centers = [
                (
                    (
                        source_elements_by_id[member_id].bbox[0]
                        + source_elements_by_id[member_id].bbox[2]
                    )
                    / 2,
                    (
                        source_elements_by_id[member_id].bbox[1]
                        + source_elements_by_id[member_id].bbox[3]
                    )
                    / 2,
                )
                for member_id in source_group.member_ids
            ]
            member_boxes = [
                source_elements_by_id[member_id].bbox for member_id in source_group.member_ids
            ]
            member_evidence_ids = {
                evidence_id
                for member_id in source_group.member_ids
                for evidence_id in source_elements_by_id[member_id].evidence_ids
            }
            for evidence_id, vector_element in trusted_group_styles.items():
                if evidence_id in used_group_evidence:
                    continue
                if evidence_id not in vector_element.evidence_ids:
                    continue
                if evidence_id in member_evidence_ids:
                    continue
                vector_box = vector_element.bbox
                matches_member_geometry = False
                for member_box in member_boxes:
                    member_intersection = max(
                        0.0,
                        min(vector_box[2], member_box[2]) - max(vector_box[0], member_box[0]),
                    ) * max(
                        0.0,
                        min(vector_box[3], member_box[3]) - max(vector_box[1], member_box[1]),
                    )
                    member_area = max(0.0, member_box[2] - member_box[0]) * max(
                        0.0, member_box[3] - member_box[1]
                    )
                    vector_area = max(0.0, vector_box[2] - vector_box[0]) * max(
                        0.0, vector_box[3] - vector_box[1]
                    )
                    member_union = member_area + vector_area - member_intersection
                    if member_union > 0 and member_intersection / member_union >= 0.8:
                        matches_member_geometry = True
                        break
                if matches_member_geometry:
                    continue
                intersection = max(
                    0.0, min(group_box[2], vector_box[2]) - max(group_box[0], vector_box[0])
                ) * max(
                    0.0,
                    min(group_box[3], vector_box[3]) - max(group_box[1], vector_box[1]),
                )
                vector_area = max(0.0, vector_box[2] - vector_box[0]) * max(
                    0.0, vector_box[3] - vector_box[1]
                )
                union = group_area + vector_area - intersection
                if union <= 0 or intersection / union < 0.8:
                    continue
                if not all(
                    vector_box[0] <= center[0] <= vector_box[2]
                    and vector_box[1] <= center[1] <= vector_box[3]
                    for center in member_centers
                ):
                    continue
                outside_inside = False
                for element in scene.elements:
                    if element.id in source_group.member_ids:
                        continue
                    if evidence_id in element.evidence_ids:
                        continue
                    duplicate_member_geometry = False
                    for member_box in member_boxes:
                        overlap = max(
                            0.0,
                            min(element.bbox[2], member_box[2])
                            - max(element.bbox[0], member_box[0]),
                        ) * max(
                            0.0,
                            min(element.bbox[3], member_box[3])
                            - max(element.bbox[1], member_box[1]),
                        )
                        element_area = max(0.0, element.bbox[2] - element.bbox[0]) * max(
                            0.0, element.bbox[3] - element.bbox[1]
                        )
                        member_area = max(0.0, member_box[2] - member_box[0]) * max(
                            0.0, member_box[3] - member_box[1]
                        )
                        overlap_union = element_area + member_area - overlap
                        if overlap_union > 0 and overlap / overlap_union >= 0.8:
                            duplicate_member_geometry = True
                            break
                    if duplicate_member_geometry:
                        continue
                    center = (
                        (element.bbox[0] + element.bbox[2]) / 2,
                        (element.bbox[1] + element.bbox[3]) / 2,
                    )
                    if (
                        vector_box[0] <= center[0] <= vector_box[2]
                        and vector_box[1] <= center[1] <= vector_box[3]
                    ):
                        outside_inside = True
                        break
                if not outside_inside:
                    trusted_matches.append((evidence_id, vector_element))
                    if len(trusted_matches) > 1:
                        break
            if len(trusted_matches) != 1:
                if trusted_matches:
                    warnings.append(
                        f"group style vector evidence was ambiguous for {source_group.id}"
                    )
                continue
            evidence_id, vector_element = trusted_matches[0]
            attributes: list[str] = []
            fill = _color(vector_element.fill_color)
            border = _color(vector_element.border_color)
            if fill is not None:
                attributes.append(f"fill:{fill}")
            if border is not None:
                attributes.append(f"stroke:{border}")
            if vector_element.border_style == "dashed":
                attributes.append("stroke-dasharray:5 5")
            elif vector_element.border_style == "thick":
                attributes.append("stroke-width:3px")
            elif vector_element.border_style not in {None, "solid"}:
                warnings.append(
                    f"unsupported group border style for {source_group.id}: "
                    f"{vector_element.border_style}"
                )
            if vector_element.fill_color and fill is None:
                warnings.append(
                    f"unsupported group fill color for {source_group.id}: "
                    f"{_warning_value(vector_element.fill_color)}"
                )
            if vector_element.border_color and border is None:
                warnings.append(
                    f"unsupported group border color for {source_group.id}: "
                    f"{_warning_value(vector_element.border_color)}"
                )
            if not attributes:
                continue
            lines.append(f"    style {emitted_group.id} {','.join(attributes)}")
            applied_groups.append(source_group.id)
            used_group_evidence.add(evidence_id)
            group_attributions.append(
                GroupStyleAttribution(
                    source_group_id=source_group.id,
                    emitted_group_id=emitted_group.id,
                    evidence_ids=(evidence_id,),
                    match_method="exact_members_and_vector_bbox",
                )
            )

    edge_lines = [line for line in code.splitlines() if _EDGE_OPERATOR.search(line)]
    edge_matches = [_EDGE.fullmatch(line) for line in edge_lines]
    edge_mapping_safe = bool(edge_lines) and all(match is not None for match in edge_matches)
    edge_pairs = [match.groups() for match in edge_matches if match is not None]
    used_edge_indexes: set[int] = set()
    applied_links: list[int] = []
    if not edge_mapping_safe and any(
        relation.line_color or relation.line_style in {"dashed", "thick"}
        for relation in scene.relations
    ):
        warnings.append(
            "edge styles were skipped because Mermaid edge ordering could not be mapped safely"
        )
    for relation in scene.relations:
        if relation.source_id is None or relation.target_id is None:
            continue
        color = _color(relation.line_color)
        style = relation.line_style
        if relation.line_color and color is None:
            warnings.append(
                f"unsupported line color for {relation.id}: {_warning_value(relation.line_color)}"
            )
        if color is None and style not in {"dashed", "thick"}:
            continue
        if not edge_mapping_safe:
            continue
        mapped_source = mapped_ids.get(relation.source_id)
        mapped_target = mapped_ids.get(relation.target_id)
        if mapped_source is None or mapped_target is None:
            if (
                relation.source_id in ambiguous_scene_source_ids
                or relation.target_id in ambiguous_scene_source_ids
            ):
                warnings.append(
                    f"edge style skipped for ambiguous normalized endpoint in {relation.id}"
                )
            else:
                warnings.append(f"edge style could not map source nodes for {relation.id}")
            continue
        normalized_source = _identifier(mapped_source)
        normalized_target = _identifier(mapped_target)
        if (
            len(normalized_sources.get(normalized_source, ())) != 1
            or len(normalized_sources.get(normalized_target, ())) != 1
        ):
            warnings.append(
                f"edge style skipped for ambiguous normalized endpoint in {relation.id}"
            )
            continue
        pair = (normalized_source, normalized_target)
        edge_index = next(
            (
                index
                for index, candidate in enumerate(edge_pairs)
                if index not in used_edge_indexes and candidate == pair
            ),
            None,
        )
        if edge_index is None:
            warnings.append(f"edge style could not be mapped for {relation.id}")
            continue
        used_edge_indexes.add(edge_index)
        attributes: list[str] = []
        if color is not None:
            attributes.append(f"stroke:{color}")
        if style == "dashed":
            attributes.append("stroke-dasharray:5 5")
        elif style == "thick":
            attributes.append("stroke-width:3px")
        lines.append(f"    linkStyle {edge_index} {','.join(attributes)}")
        applied_links.append(edge_index)

    if not lines:
        return StyleRecoveryResult(code=code, warnings=tuple(dict.fromkeys(warnings)))
    styled = code.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    warnings.append(
        "Mermaid style statements may render differently in external Markdown consumers"
    )
    return StyleRecoveryResult(
        code=styled,
        applied_element_ids=tuple(applied_elements),
        applied_link_indexes=tuple(applied_links),
        applied_group_ids=tuple(applied_groups),
        attributions=tuple(attributions),
        group_attributions=tuple(group_attributions),
        warnings=tuple(dict.fromkeys(warnings)),
    )
