"""Hash-bound MMX-001 corpus manifests and deterministic release-gate reports.

This module is an aggregator, not a benchmark runner. A trusted runner records
source preservation, validation, process, and budget telemetry in each prediction
artifact. The aggregator verifies every artifact digest, computes corpus metrics,
and fails closed when evidence required by MMX-001 is absent.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from marker_mermaid.config import ALL_TYPES, CORE_TYPES
from marker_mermaid.models import DiagramSceneIR, VisualEvidence
from marker_mermaid.quality import SceneAlignment, injective_node_provenance_counts

MANIFEST_SCHEMA_VERSION = "mmx-eval-manifest-0.1"
GROUND_TRUTH_SCHEMA_VERSION = "mmx-eval-ground-truth-0.1"
PREDICTION_SCHEMA_VERSION = "mmx-eval-prediction-0.1"
REPORT_SCHEMA_VERSION = "mmx-eval-report-0.1"
GATE_PROFILE = "mmx-001-v0.3-extended"
MAX_CASES = 100_000
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_JSON_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_CORPUS_BYTES = 64 * 1024 * 1024 * 1024
MAX_PATHS = 10_000
MAX_PATH_STATES = 100_000
OUTPUT_MARKER = ".marker-mermaid-evaluation.json"

FixtureGroup = Literal[
    "flowchart",
    "uml",
    "architecture_c4",
    "bpmn_swimlane",
    "planning",
    "data_chart",
    "mindmap_tree",
    "specialized",
    "negative",
]
FixtureTier = Literal[
    "synthetic_syntax",
    "synthetic_perturbation",
    "real_scientific",
    "real_enterprise",
    "specialized",
    "multilingual",
    "hand_drawn",
    "negative",
]
EvaluationScope = Literal["serializer", "end_to_end", "detector", "fault_probe"]
SourceOrigin = Literal["real", "synthetic"]
TypeStability = Literal["core", "extended", "experimental", "negative"]
GateStatus = Literal["pass", "fail", "unavailable"]

FIXTURE_MINIMUMS: dict[str, int] = {
    "flowchart": 100,
    "uml": 100,
    "architecture_c4": 80,
    "bpmn_swimlane": 80,
    "planning": 80,
    "data_chart": 120,
    "mindmap_tree": 50,
    "specialized": 100,
    "negative": 150,
}

FUNCTIONAL_TYPES = (
    "flowchart",
    "swimlane",
    "sequence",
    "state",
    "class",
    "er",
    "architecture",
    "c4",
    "requirement",
    "mindmap",
    "timeline",
    "gantt",
    "kanban",
    "pie",
    "xychart",
    "quadrant",
    "sankey",
    "radar",
    "treemap",
    "venn",
    "packet",
    "ishikawa",
)

QUALITY_TARGETS: dict[str, float] = {
    "structural_precision": 0.88,
    "structural_recall": 0.85,
    "core_type_accuracy": 0.85,
    "extended_type_accuracy": 0.70,
    "ocr_label_recall": 0.85,
    "flowchart_edge_f1": 0.75,
    "flowchart_path_f1": 0.65,
    "architecture_node_recall": 0.75,
    "chart_numeric_exact_match": 0.70,
    "human_accept_rate": 0.70,
}

EXTENDED_STABILITY_TYPES = frozenset(
    {
        "swimlane",
        "bpmn",
        "architecture",
        "requirement",
        "mindmap",
        "timeline",
        "gantt",
        "pie",
        "generic_network",
        "organization",
        "data_lineage",
        "deployment",
        "component",
    }
)
TYPE_STABILITY = {
    diagram_type: (
        "core"
        if diagram_type in CORE_TYPES
        else "extended"
        if diagram_type in EXTENDED_STABILITY_TYPES
        else "experimental"
    )
    for diagram_type in ALL_TYPES
}
TYPE_FIXTURE_GROUP = {
    "flowchart": "flowchart",
    "swimlane": "bpmn_swimlane",
    "bpmn": "bpmn_swimlane",
    "sequence": "uml",
    "state": "uml",
    "class": "uml",
    "er": "uml",
    "architecture": "architecture_c4",
    "c4": "architecture_c4",
    "requirement": "uml",
    "mindmap": "mindmap_tree",
    "timeline": "planning",
    "gantt": "planning",
    "journey": "planning",
    "kanban": "planning",
    "gitgraph": "planning",
    "pie": "data_chart",
    "xychart": "data_chart",
    "quadrant": "data_chart",
    "sankey": "data_chart",
    "radar": "data_chart",
    "treemap": "data_chart",
    "venn": "data_chart",
    "packet": "specialized",
    "ishikawa": "specialized",
    "wardley": "specialized",
    "cynefin": "specialized",
    "treeview": "mindmap_tree",
    "block": "uml",
    "eventmodeling": "planning",
    "zenuml": "uml",
    "railroad": "specialized",
    "generic_network": "architecture_c4",
    "organization": "mindmap_tree",
    "data_lineage": "architecture_c4",
    "deployment": "architecture_c4",
    "component": "architecture_c4",
    "usecase": "uml",
}
NUMERIC_ANNOTATION_TYPES = frozenset(
    {"pie", "xychart", "quadrant", "sankey", "radar", "treemap", "gantt", "packet"}
)

if set(TYPE_FIXTURE_GROUP) != set(ALL_TYPES):  # pragma: no cover - import-time invariant
    raise RuntimeError("evaluation fixture-group map must cover every configured diagram type")


class EvaluationManifestError(ValueError):
    """A manifest or one of its hash-bound artifacts is invalid."""


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    sha256: str

    @field_validator("path")
    @classmethod
    def path_is_confined(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("artifact path must be a confined relative POSIX path")
        return value

    @field_validator("sha256")
    @classmethod
    def digest_is_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("artifact sha256 must be lowercase 64-hex")
        return value


class CorpusMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_id: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    license: str = Field(min_length=1, max_length=512)
    split: str = Field(min_length=1, max_length=128)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=256)
    fixture_group: FixtureGroup
    fixture_tiers: list[FixtureTier] = Field(min_length=1, max_length=8)
    source_origin: SourceOrigin
    scope: EvaluationScope
    languages: list[str] = Field(min_length=1, max_length=16)
    source: ArtifactRef
    ground_truth: ArtifactRef
    prediction: ArtifactRef

    @field_validator("case_id")
    @classmethod
    def case_id_is_safe(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", value):
            raise ValueError("case id uses unsupported characters")
        return value

    @field_validator("fixture_tiers")
    @classmethod
    def fixture_tiers_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("fixture tiers must be unique")
        return value

    @field_validator("languages")
    @classmethod
    def languages_are_bounded_tags(cls, values: list[str]) -> list[str]:
        language_pattern = r"[A-Za-z0-9]{1,8}(?:-[A-Za-z0-9]{1,8})*"
        if any(not re.fullmatch(language_pattern, item) for item in values):
            raise ValueError("languages must contain bounded BCP-47-like tags")
        if len(values) != len(set(values)):
            raise ValueError("languages must be unique")
        return values

    @model_validator(mode="after")
    def artifacts_are_separate(self) -> EvaluationCase:
        paths = {self.source.path, self.ground_truth.path, self.prediction.path}
        if len(paths) != 3:
            raise ValueError("source, ground truth, and prediction must be separate artifacts")
        if self.fixture_group == "negative" and "negative" not in self.fixture_tiers:
            raise ValueError("negative fixture group requires the negative tier")
        return self


class EvaluationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[MANIFEST_SCHEMA_VERSION] = MANIFEST_SCHEMA_VERSION
    gate_profile: Literal[GATE_PROFILE] = GATE_PROFILE
    corpus: CorpusMetadata
    cases: list[EvaluationCase] = Field(min_length=1, max_length=MAX_CASES)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> EvaluationManifest:
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation case ids must be unique")
        return self


class EvaluationGroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[GROUND_TRUTH_SCHEMA_VERSION] = GROUND_TRUTH_SCHEMA_VERSION
    expected_reconstruction: bool
    diagram_type: str | None = Field(default=None, max_length=128)
    type_stability: TypeStability
    scene_ir: DiagramSceneIR | None = None
    ocr_labels: list[str] = Field(default_factory=list, max_length=20_000)
    numbers: list[str] = Field(default_factory=list, max_length=20_000)
    numeric_applicable: bool | None = None
    numeric_unavailable_reason: str | None = Field(default=None, max_length=1024)
    path_applicable: bool | None = None
    path_unavailable_reason: str | None = Field(default=None, max_length=1024)
    shared_id_namespace: bool = False
    human_accepted: bool | None = None

    @field_validator("ocr_labels", "numbers")
    @classmethod
    def values_are_bounded(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 4096 for value in values):
            raise ValueError("ground-truth values must be non-empty and bounded")
        return values

    @field_validator("numbers")
    @classmethod
    def numbers_are_canonical(cls, values: list[str]) -> list[str]:
        return _canonical_numbers(values)

    @model_validator(mode="after")
    def expectation_is_consistent(self) -> EvaluationGroundTruth:
        if self.expected_reconstruction:
            if self.diagram_type is None or self.type_stability == "negative":
                raise ValueError("positive ground truth requires a diagram type and stability")
            if self.scene_ir is None or not self.scene_ir.elements:
                raise ValueError("positive ground truth requires a non-empty independent scene")
            expected_stability = TYPE_STABILITY.get(self.diagram_type)
            if expected_stability is None:
                raise ValueError("ground-truth diagram type is not configured")
            if self.type_stability != expected_stability:
                raise ValueError(
                    f"{self.diagram_type} requires {expected_stability} type stability"
                )
            scene_tokens = _token_counter(
                [element.text for element in self.scene_ir.elements if element.text]
            )
            annotated_tokens = _token_counter(self.ocr_labels)
            if scene_tokens - annotated_tokens:
                raise ValueError("OCR annotations must cover all text-bearing scene elements")
            if self.diagram_type in NUMERIC_ANNOTATION_TYPES:
                if self.numeric_applicable is None:
                    raise ValueError("numeric diagram requires an applicability annotation")
                if self.numeric_applicable and not self.numbers:
                    raise ValueError("applicable numeric diagram requires annotated numbers")
                if not self.numeric_applicable and self.numbers:
                    raise ValueError("non-applicable numeric diagram cannot contain numbers")
                if not self.numeric_applicable and not self.numeric_unavailable_reason:
                    raise ValueError("non-applicable numeric diagram requires a reason")
            if self.diagram_type == "flowchart":
                if self.path_applicable is None:
                    raise ValueError("flowchart ground truth requires path applicability")
                if not self.path_applicable and not self.path_unavailable_reason:
                    raise ValueError("non-applicable flowchart path requires a reason")
        elif (
            self.diagram_type is not None
            or self.scene_ir is not None
            or bool(self.ocr_labels)
            or bool(self.numbers)
            or self.numeric_applicable is not None
            or self.path_applicable is not None
            or self.human_accepted is not None
        ):
            raise ValueError("negative ground truth cannot contain diagram annotations")
        elif self.type_stability != "negative":
            raise ValueError("negative ground truth requires negative type stability")
        return self


class RunnerTelemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_preserved: bool
    candidate_failure_injected: bool = False
    document_failed: bool
    forbidden_external_actions: int = Field(ge=0)
    duplicate_mermaid_insertions: int = Field(ge=0)
    orphan_processes: int = Field(ge=0)
    candidate_budget_exceeded: bool


class EvaluationPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[PREDICTION_SCHEMA_VERSION] = PREDICTION_SCHEMA_VERSION
    reconstruction_present: bool
    diagram_type: str | None = Field(default=None, max_length=128)
    generated_scene_ir: DiagramSceneIR | None = None
    evidence: list[VisualEvidence] = Field(default_factory=list, max_length=100_000)
    numbers: list[str] = Field(default_factory=list, max_length=20_000)
    published: bool
    syntax_valid: bool | None = None
    render_valid: bool | None = None
    grade: Literal["A", "B", "C", "D", "U"] = "U"
    experimental_warning_present: bool = False
    sidecar_present: bool = False
    review_available: bool = False
    hallucination_precision: float | None = Field(default=None, ge=0, le=1)
    telemetry: RunnerTelemetry

    @field_validator("numbers")
    @classmethod
    def numbers_are_bounded(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 256 for value in values):
            raise ValueError("prediction numbers must be non-empty and bounded")
        return _canonical_numbers(values)

    @field_validator("evidence")
    @classmethod
    def evidence_ids_are_unique(cls, values: list[VisualEvidence]) -> list[VisualEvidence]:
        ids = [item.id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("prediction evidence ids must be unique")
        return values

    @model_validator(mode="after")
    def reconstruction_is_consistent(self) -> EvaluationPrediction:
        if self.reconstruction_present and self.diagram_type is None:
            raise ValueError("a present reconstruction requires a diagram type")
        if not self.reconstruction_present and any(
            (self.diagram_type, self.generated_scene_ir, self.numbers, self.published)
        ):
            raise ValueError("an absent reconstruction cannot contain generated output")
        return self


@dataclass(frozen=True, slots=True)
class LoadedCase:
    definition: EvaluationCase
    ground_truth: EvaluationGroundTruth
    prediction: EvaluationPrediction


@dataclass(frozen=True, slots=True)
class LoadedEvaluation:
    manifest: EvaluationManifest
    cases: tuple[LoadedCase, ...]
    root: Path
    manifest_sha256: str
    manifest_payload: bytes


class GateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: GateStatus
    observed: float | int | None = None
    required: float | int | str
    sample_count: int = 0
    unit: str = "cases"
    counts: dict[str, int] = Field(default_factory=dict)
    evidence_case_ids: list[str] = Field(default_factory=list)
    unavailable_case_ids: list[str] = Field(default_factory=list)
    detail: str


class CaseEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    expected_type: str | None
    predicted_type: str | None
    metrics: dict[str, float | None]


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[REPORT_SCHEMA_VERSION] = REPORT_SCHEMA_VERSION
    manifest_schema_version: Literal[MANIFEST_SCHEMA_VERSION] = MANIFEST_SCHEMA_VERSION
    manifest_sha256: str
    gate_profile: Literal[GATE_PROFILE] = GATE_PROFILE
    attestation: Literal["trusted_runner_input"] = "trusted_runner_input"
    corpus: CorpusMetadata
    case_count: int
    fixture_counts: dict[str, int]
    gates: list[GateResult]
    cases: list[CaseEvaluation]
    overall_status: GateStatus


def load_evaluation_manifest(path: str | Path) -> LoadedEvaluation:
    """Load a manifest and verify all source, annotation, and prediction hashes."""

    manifest_path = Path(path).absolute()
    payload, _, _ = _read_regular_file(
        manifest_path,
        MAX_MANIFEST_BYTES,
        "evaluation manifest",
    )
    try:
        manifest = EvaluationManifest.model_validate_json(payload)
    except ValidationError as error:
        raise EvaluationManifestError(f"invalid evaluation manifest: {error}") from error
    root = manifest_path.parent.resolve()
    loaded: list[LoadedCase] = []
    source_digests: set[str] = set()
    total_bytes = len(payload)
    for case in manifest.cases:
        source_path, _, source_size = _resolve_artifact(
            root, case.source, case.case_id, MAX_SOURCE_BYTES, retain_payload=False
        )
        ground_truth_path, ground_truth_payload, ground_truth_size = _resolve_artifact(
            root, case.ground_truth, case.case_id, MAX_JSON_ARTIFACT_BYTES
        )
        prediction_path, prediction_payload, prediction_size = _resolve_artifact(
            root, case.prediction, case.case_id, MAX_JSON_ARTIFACT_BYTES
        )
        total_bytes += source_size + ground_truth_size + prediction_size
        if total_bytes > MAX_CORPUS_BYTES:
            raise EvaluationManifestError("evaluation corpus exceeds the 64 GiB read budget")
        if len({source_path, ground_truth_path, prediction_path}) != 3:
            raise EvaluationManifestError(
                f"case {case.case_id}: resolved artifacts must remain distinct"
            )
        try:
            ground_truth = EvaluationGroundTruth.model_validate_json(ground_truth_payload)
            prediction = EvaluationPrediction.model_validate_json(prediction_payload)
        except ValidationError as error:
            raise EvaluationManifestError(
                f"case {case.case_id}: invalid evaluation artifact: {error}"
            ) from error
        if (case.fixture_group == "negative") != (not ground_truth.expected_reconstruction):
            raise EvaluationManifestError(
                f"case {case.case_id}: fixture group and reconstruction expectation disagree"
            )
        if ground_truth.expected_reconstruction:
            expected_group = TYPE_FIXTURE_GROUP[ground_truth.diagram_type]  # type: ignore[index]
            if case.fixture_group != expected_group:
                raise EvaluationManifestError(
                    f"case {case.case_id}: {ground_truth.diagram_type} requires "
                    f"fixture group {expected_group}"
                )
            if ground_truth.diagram_type == "flowchart" and ground_truth.path_applicable:
                paths = _root_to_terminal_paths(ground_truth.scene_ir)  # type: ignore[arg-type]
                if not paths:
                    raise EvaluationManifestError(
                        f"case {case.case_id}: applicable flowchart path is unavailable"
                    )
        if case.scope == "fault_probe" and not prediction.telemetry.candidate_failure_injected:
            raise EvaluationManifestError(
                f"case {case.case_id}: fault_probe requires injected candidate failure telemetry"
            )
        if case.source.sha256 in source_digests:
            raise EvaluationManifestError(
                f"case {case.case_id}: duplicate source digest cannot count as another fixture"
            )
        source_digests.add(case.source.sha256)
        loaded.append(LoadedCase(case, ground_truth, prediction))
    return LoadedEvaluation(
        manifest=manifest,
        cases=tuple(loaded),
        root=root,
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
        manifest_payload=payload,
    )


def _read_regular_file(
    path: Path,
    maximum: int,
    label: str,
    *,
    retain_payload: bool = True,
) -> tuple[bytes, int, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise EvaluationManifestError(f"{label} is unavailable: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise EvaluationManifestError(f"{label} must be a regular non-symlink file")
        if metadata.st_size > maximum:
            raise EvaluationManifestError(f"{label} exceeds its size limit")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            digest.update(chunk)
            if retain_payload:
                chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise EvaluationManifestError(f"{label} exceeds its size limit")
        return b"".join(chunks), total, digest.hexdigest()
    except OSError as error:
        raise EvaluationManifestError(f"{label} is unavailable: {error}") from error
    finally:
        os.close(descriptor)


def _resolve_artifact(
    root: Path,
    reference: ArtifactRef,
    case_id: str,
    maximum: int,
    *,
    retain_payload: bool = True,
) -> tuple[Path, bytes, int]:
    lexical = root.joinpath(*PurePosixPath(reference.path).parts)
    current = root
    for part in PurePosixPath(reference.path).parts:
        current = current / part
        if current.is_symlink():
            raise EvaluationManifestError(f"case {case_id}: artifact path contains a symlink")
    resolved = lexical.resolve()
    if root != resolved and root not in resolved.parents:
        raise EvaluationManifestError(f"case {case_id}: artifact escapes manifest root")
    payload, size, digest = _read_regular_file(
        resolved,
        maximum,
        f"case {case_id} artifact",
        retain_payload=retain_payload,
    )
    if digest != reference.sha256:
        raise EvaluationManifestError(f"case {case_id}: artifact sha256 mismatch")
    return resolved, payload if retain_payload else b"", size


def evaluate_manifest(loaded: LoadedEvaluation) -> EvaluationReport:
    """Compute the fixed MMX-001 v0.3 extended gate profile."""

    fixture_counts = Counter(
        case.definition.fixture_group
        for case in loaded.cases
        if _is_positive_end_to_end(case) or _is_negative_detector(case)
    )
    gates = [
        *_hard_gates(loaded.cases),
        *_annotation_gates(loaded.cases),
        *_fixture_gates(fixture_counts),
        *_functional_gates(loaded.cases),
        *_quality_gates(loaded.cases),
    ]
    overall: GateStatus
    if any(gate.status == "fail" for gate in gates):
        overall = "fail"
    elif any(gate.status == "unavailable" for gate in gates):
        overall = "unavailable"
    else:
        overall = "pass"
    return EvaluationReport(
        manifest_sha256=loaded.manifest_sha256,
        corpus=loaded.manifest.corpus,
        case_count=len(loaded.cases),
        fixture_counts={name: fixture_counts.get(name, 0) for name in FIXTURE_MINIMUMS},
        gates=gates,
        cases=[_case_evaluation(case) for case in loaded.cases],
        overall_status=overall,
    )


def _hard_gates(cases: tuple[LoadedCase, ...]) -> list[GateResult]:
    published = [case.prediction for case in cases if case.prediction.published]
    fault_probes = [
        case.prediction.telemetry for case in cases if case.definition.scope == "fault_probe"
    ]
    gates = [
        _all_true_gate(
            "published_parse_success",
            [prediction.syntax_valid for prediction in published],
            "100%",
        ),
        _all_true_gate(
            "published_render_success",
            [prediction.render_valid for prediction in published],
            "100%",
        ),
        _all_true_gate(
            "published_grade_allowed",
            [prediction.grade in {"A", "B", "C"} for prediction in published],
            "100%",
        ),
        _all_true_gate(
            "original_image_preservation",
            [
                case.prediction.telemetry.original_preserved
                for case in cases
                if case.definition.scope != "serializer"
            ],
            "100%",
        ),
        _all_false_gate(
            "candidate_failure_document_failure",
            [telemetry.document_failed for telemetry in fault_probes],
            require_samples=True,
        ),
        _sum_zero_gate(
            "forbidden_external_actions",
            [case.prediction.telemetry.forbidden_external_actions for case in cases],
        ),
        _sum_zero_gate(
            "duplicate_mermaid_insertions",
            [case.prediction.telemetry.duplicate_mermaid_insertions for case in cases],
        ),
        _sum_zero_gate(
            "orphan_processes",
            [case.prediction.telemetry.orphan_processes for case in cases],
        ),
        _all_false_gate(
            "candidate_budget_exceeded",
            [case.prediction.telemetry.candidate_budget_exceeded for case in cases],
        ),
    ]
    reconstructed = [
        case
        for case in cases
        if _is_positive_end_to_end(case) and case.prediction.reconstruction_present
    ]
    missing_generated_scene = any(
        case.prediction.generated_scene_ir is None for case in reconstructed
    )
    if not reconstructed or missing_generated_scene:
        gates.append(_unavailable_gate("generated_nodes_without_provenance", "<= 0.20"))
    else:
        supported_nodes = 0
        total_nodes = 0
        for case in reconstructed:
            supported, total = injective_node_provenance_counts(
                (
                    element.evidence_ids
                    for element in case.prediction.generated_scene_ir.elements  # type: ignore[union-attr]
                ),
                case.prediction.evidence,
            )
            supported_nodes += supported
            total_nodes += total
        if not total_nodes:
            gates.append(_unavailable_gate("generated_nodes_without_provenance", "<= 0.20"))
        else:
            missing = total_nodes - supported_nodes
            gates.append(
                _threshold_gate(
                    "generated_nodes_without_provenance",
                    missing / total_nodes,
                    0.20,
                    total_nodes,
                    maximum=True,
                )
            )
    experimental = [
        case
        for case in cases
        if _is_positive_end_to_end(case)
        and case.ground_truth.type_stability == "experimental"
        and case.prediction.reconstruction_present
    ]
    gates.extend(
        [
            _all_true_gate(
                "experimental_warning_present",
                [case.prediction.experimental_warning_present for case in experimental],
                "100%",
            ),
            _all_true_gate(
                "experimental_sidecar_present",
                [case.prediction.sidecar_present for case in experimental],
                "100%",
            ),
            _all_true_gate(
                "experimental_review_available",
                [case.prediction.review_available for case in experimental],
                "100%",
            ),
            _all_true_gate(
                "experimental_hallucination_score_present",
                [case.prediction.hallucination_precision is not None for case in experimental],
                "100%",
            ),
        ]
    )
    return gates


def _annotation_gates(cases: tuple[LoadedCase, ...]) -> list[GateResult]:
    positive = [case for case in cases if case.ground_truth.expected_reconstruction]
    text_bearing = [
        case
        for case in positive
        if any(element.text for element in case.ground_truth.scene_ir.elements)  # type: ignore[union-attr]
    ]
    numeric = [
        case
        for case in positive
        if case.ground_truth.diagram_type in NUMERIC_ANNOTATION_TYPES
    ]
    reviewed = [
        case
        for case in cases
        if _is_positive_end_to_end(case)
        and case.prediction.published
        and case.prediction.grade in {"A", "B", "C"}
    ]
    return [
        _all_true_gate(
            "ground_truth_scene_coverage",
            [bool(case.ground_truth.scene_ir.elements) for case in positive],  # type: ignore[union-attr]
            "100%",
        ),
        _all_true_gate(
            "ocr_annotation_coverage",
            [bool(case.ground_truth.ocr_labels) for case in text_bearing],
            "100%",
        ),
        _all_true_gate(
            "numeric_annotation_coverage",
            [case.ground_truth.numeric_applicable is not None for case in numeric],
            "100%",
        ),
        _all_true_gate(
            "human_review_coverage",
            [case.ground_truth.human_accepted is not None for case in reviewed],
            "100%",
        ),
    ]


def _fixture_gates(counts: Counter[str]) -> list[GateResult]:
    return [
        GateResult(
            name=f"fixture_count:{group}",
            status="pass" if counts.get(group, 0) >= minimum else "fail",
            observed=counts.get(group, 0),
            required=minimum,
            sample_count=counts.get(group, 0),
            detail=f"{counts.get(group, 0)} fixture(s); minimum {minimum}",
        )
        for group, minimum in FIXTURE_MINIMUMS.items()
    ]


def _functional_gates(cases: tuple[LoadedCase, ...]) -> list[GateResult]:
    real_end_to_end = [
        case
        for case in cases
        if case.definition.source_origin == "real" and case.definition.scope == "end_to_end"
    ]
    passed_types = {
        case.ground_truth.diagram_type
        for case in real_end_to_end
        if case.prediction.diagram_type == case.ground_truth.diagram_type
        and case.prediction.syntax_valid is True
        and case.prediction.render_valid is True
    }
    sample_counts = Counter(case.ground_truth.diagram_type for case in real_end_to_end)
    return [
        GateResult(
            name=f"functional_type:{diagram_type}",
            status="pass" if diagram_type in passed_types else "fail",
            observed=int(diagram_type in passed_types),
            required=1,
            sample_count=sample_counts.get(diagram_type, 0),
            detail="requires one exact-type, parsed, rendered, real end-to-end fixture",
        )
        for diagram_type in FUNCTIONAL_TYPES
    ]


def _quality_gates(cases: tuple[LoadedCase, ...]) -> list[GateResult]:
    structural = _structural_counts(cases)
    edge = _flowchart_edge_counts(cases)
    path = _flowchart_path_counts(cases)
    metrics: dict[str, tuple[float | None, int]] = {
        "structural_precision": (
            _ratio(structural[0], structural[0] + structural[1]),
            structural[0] + structural[1],
        ),
        "structural_recall": (
            _ratio(structural[0], structural[0] + structural[2]),
            structural[0] + structural[2],
        ),
        "core_type_accuracy": _type_accuracy(cases, "core"),
        "extended_type_accuracy": _type_accuracy(cases, "extended"),
        "ocr_label_recall": _ocr_recall(cases),
        "flowchart_edge_f1": (_f1(*edge[:3]) if edge[3] else None, edge[3]),
        "flowchart_path_f1": (
            _f1(*path[:3]) if path[3] and not path[4] else None,
            path[3],
        ),
        "architecture_node_recall": _architecture_node_recall(cases),
        "chart_numeric_exact_match": _chart_numeric_exact_match(cases),
        "human_accept_rate": _human_accept_rate(cases),
    }
    gates = [
        (
            _threshold_gate(name, value, QUALITY_TARGETS[name], count)
            if value is not None
            else _unavailable_gate(name, QUALITY_TARGETS[name])
        )
        for name, (value, count) in metrics.items()
    ]
    by_name = {gate.name: gate for gate in gates}
    by_name["structural_precision"].unit = "scene_components"
    by_name["structural_precision"].counts = {
        "true_positive": structural[0],
        "false_positive": structural[1],
        "false_negative": structural[2],
    }
    by_name["structural_recall"].unit = "scene_components"
    by_name["structural_recall"].counts = dict(
        by_name["structural_precision"].counts
    )
    by_name["ocr_label_recall"].unit = "tokens"
    by_name["flowchart_edge_f1"].unit = "flowchart_cases"
    by_name["flowchart_edge_f1"].counts = {
        "true_positive": edge[0],
        "false_positive": edge[1],
        "false_negative": edge[2],
    }
    by_name["flowchart_path_f1"].unit = "flowchart_cases"
    by_name["flowchart_path_f1"].counts = {
        "true_positive": path[0],
        "false_positive": path[1],
        "false_negative": path[2],
    }
    by_name["flowchart_path_f1"].unavailable_case_ids = list(path[4])
    by_name["architecture_node_recall"].unit = "nodes"
    positive_ids = [
        case.definition.case_id for case in cases if _is_positive_end_to_end(case)
    ]
    structural_positive_ids = [
        case.definition.case_id for case in cases if _is_structural_positive(case)
    ]
    structural_ids = [
        case.definition.case_id
        for case in cases
        if _is_structural_positive(case) or _is_negative_detector(case)
    ]
    evidence_ids = {
        "structural_precision": structural_ids,
        "structural_recall": structural_positive_ids,
        "core_type_accuracy": [
            case.definition.case_id
            for case in cases
            if _is_positive_end_to_end(case) and case.ground_truth.type_stability == "core"
        ],
        "extended_type_accuracy": [
            case.definition.case_id
            for case in cases
            if _is_positive_end_to_end(case)
            and case.ground_truth.type_stability == "extended"
        ],
        "ocr_label_recall": positive_ids,
        "flowchart_edge_f1": [
            case.definition.case_id
            for case in cases
            if _is_positive_end_to_end(case)
            and case.ground_truth.diagram_type == "flowchart"
        ],
        "flowchart_path_f1": [
            case.definition.case_id
            for case in cases
            if _is_positive_end_to_end(case)
            and case.ground_truth.diagram_type == "flowchart"
            and case.ground_truth.path_applicable
        ],
        "architecture_node_recall": [
            case.definition.case_id
            for case in cases
            if _is_positive_end_to_end(case)
            and case.ground_truth.diagram_type == "architecture"
        ],
        "chart_numeric_exact_match": [
            case.definition.case_id
            for case in cases
            if _is_positive_end_to_end(case)
            and case.definition.fixture_group == "data_chart"
            and case.ground_truth.numeric_applicable
        ],
        "human_accept_rate": [
            case.definition.case_id
            for case in cases
            if _is_positive_end_to_end(case)
            and case.prediction.published
            and case.prediction.grade in {"A", "B", "C"}
        ],
    }
    for gate in gates:
        gate.evidence_case_ids = evidence_ids[gate.name]
    return gates


def _structural_counts(cases: tuple[LoadedCase, ...]) -> tuple[int, int, int]:
    true_positive = false_positive = false_negative = 0
    for case in cases:
        generated = case.prediction.generated_scene_ir
        if _is_negative_detector(case):
            if generated is not None:
                false_positive += len(generated.elements) + len(generated.relations)
            continue
        if not _is_structural_positive(case):
            continue
        reference = case.ground_truth.scene_ir
        if reference is None:
            continue
        if generated is None:
            false_negative += len(reference.elements) + len(reference.relations)
            continue
        alignment = _align_case(case, reference, generated)
        true_positive += len(alignment.generated_to_source)
        false_positive += len(alignment.unmatched_generated_ids)
        false_negative += len(alignment.unmatched_source_ids)
        edge_counts = _edge_counts(
            reference,
            generated,
            shared_id_namespace=case.ground_truth.shared_id_namespace,
        )
        true_positive += edge_counts[0]
        false_positive += edge_counts[1]
        false_negative += edge_counts[2]
    return true_positive, false_positive, false_negative


def _edge_counts(
    reference: DiagramSceneIR,
    generated: DiagramSceneIR,
    *,
    shared_id_namespace: bool = False,
) -> tuple[int, int, int]:
    alignment = _align_elements(reference, generated, shared_id_namespace=shared_id_namespace)
    reference_edges = _edge_counter(reference)
    generated_edges = _edge_counter(generated, alignment.generated_to_source)
    overlap = sum((reference_edges & generated_edges).values())
    return overlap, generated_edges.total() - overlap, reference_edges.total() - overlap


def _edge_counter(
    scene: DiagramSceneIR,
    mapping: dict[str, str] | None = None,
) -> Counter[tuple[str, str]]:
    counter: Counter[tuple[str, str]] = Counter()
    for relation in scene.relations:
        if relation.source_id is None or relation.target_id is None:
            continue
        if mapping is None:
            source, target = relation.source_id, relation.target_id
        else:
            source = mapping.get(relation.source_id, f"generated:{relation.source_id}")
            target = mapping.get(relation.target_id, f"generated:{relation.target_id}")
        counter[tuple(sorted((source, target)))] += 1
    return counter


def _directed_edge_counter(
    scene: DiagramSceneIR,
    mapping: dict[str, str] | None = None,
) -> Counter[tuple[str, str, str]]:
    counter: Counter[tuple[str, str, str]] = Counter()
    for relation in scene.relations:
        if relation.source_id is None or relation.target_id is None:
            continue
        if mapping is None:
            source, target = relation.source_id, relation.target_id
        else:
            source = mapping.get(relation.source_id, f"generated:{relation.source_id}")
            target = mapping.get(relation.target_id, f"generated:{relation.target_id}")
        if relation.arrow_at_end:
            counter[(source, target, "directed")] += 1
        if relation.arrow_at_start:
            counter[(target, source, "directed")] += 1
        if not relation.arrow_at_start and not relation.arrow_at_end:
            left, right = sorted((source, target))
            counter[(left, right, "undirected")] += 1
    return counter


def _flowchart_edge_counts(cases: tuple[LoadedCase, ...]) -> tuple[int, int, int, int]:
    true_positive = false_positive = false_negative = samples = 0
    for case in cases:
        if (
            not _is_positive_end_to_end(case)
            or case.ground_truth.diagram_type != "flowchart"
            or case.ground_truth.scene_ir is None
        ):
            continue
        reference = case.ground_truth.scene_ir
        if not _directed_edge_counter(reference):
            continue
        samples += 1
        generated = case.prediction.generated_scene_ir or _empty_scene()
        alignment = _align_case(case, reference, generated)
        reference_edges = _directed_edge_counter(reference)
        generated_edges = _directed_edge_counter(generated, alignment.generated_to_source)
        overlap = sum((reference_edges & generated_edges).values())
        counts = (
            overlap,
            generated_edges.total() - overlap,
            reference_edges.total() - overlap,
        )
        true_positive += counts[0]
        false_positive += counts[1]
        false_negative += counts[2]
    return true_positive, false_positive, false_negative, samples


def _flowchart_path_counts(
    cases: tuple[LoadedCase, ...],
) -> tuple[int, int, int, int, tuple[str, ...]]:
    true_positive = false_positive = false_negative = samples = 0
    unavailable: list[str] = []
    for case in cases:
        reference = case.ground_truth.scene_ir
        if (
            not _is_positive_end_to_end(case)
            or case.ground_truth.diagram_type != "flowchart"
            or reference is None
            or not case.ground_truth.path_applicable
        ):
            continue
        reference_paths = _root_to_terminal_paths(reference)
        if reference_paths is None or not reference_paths:
            unavailable.append(case.definition.case_id)
            continue
        samples += 1
        generated = case.prediction.generated_scene_ir
        if generated is None:
            false_negative += reference_paths.total()
            continue
        alignment = _align_case(case, reference, generated)
        generated_paths = _root_to_terminal_paths(generated)
        if generated_paths is None:
            unavailable.append(case.definition.case_id)
            continue
        mapped = Counter(
            tuple(alignment.generated_to_source.get(node, f"generated:{node}") for node in path)
            for path, count in generated_paths.items()
            for _ in range(count)
        )
        overlap = sum((reference_paths & mapped).values())
        true_positive += overlap
        false_positive += mapped.total() - overlap
        false_negative += reference_paths.total() - overlap
    return true_positive, false_positive, false_negative, samples, tuple(unavailable)


def _root_to_terminal_paths(scene: DiagramSceneIR) -> Counter[tuple[str, ...]] | None:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for relation in scene.relations:
        if relation.source_id is None or relation.target_id is None:
            continue
        if relation.arrow_at_end:
            adjacency[relation.source_id].add(relation.target_id)
        if relation.arrow_at_start:
            adjacency[relation.target_id].add(relation.source_id)
    nodes = set(adjacency) | {target for targets in adjacency.values() for target in targets}
    if not nodes:
        return Counter()
    indegree = Counter(target for targets in adjacency.values() for target in targets)
    roots = sorted(node for node in nodes if indegree[node] == 0)
    terminals = {node for node in nodes if not adjacency.get(node)}
    if not roots or not terminals:
        return None
    paths: Counter[tuple[str, ...]] = Counter()
    stack = [(root, (root,)) for root in reversed(roots)]
    expanded = 0
    while stack:
        expanded += 1
        if expanded > MAX_PATH_STATES:
            return None
        node, path = stack.pop()
        if node in terminals and len(path) >= 2:
            paths[path] += 1
            if paths.total() > MAX_PATHS:
                return None
            continue
        if len(path) >= len(nodes):
            continue
        for target in sorted(adjacency.get(node, ()), reverse=True):
            if target not in path:
                if expanded + len(stack) >= MAX_PATH_STATES:
                    return None
                stack.append((target, (*path, target)))
    return paths


def _empty_scene() -> DiagramSceneIR:
    return DiagramSceneIR(elements=[], relations=[], groups=[])


def _type_accuracy(
    cases: tuple[LoadedCase, ...], stability: str
) -> tuple[float | None, int]:
    selected = [
        case
        for case in cases
        if _is_positive_end_to_end(case) and case.ground_truth.type_stability == stability
    ]
    if not selected:
        return None, 0
    correct = sum(
        case.prediction.diagram_type == case.ground_truth.diagram_type for case in selected
    )
    return correct / len(selected), len(selected)


def _ocr_recall(cases: tuple[LoadedCase, ...]) -> tuple[float | None, int]:
    expected: Counter[tuple[str, str]] = Counter()
    observed: Counter[tuple[str, str]] = Counter()
    for case in cases:
        if not _is_positive_end_to_end(case):
            continue
        expected.update(
            (case.definition.case_id, token)
            for token in _token_list(case.ground_truth.ocr_labels)
        )
        scene = case.prediction.generated_scene_ir
        if scene is not None:
            labels = [element.text for element in scene.elements if element.text]
            observed.update(
                (case.definition.case_id, token) for token in _token_list(labels)
            )
    if not expected:
        return None, 0
    overlap = sum((expected & observed).values())
    return overlap / expected.total(), expected.total()


def _token_list(values: list[str]) -> list[str]:
    normalized = unicodedata.normalize("NFKC", " ".join(values)).casefold()
    return re.findall(r"[\w가-힣ぁ-んァ-ン一-龥]+", normalized)


def _token_counter(values: list[str]) -> Counter[str]:
    return Counter(_token_list(values))


def _canonical_numbers(values: list[str]) -> list[str]:
    canonical: list[str] = []
    for value in values:
        try:
            number = Decimal(value)
        except InvalidOperation as error:
            raise ValueError(f"invalid numeric annotation: {value!r}") from error
        if not number.is_finite():
            raise ValueError("numeric annotations must be finite")
        canonical.append(format(number.normalize(), "E"))
    return canonical


def _architecture_node_recall(cases: tuple[LoadedCase, ...]) -> tuple[float | None, int]:
    aligned = total = 0
    for case in cases:
        reference = case.ground_truth.scene_ir
        if (
            not _is_positive_end_to_end(case)
            or case.ground_truth.diagram_type != "architecture"
            or reference is None
        ):
            continue
        total += len(reference.elements)
        generated = case.prediction.generated_scene_ir
        if generated is not None:
            aligned += len(_align_case(case, reference, generated).generated_to_source)
    return _ratio(aligned, total), total


def _chart_numeric_exact_match(cases: tuple[LoadedCase, ...]) -> tuple[float | None, int]:
    selected = [
        case
        for case in cases
        if _is_positive_end_to_end(case)
        and case.definition.fixture_group == "data_chart"
        and case.ground_truth.numeric_applicable
    ]
    if not selected:
        return None, 0
    exact = sum(
        Counter(case.ground_truth.numbers) == Counter(case.prediction.numbers)
        for case in selected
    )
    return exact / len(selected), len(selected)


def _human_accept_rate(cases: tuple[LoadedCase, ...]) -> tuple[float | None, int]:
    selected = [
        case
        for case in cases
        if _is_positive_end_to_end(case)
        and case.prediction.published
        and case.prediction.grade in {"A", "B", "C"}
    ]
    if not selected:
        return None, 0
    if any(case.ground_truth.human_accepted is None for case in selected):
        return None, len(selected)
    accepted = sum(case.ground_truth.human_accepted is True for case in selected)
    return accepted / len(selected), len(selected)


def _case_evaluation(case: LoadedCase) -> CaseEvaluation:
    metrics: dict[str, float | None] = {
        "type_correct": (
            float(case.prediction.diagram_type == case.ground_truth.diagram_type)
            if case.ground_truth.expected_reconstruction
            else None
        ),
        "node_recall": None,
        "edge_f1": None,
        "path_f1": None,
    }
    reference = case.ground_truth.scene_ir
    generated = case.prediction.generated_scene_ir
    if reference is not None and generated is None:
        metrics["node_recall"] = 0.0 if reference.elements else None
        reference_edges = (
            _directed_edge_counter(reference)
            if case.ground_truth.diagram_type == "flowchart"
            else _edge_counter(reference)
        )
        metrics["edge_f1"] = 0.0 if reference_edges else None
        reference_paths = _root_to_terminal_paths(reference)
        metrics["path_f1"] = 0.0 if reference_paths else None
    elif reference is not None and generated is not None:
        alignment = _align_case(case, reference, generated)
        metrics["node_recall"] = _ratio(
            len(alignment.generated_to_source), len(reference.elements)
        )
        if case.ground_truth.diagram_type == "flowchart":
            reference_edges = _directed_edge_counter(reference)
            generated_edges = _directed_edge_counter(
                generated, alignment.generated_to_source
            )
            overlap = sum((reference_edges & generated_edges).values())
            if reference_edges:
                metrics["edge_f1"] = _f1(
                    overlap,
                    generated_edges.total() - overlap,
                    reference_edges.total() - overlap,
                )
        else:
            edge = _edge_counts(
                reference,
                generated,
                shared_id_namespace=case.ground_truth.shared_id_namespace,
            )
            if _edge_counter(reference):
                metrics["edge_f1"] = _f1(*edge)
        reference_paths = _root_to_terminal_paths(reference)
        generated_paths = _root_to_terminal_paths(generated)
        if reference_paths and generated_paths is not None:
            mapped = Counter(
                tuple(
                    alignment.generated_to_source.get(node, f"generated:{node}")
                    for node in path
                )
                for path, count in generated_paths.items()
                for _ in range(count)
            )
            overlap = sum((reference_paths & mapped).values())
            metrics["path_f1"] = _f1(
                overlap,
                mapped.total() - overlap,
                reference_paths.total() - overlap,
            )
    return CaseEvaluation(
        case_id=case.definition.case_id,
        expected_type=case.ground_truth.diagram_type,
        predicted_type=case.prediction.diagram_type,
        metrics=metrics,
    )


def _is_positive_end_to_end(case: LoadedCase) -> bool:
    return case.definition.scope == "end_to_end" and case.ground_truth.expected_reconstruction


def _is_structural_positive(case: LoadedCase) -> bool:
    return _is_positive_end_to_end(case) and case.definition.fixture_group != "data_chart"


def _is_negative_detector(case: LoadedCase) -> bool:
    return case.definition.scope == "detector" and not case.ground_truth.expected_reconstruction


def _align_case(
    case: LoadedCase,
    reference: DiagramSceneIR,
    generated: DiagramSceneIR,
) -> SceneAlignment:
    return _align_elements(
        reference,
        generated,
        shared_id_namespace=case.ground_truth.shared_id_namespace,
    )


def _align_elements(
    reference: DiagramSceneIR,
    generated: DiagramSceneIR,
    *,
    shared_id_namespace: bool,
) -> SceneAlignment:
    reference_labels = _unique_scene_labels(reference)
    generated_labels = _unique_scene_labels(generated)
    mapping = {
        generated_labels[label]: reference_labels[label]
        for label in reference_labels.keys() & generated_labels.keys()
    }
    if shared_id_namespace:
        reference_by_id = {element.id: element for element in reference.elements}
        generated_by_id = {element.id: element for element in generated.elements}
        for element_id in reference_by_id.keys() & generated_by_id.keys():
            reference_label = _normalized_scene_label(reference_by_id[element_id].text)
            generated_label = _normalized_scene_label(generated_by_id[element_id].text)
            if reference_label == generated_label:
                mapping[element_id] = element_id
    reference_ids = {element.id for element in reference.elements}
    generated_ids = {element.id for element in generated.elements}
    return SceneAlignment(
        generated_to_source=mapping,
        unmatched_source_ids=tuple(sorted(reference_ids - set(mapping.values()))),
        unmatched_generated_ids=tuple(sorted(generated_ids - set(mapping))),
    )


def _unique_scene_labels(scene: DiagramSceneIR) -> dict[str, str]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for element in scene.elements:
        normalized = _normalized_scene_label(element.text)
        if normalized:
            grouped[normalized].append(element.id)
    return {label: ids[0] for label, ids in grouped.items() if len(ids) == 1}


def _normalized_scene_label(text: str | None) -> str:
    return " ".join(_token_list([text])) if text else ""


def _f1(true_positive: int, false_positive: int, false_negative: int) -> float:
    denominator = 2 * true_positive + false_positive + false_negative
    return 2 * true_positive / denominator if denominator else 0.0


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _threshold_gate(
    name: str,
    value: float,
    required: float,
    sample_count: int,
    *,
    maximum: bool = False,
) -> GateResult:
    if not math.isfinite(value):
        return _unavailable_gate(name, required)
    passed = value <= required if maximum else value >= required
    operator = "<=" if maximum else ">="
    return GateResult(
        name=name,
        status="pass" if passed else "fail",
        observed=value,
        required=required,
        sample_count=sample_count,
        detail=f"observed {value:.4f}; required {operator} {required:.4f}",
    )


def _all_true_gate(name: str, values: list[bool | None], required: str) -> GateResult:
    if not values:
        return _unavailable_gate(name, required)
    passed = sum(value is True for value in values)
    return GateResult(
        name=name,
        status="pass" if passed == len(values) else "fail",
        observed=passed / len(values),
        required=required,
        sample_count=len(values),
        detail=f"{passed}/{len(values)} passed",
    )


def _all_false_gate(
    name: str, values: list[bool], *, require_samples: bool = False
) -> GateResult:
    if require_samples and not values:
        return _unavailable_gate(name, 0)
    failures = sum(values)
    return GateResult(
        name=name,
        status="pass" if failures == 0 else "fail",
        observed=failures,
        required=0,
        sample_count=len(values),
        detail=f"{failures} violation(s)",
    )


def _sum_zero_gate(name: str, values: list[int]) -> GateResult:
    total = sum(values)
    return GateResult(
        name=name,
        status="pass" if total == 0 else "fail",
        observed=total,
        required=0,
        sample_count=len(values),
        detail=f"{total} violation(s)",
    )


def _unavailable_gate(name: str, required: float | int | str) -> GateResult:
    return GateResult(
        name=name,
        status="unavailable",
        required=required,
        detail="required evidence is unavailable",
    )


def write_evaluation_report(
    report: EvaluationReport,
    output_dir: str | Path,
    *,
    evaluation: LoadedEvaluation,
) -> Path:
    """Atomically replace an owned evaluation-report directory."""

    lexical_output = Path(output_dir).absolute()
    if lexical_output.is_symlink():
        raise ValueError("evaluation output cannot be a symlink")
    output = lexical_output.resolve()
    protected_root = evaluation.root.resolve()
    if output == protected_root or output in protected_root.parents:
        raise ValueError("evaluation output cannot replace the corpus root or its ancestor")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not output.is_dir():
        raise NotADirectoryError(f"evaluation output is not a directory: {output}")
    if output.exists():
        marker_path = output / OUTPUT_MARKER
        try:
            marker_payload, _, _ = _read_regular_file(
                marker_path,
                4096,
                "evaluation output ownership marker",
            )
            marker = json.loads(marker_payload)
        except (EvaluationManifestError, json.JSONDecodeError) as error:
            raise ValueError(
                "refusing to replace a directory not owned by marker-mermaid evaluation"
            ) from error
        if marker != {
            "kind": "marker-mermaid-evaluation-output",
            "schema_version": REPORT_SCHEMA_VERSION,
        }:
            raise ValueError("evaluation output ownership marker is invalid")
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        _write_json(
            temporary / OUTPUT_MARKER,
            {
                "kind": "marker-mermaid-evaluation-output",
                "schema_version": REPORT_SCHEMA_VERSION,
            },
        )
        _write_json(temporary / "evaluation-report.json", report.model_dump(mode="json"))
        (temporary / "evaluation-report.md").write_text(
            _report_markdown(report), encoding="utf-8"
        )
        _write_bytes(temporary / "manifest-snapshot.json", evaluation.manifest_payload)
        case_dir = temporary / "cases"
        case_dir.mkdir()
        for case in report.cases:
            _write_json(case_dir / f"{case.case_id}.json", case.model_dump(mode="json"))
        if output.exists():
            backup = Path(
                tempfile.mkdtemp(prefix=f".{output.name}-previous-", dir=output.parent)
            )
            backup.rmdir()
            os.replace(output, backup)
            try:
                os.replace(temporary, output)
            except Exception:
                os.replace(backup, output)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output / "evaluation-report.json"


def _write_json(path: Path, value: Any) -> None:
    _write_bytes(
        path,
        (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            + "\n"
        ).encode(),
    )


def _write_bytes(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)


def _report_markdown(report: EvaluationReport) -> str:
    lines = [
        f"# Evaluation report: {report.corpus.corpus_id}",
        "",
        f"Overall status: **{report.overall_status}**",
        "",
        f"Attestation: `{report.attestation}`",
        "",
        f"Manifest SHA-256: `{report.manifest_sha256}`",
        "",
        "| Gate | Status | Observed | Required | Samples | Unit | Raw counts |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for gate in report.gates:
        observed = "—" if gate.observed is None else str(gate.observed)
        counts = ", ".join(f"{name}={value}" for name, value in gate.counts.items()) or "—"
        lines.append(
            f"| `{gate.name}` | {gate.status} | {observed} | {gate.required} | "
            f"{gate.sample_count} | {gate.unit} | {counts} |"
        )
    lines.append("")
    return "\n".join(lines)
