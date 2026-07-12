"""Compatibility- and security-aware Mermaid flowchart style recovery."""

from __future__ import annotations

import re
from dataclasses import dataclass

from marker_mermaid.config import CompatibilityProfile, SecurityProfile
from marker_mermaid.models import DiagramSceneIR

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


@dataclass(frozen=True, slots=True)
class StyleRecoveryResult:
    code: str
    applied_element_ids: tuple[str, ...] = ()
    applied_link_indexes: tuple[int, ...] = ()
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


def _has_style_evidence(scene: DiagramSceneIR) -> bool:
    return any(
        element.fill_color or element.border_color or element.border_style
        for element in scene.elements
    ) or any(relation.line_style in {"dashed", "thick"} for relation in scene.relations)


def recover_flowchart_styles(
    code: str,
    scene: DiagramSceneIR | None,
    *,
    compatibility_profile: CompatibilityProfile,
    security_profile: SecurityProfile,
) -> StyleRecoveryResult:
    """Append an allowlisted style subset when both product profiles permit it.

    No style is emitted under strict security or portable-basic compatibility.
    Unsupported colors remain in Scene IR and are disclosed through warnings.
    """

    if scene is None or not _has_style_evidence(scene):
        return StyleRecoveryResult(code=code)
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

    declarations = set(_NODE_DECLARATION.findall(code))
    normalized_sources: dict[str, list[str]] = {}
    for element in scene.elements:
        normalized_sources.setdefault(_identifier(element.id), []).append(element.id)
    lines: list[str] = []
    applied_elements: list[str] = []
    warnings: list[str] = []
    for element in scene.elements:
        node_id = _identifier(element.id)
        if node_id not in declarations:
            continue
        if len(normalized_sources[node_id]) != 1:
            warnings.append(f"style skipped for ambiguous normalized node id {node_id}")
            continue
        attributes: list[str] = []
        fill = _color(element.fill_color)
        border = _color(element.border_color)
        if element.fill_color and fill is None:
            warnings.append(f"unsupported fill color for {element.id}: {element.fill_color}")
        if element.border_color and border is None:
            warnings.append(f"unsupported border color for {element.id}: {element.border_color}")
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
        if attributes:
            lines.append(f"    style {node_id} {','.join(attributes)}")
            applied_elements.append(element.id)

    edge_pairs = list(_EDGE.findall(code))
    used_edge_indexes: set[int] = set()
    applied_links: list[int] = []
    for relation in scene.relations:
        if relation.source_id is None or relation.target_id is None:
            continue
        style = relation.line_style
        if style not in {"dashed", "thick"}:
            continue
        pair = (_identifier(relation.source_id), _identifier(relation.target_id))
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
        declaration = "stroke-dasharray:5 5" if style == "dashed" else "stroke-width:3px"
        lines.append(f"    linkStyle {edge_index} {declaration}")
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
        warnings=tuple(dict.fromkeys(warnings)),
    )
