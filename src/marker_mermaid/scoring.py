"""Reference-free scores, aggregation, and publish decisions."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from marker_mermaid.config import MermaidConfig, PublishPolicy, quality_grade
from marker_mermaid.models import MermaidCandidate


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


def numeric_consistency(source_texts: list[str], mermaid_code: str) -> float | None:
    number_pattern = re.compile(r"(?<!\w)[+-]?(?:\d+(?:\.\d+)?|\.\d+)%?")
    source = Counter(number_pattern.findall(" ".join(source_texts)))
    if not source:
        return None
    semantic_code = "\n".join(
        line
        for line in mermaid_code.splitlines()
        if not re.match(r"\s*(?:accTitle|accDescr|title)\s*:", line, re.IGNORECASE)
    )
    generated = Counter(number_pattern.findall(semantic_code))
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
