"""Reference-free scores, aggregation, and publish decisions."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from marker_mermaid.config import MermaidConfig, PublishPolicy, quality_grade
from marker_mermaid.models import MermaidCandidate


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return {token for token in re.findall(r"[\w가-힣ぁ-んァ-ン一-龥]+", normalized) if token}


def ocr_recall(source_texts: list[str], mermaid_code: str) -> float | None:
    source = set().union(*(_tokens(text) for text in source_texts)) if source_texts else set()
    if not source:
        return None
    generated = _tokens(mermaid_code)
    return len(source & generated) / len(source)


def numeric_consistency(source_texts: list[str], mermaid_code: str) -> float | None:
    number_pattern = re.compile(r"(?<!\w)[+-]?(?:\d+(?:\.\d+)?|\.\d+)%?")
    source = Counter(number_pattern.findall(" ".join(source_texts)))
    if not source:
        return None
    generated = Counter(number_pattern.findall(mermaid_code))
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
    if config.publish_policy == PublishPolicy.STRICT_VALIDATED:
        passed = candidate.aggregate_score >= config.review_below_score
        return PublishDecision(passed, not passed, grade, "strict semantic threshold")
    passed = candidate.aggregate_score >= config.publish_min_score
    return PublishDecision(passed, not passed, grade, "best-effort semantic threshold")
