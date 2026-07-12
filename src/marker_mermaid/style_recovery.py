"""Compatibility- and security-aware Mermaid flowchart style recovery."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from marker_mermaid.config import CompatibilityProfile, SecurityProfile
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


@dataclass(frozen=True, slots=True)
class StyleAttribution:
    source_element_id: str
    emitted_element_id: str
    evidence_ids: tuple[str, ...]
    match_method: str


@dataclass(frozen=True, slots=True)
class StyleRecoveryResult:
    code: str
    applied_element_ids: tuple[str, ...] = ()
    applied_link_indexes: tuple[int, ...] = ()
    attributions: tuple[StyleAttribution, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.applied_element_ids or self.applied_link_indexes)


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
        if exact is not None:
            exact_overlap = tuple(
                sorted(set(exact.evidence_ids) & source_evidence_set)
            )
            if _normalized_label(source.text) == _normalized_label(exact.text) or exact_overlap:
                proposals.append(
                    (source, exact, "exact_id", exact_overlap or source_evidence)
                )
                continue
            warnings.append(f"style mapping exact ID content mismatch for {source.id}")
        evidence_match_by_id: dict[str, SceneElement] = {}
        for evidence_id in source_evidence:
            for candidate in generated_by_evidence.get(evidence_id, ()):  # bounded index lookup
                evidence_match_by_id[candidate.id] = candidate
        evidence_matches = list(evidence_match_by_id.values())
        if len(evidence_matches) == 1:
            overlap = tuple(
                sorted(set(evidence_matches[0].evidence_ids) & set(source_evidence))
            )
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
) -> StyleRecoveryResult:
    """Append an allowlisted style subset when both product profiles permit it.

    No style is emitted under strict security or portable-basic compatibility.
    Unsupported colors remain in Scene IR and are disclosed through warnings.
    """

    if scene is None or not _has_style_evidence(scene):
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
    declarations = set(_NODE_DECLARATION.findall(code))
    normalized_sources: dict[str, list[str]] = {}
    for element in generated_scene.elements:
        normalized_sources.setdefault(_identifier(element.id), []).append(element.id)
    lines: list[str] = []
    applied_elements: list[str] = []
    attributions: list[StyleAttribution] = []
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

    edge_lines = [line for line in code.splitlines() if _EDGE_OPERATOR.search(line)]
    edge_matches = [_EDGE.fullmatch(line) for line in edge_lines]
    edge_mapping_safe = bool(edge_lines) and all(match is not None for match in edge_matches)
    edge_pairs = [match.groups() for match in edge_matches if match is not None]
    used_edge_indexes: set[int] = set()
    applied_links: list[int] = []
    mapped_ids = {source.id: emitted.id for source, emitted, _method, _ids in mappings}
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
        attributions=tuple(attributions),
        warnings=tuple(dict.fromkeys(warnings)),
    )
