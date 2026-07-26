"""Fail-closed publication source normalization and result certification."""

from __future__ import annotations

from marker_mermaid.config import (
    MermaidConfig,
    PublishPolicy,
    ScoreWeights,
    SecurityProfile,
)
from marker_mermaid.models import (
    MermaidCandidate,
    ReconstructionResult,
    _publication_authorization_seal,
    canonical_evidence_collection_snapshot,
)
from marker_mermaid.scoring import decide_publication


def canonical_publication_source(code: str) -> str:
    """Keep validated Mermaid fence payloads byte-identical at publication."""

    return code if code.endswith("\n") else code + "\n"


def certify_publication_result(
    result: ReconstructionResult,
    config: MermaidConfig,
) -> bool:
    """Seal only a result that exactly matches a freshly computed policy decision."""

    result.publication_receipt = None
    result._publication_authorization_seal = None
    if type(result) is not ReconstructionResult or type(config) is not MermaidConfig:
        return False
    try:
        evidence_snapshot = canonical_evidence_collection_snapshot(result.evidence)
    except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
        return False
    selected = result.selected
    weights = config.score_weights
    if not (
        type(selected) is MermaidCandidate
        and type(config.publish_policy) is PublishPolicy
        and type(config.security_profile) is SecurityProfile
        and type(config.publish_min_score) is float
        and type(config.review_below_score) is float
        and type(weights) is ScoreWeights
    ):
        return False
    try:
        weight_values = weights.model_dump(mode="python")
        if any(type(value) is not float for value in weight_values.values()):
            return False
        trusted_config = MermaidConfig(
            publish_policy=config.publish_policy,
            security_profile=config.security_profile,
            publish_min_score=config.publish_min_score,
            review_below_score=config.review_below_score,
            score_weights=ScoreWeights.model_validate(weight_values),
        )
    except (TypeError, ValueError):
        return False
    included_fields = {
        "source_id",
        "selected",
        "grade",
        "publish",
        "review_required",
        "status",
    }
    try:
        before_projection = result.model_dump(
            mode="python",
            include=included_fields,
        )
        before_validation_seal = selected._validation_receipt_seal
        shallow_snapshot = ReconstructionResult.model_copy(result, deep=False)
        shallow_snapshot.evidence = list(evidence_snapshot.evidence)
        snapshot = ReconstructionResult.model_copy(shallow_snapshot, deep=True)
        after_projection = result.model_dump(
            mode="python",
            include=included_fields,
        )
        snapshot_projection = snapshot.model_dump(
            mode="python",
            include=included_fields,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    snapshot_selected = snapshot.selected
    if not (
        before_projection == after_projection == snapshot_projection
        and type(before_validation_seal) is str
        and type(snapshot_selected) is MermaidCandidate
        and snapshot_selected._validation_receipt_seal == before_validation_seal
        and snapshot_selected.has_validated_publication_artifacts()
    ):
        return False
    decision = decide_publication(snapshot_selected, trusted_config)
    if decision.publish or not decision.review_required:
        expected_status = "success"
    else:
        expected_status = "review_required"
    if (
        snapshot.grade != decision.grade
        or snapshot.publish is not decision.publish
        or snapshot.review_required is not decision.review_required
        or snapshot.status != expected_status
    ):
        return False
    receipt = snapshot._build_publication_receipt(
        trusted_config.publish_policy,
        trusted_config.security_profile,
    )
    if receipt is None:
        return False
    try:
        final_projection = result.model_dump(
            mode="python",
            include=included_fields,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    if (
        final_projection != before_projection
        or result.selected is not selected
        or selected._validation_receipt_seal != before_validation_seal
    ):
        return False
    result.publication_receipt = receipt
    result._publication_authorization_seal = _publication_authorization_seal(receipt)
    if result.has_trusted_publication_decision():
        return True
    result.publication_receipt = None
    result._publication_authorization_seal = None
    return False
