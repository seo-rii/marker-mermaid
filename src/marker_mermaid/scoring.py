"""Reference-free scores, aggregation, and publish decisions."""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from marker_mermaid.accessibility import supports_accessibility_directives
from marker_mermaid.config import MermaidConfig, PublishPolicy, quality_grade
from marker_mermaid.models import MermaidCandidate

_NUMERIC_TOKEN_PATTERN = re.compile(r"(?<!\w)[+-]?(?:\d+(?:\.\d+)?|\.\d+)%?")
_SCORABLE_GRAMMAR_BY_HEADER = {
    # Numeric serializers can emit these portable fallbacks after runtime rejection.
    "flowchart": "flowchart",
    "graph": "flowchart",
    "gantt": "gantt",
    "packet-beta": "packet",
    "pie": "pie",
    "quadrantchart": "quadrant",
    "radar-beta": "radar",
    "sankey-beta": "sankey",
    "treemap-beta": "treemap",
    "venn-beta": "venn",
    "xychart-beta": "xychart",
}
_NATIVE_TITLE_TYPES = frozenset(
    {"gantt", "packet", "pie", "quadrant", "radar", "treemap", "venn", "xychart"}
)
_MAX_METADATA_SUFFIXES_PER_LINE = 32


def _tokens(text: str) -> Iterator[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return (
        match.group(0)
        for match in re.finditer(r"[\w가-힣ぁ-んァ-ン一-龥]+", normalized)
        if match.group(0)
    )


def ocr_token_multiset(texts: Iterable[str]) -> Counter[str]:
    """Return a Unicode-normalized token multiset without collapsing occurrences."""

    return Counter(token for text in texts for token in _tokens(text))


def numeric_token_multiset(texts: Iterable[str]) -> Counter[str]:
    """Return exact numeric tokens for source-channel occurrence accounting."""

    return Counter(_NUMERIC_TOKEN_PATTERN.findall(" ".join(texts)))


def bounded_ocr_token_multiset(
    texts: Iterable[str],
    *,
    max_texts: int,
    max_chars: int,
    max_tokens: int,
) -> Counter[str] | None:
    """Tokenize within a fixed work budget, returning ``None`` instead of truncating."""

    result: Counter[str] = Counter()
    char_count = 0
    token_count = 0
    for text_count, text in enumerate(texts, start=1):
        char_count += len(text)
        if text_count > max_texts or char_count > max_chars:
            return None
        for token in _tokens(text):
            token_count += 1
            if token_count > max_tokens:
                return None
            result[token] += 1
    return result


def _direct_mermaid_label_texts(code: str) -> list[str]:
    """Extract a conservative label subset from raw Mermaid without counting IDs/metadata."""

    labels: list[str] = []
    quoted = re.compile(r'"(?P<label>(?:[^"\\]|\\.)*)"')
    metadata = re.compile(
        r"^\s*(?:accTitle|accDescr|title)\s*:|^\s*(?:flowchart|graph|sequenceDiagram|"
        r"stateDiagram(?:-v2)?|classDiagram|erDiagram|mindmap|timeline|gantt|pie|"
        r"xychart-beta|quadrantChart|sankey-beta)\b",
        re.IGNORECASE,
    )
    first_content = next(
        (
            line.strip().casefold()
            for line in code.splitlines()
            if line.strip() and not line.lstrip().startswith("%%")
        ),
        "",
    )
    is_gantt = first_content == "gantt"
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%%") or metadata.search(stripped):
            continue
        if is_gantt:
            section = re.match(r"section\s+(?P<label>.+)$", stripped, re.IGNORECASE)
            if section:
                labels.append(section["label"].strip())
                continue
            if re.match(
                r"(?:dateFormat|axisFormat|tickInterval|excludes|todayMarker|title)\b",
                stripped,
                re.IGNORECASE,
            ):
                continue
            if ":" in line:
                task_label, _fields = line.split(":", 1)
                if task_label.strip():
                    labels.append(task_label.strip())
                continue
        if re.match(r"(?:style|classDef|linkStyle|class)\b", stripped, re.IGNORECASE):
            continue
        labels.extend(match["label"] for match in quoted.finditer(line))
        if ":" in line and not quoted.search(line):
            _prefix, suffix = line.split(":", 1)
            if suffix.strip():
                labels.append(suffix.strip())
    return labels


def svg_visible_texts(svg: str) -> list[str] | None:
    """Return painted SVG text terminals, excluding accessibility-only metadata."""

    if type(svg) is not str:
        return None
    try:
        root = ET.fromstring(svg)
    except (ET.ParseError, RecursionError):
        return None
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
        return None

    texts: list[str] = []
    stack: list[tuple[ET.Element, bool]] = [(root, False)]
    non_painted_containers = {"defs", "clippath", "mask", "marker", "pattern", "symbol"}
    while stack:
        element, ancestor_hidden = stack.pop()
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        hidden = ancestor_hidden or tag in non_painted_containers
        if not hidden:
            display = (element.get("display") or "").strip().casefold()
            visibility = (element.get("visibility") or "").strip().casefold()
            opacity = (element.get("opacity") or "").strip().casefold()
            hidden = (
                display == "none"
                or visibility in {"hidden", "collapse"}
                or opacity in {"0", "0.0", "0%"}
            )
            if not hidden and (style := element.get("style")):
                for declaration in style.split(";"):
                    name, separator, value = declaration.partition(":")
                    if not separator:
                        continue
                    normalized_name = name.strip().casefold()
                    normalized_value = value.strip().casefold()
                    if normalized_value.endswith("!important"):
                        normalized_value = normalized_value[: -len("!important")].rstrip()
                    if (
                        (normalized_name == "display" and normalized_value == "none")
                        or (
                            normalized_name == "visibility"
                            and normalized_value in {"hidden", "collapse"}
                        )
                        or (
                            normalized_name == "opacity"
                            and normalized_value in {"0", "0.0", "0%"}
                        )
                    ):
                        hidden = True
                        break

        if tag == "text":
            if not hidden:
                text = "".join(element.itertext()).replace("\u200b", "")
                if text.strip():
                    texts.append(text)
            # A text subtree is one painted terminal. Walking nested tspans would
            # count the same token once per ancestor.
            continue
        for child in reversed(element):
            stack.append((child, hidden))
    return texts


def ocr_recall(
    source_texts: Counter[str] | Iterable[str],
    mermaid_code: str,
    *,
    generated_texts: Counter[str] | Iterable[str] | None = None,
) -> float | None:
    source = (
        source_texts.copy()
        if isinstance(source_texts, Counter)
        else ocr_token_multiset(source_texts)
    )
    if not source:
        return None
    if isinstance(generated_texts, Counter):
        generated = generated_texts.copy()
    else:
        generated = ocr_token_multiset(
            generated_texts
            if generated_texts is not None
            else _direct_mermaid_label_texts(mermaid_code)
        )
    return sum((source & generated).values()) / source.total()


def numeric_consistency(
    source_texts: Counter[str] | Iterable[str], mermaid_code: str
) -> float | None:
    source = (
        source_texts.copy()
        if isinstance(source_texts, Counter)
        else numeric_token_multiset(source_texts)
    )
    if not source:
        return None
    first_grammar_line = next(
        (
            uncommented.strip()
            for raw_line in mermaid_code.splitlines()
            if (uncommented := raw_line.split("%%", 1)[0]).strip()
        ),
        "",
    )
    header = first_grammar_line.split(maxsplit=1)[0].casefold() if first_grammar_line else ""
    diagram_type = _SCORABLE_GRAMMAR_BY_HEADER.get(header)
    accessibility_metadata_supported = diagram_type is not None and (
        supports_accessibility_directives(diagram_type)
    )
    native_title_supported = diagram_type in _NATIVE_TITLE_TYPES

    accessibility_colon_pattern = re.compile(r"^\s*(?:accTitle|accDescr)\s*:", re.IGNORECASE)
    title_colon_pattern = re.compile(r"^\s*title\s*:", re.IGNORECASE)
    native_title_pattern = re.compile(r"^\s*title(?:\s+|$)", re.IGNORECASE)
    acc_descr_block_pattern = re.compile(r"^\s*accDescr\s*\{", re.IGNORECASE)
    semantic_lines: list[str] = []
    in_acc_descr_block = False
    pending_lines = deque(
        (line, _MAX_METADATA_SUFFIXES_PER_LINE) for line in mermaid_code.splitlines()
    )
    while pending_lines:
        raw_line, suffix_budget = pending_lines.popleft()
        # Mermaid quoted strings are line-local. A malformed quote on one line
        # must not suppress comments or metadata detection on later lines.
        in_quoted_text = False
        if in_acc_descr_block:
            close_index = raw_line.find("}")
            if close_index < 0:
                continue
            in_acc_descr_block = False
            raw_line = raw_line[close_index + 1 :]
            if not raw_line.strip():
                continue

        comment_start = None
        inline_metadata_start = None
        inline_acc_descr_block = False
        metadata_tail = None
        escaped = False
        for index, character in enumerate(raw_line):
            if escaped:
                escaped = False
            elif in_quoted_text and character == "\\":
                escaped = True
            elif character == '"':
                in_quoted_text = not in_quoted_text
            elif (
                not in_quoted_text
                and character == "%"
                and index + 1 < len(raw_line)
                and raw_line[index + 1] == "%"
            ):
                comment_start = index
                break
            elif not in_quoted_text and character == ";":
                suffix = raw_line[index + 1 :]
                if accessibility_metadata_supported and (
                    inline_block_match := acc_descr_block_pattern.match(suffix)
                ):
                    inline_metadata_start = index
                    block_value = suffix[inline_block_match.end() :]
                    close_index = block_value.find("}")
                    if close_index < 0:
                        inline_acc_descr_block = True
                    else:
                        metadata_tail = block_value[close_index + 1 :]
                    break
                if (
                    accessibility_metadata_supported and accessibility_colon_pattern.match(suffix)
                ) or (
                    native_title_supported
                    and (title_colon_pattern.match(suffix) or native_title_pattern.match(suffix))
                ):
                    inline_metadata_start = index
                    break
        content_end = (
            comment_start
            if comment_start is not None
            else inline_metadata_start
            if inline_metadata_start is not None
            else len(raw_line)
        )
        line = raw_line[:content_end]
        if inline_acc_descr_block:
            in_acc_descr_block = True
        elif metadata_tail is not None and metadata_tail.strip():
            if suffix_budget <= 0:
                return 0.0
            pending_lines.appendleft((metadata_tail, suffix_budget - 1))
        if not line.strip():
            continue

        if accessibility_metadata_supported and (
            block_match := acc_descr_block_pattern.match(line)
        ):
            in_quoted_text = False
            block_value = line[block_match.end() :]
            close_index = block_value.find("}")
            if close_index < 0:
                in_acc_descr_block = True
            elif (tail := block_value[close_index + 1 :]).strip():
                if suffix_budget <= 0:
                    return 0.0
                pending_lines.appendleft((tail, suffix_budget - 1))
            continue
        if (accessibility_metadata_supported and accessibility_colon_pattern.match(line)) or (
            native_title_supported
            and (title_colon_pattern.match(line) or native_title_pattern.match(line))
        ):
            in_quoted_text = False
            continue
        semantic_lines.append(line)

    first_content = next(
        (line.strip() for line in semantic_lines if line.strip()),
        "",
    )
    is_quadrant_chart = first_content.casefold() == "quadrantchart"
    # Mermaid's quadrant-N prefix selects a grammar slot; N is not rendered chart data.
    quadrant_slot_pattern = re.compile(r"^\s*quadrant-(?P<slot>[1-4])(?=\s|$)", re.IGNORECASE)
    generated: Counter[str] = Counter()
    for line in semantic_lines:
        ignored_slot_span = None
        if is_quadrant_chart and (slot_match := quadrant_slot_pattern.match(line)):
            ignored_slot_span = slot_match.span("slot")
        for match in _NUMERIC_TOKEN_PATTERN.finditer(line):
            if match.span() != ignored_slot_span:
                generated[match.group(0)] += 1
    if not generated:
        return 0.0
    overlap = sum((source & generated).values())
    precision = overlap / generated.total()
    recall = overlap / source.total()
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def aggregate_scores(scores: dict[str, float], config: MermaidConfig) -> float | None:
    semantic_metrics = set(scores) - {"syntax", "render"}
    if not semantic_metrics:
        return None
    weights = config.score_weights.model_dump()
    present = [(weights[key], value) for key, value in scores.items() if key in weights]
    total_weight = sum(weight for weight, _ in present)
    if total_weight == 0:
        return None
    return sum(weight * value for weight, value in present) / total_weight


def semantic_score(scores: dict[str, float], config: MermaidConfig) -> float | None:
    """Return the normalized non-runtime evidence score used by publish policy.

    Parse and render success remain mandatory and contribute to the displayed total,
    but they cannot dilute a zero semantic score into a publishable grade.
    """

    weights = config.score_weights.model_dump()
    present = [
        (weights[key], value)
        for key, value in scores.items()
        if key in weights and key not in {"syntax", "render"}
    ]
    total_weight = sum(weight for weight, _ in present)
    if total_weight == 0:
        return None
    return sum(weight * value for weight, value in present) / total_weight


@dataclass(frozen=True, slots=True)
class PublishDecision:
    publish: bool
    review_required: bool
    grade: str
    reason: str


def decide_publication(
    candidate: MermaidCandidate | None, config: MermaidConfig
) -> PublishDecision:
    if candidate is None:
        return PublishDecision(False, True, "U", "no candidate")
    grade = quality_grade(candidate.aggregate_score)
    if not candidate.syntax_valid or not candidate.render_valid:
        return PublishDecision(False, True, grade, "parse and render are mandatory")
    if config.publish_policy == PublishPolicy.SIDECAR_ONLY:
        return PublishDecision(False, False, grade, "sidecar-only policy")
    if config.publish_policy == PublishPolicy.REVIEW_REQUIRED:
        return PublishDecision(False, True, grade, "review-required policy")
    if candidate.aggregate_score is None:
        return PublishDecision(False, True, "U", "quality could not be evaluated")
    if grade not in {"A", "B", "C"}:
        return PublishDecision(False, True, grade, "grade is below the automatic publication floor")
    semantic = semantic_score(candidate.scores, config)
    if semantic is None:
        return PublishDecision(False, True, grade, "semantic evidence is unavailable")
    if config.publish_policy == PublishPolicy.STRICT_VALIDATED:
        passed = (
            candidate.aggregate_score >= config.review_below_score
            and semantic >= config.review_below_score
        )
        return PublishDecision(passed, not passed, grade, "strict semantic threshold")
    passed = (
        candidate.aggregate_score >= config.publish_min_score
        and semantic >= config.publish_min_score
    )
    return PublishDecision(passed, not passed, grade, "best-effort semantic threshold")
