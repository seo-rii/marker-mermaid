from __future__ import annotations

import json
from io import BytesIO
from itertools import islice as stdlib_islice

import pytest
from PIL import Image

import marker_mermaid.engines as engines_module
from marker_mermaid.config import ALL_TYPES, MIN_VLM_PROMPT_CHARS, PHASE_ONE_TYPES
from marker_mermaid.engines import (
    MAX_VLM_EVIDENCE_INPUT_CHARS,
    MAX_VLM_OCR_INPUT_CHARS,
    MAX_VLM_RESPONSE_SCHEMA_CHARS,
    SYSTEM_PROMPT,
    MarkerStructuredVLMEngine,
    StructuredVLMRequestError,
)
from marker_mermaid.models import (
    MAX_EVIDENCE_REFS,
    MAX_ID_CHARS,
    MAX_OBSERVATION_EVIDENCE,
    DiagramTypePrediction,
    EngineObservation,
    PromptBudgetNotice,
    VisualEvidence,
)
from marker_mermaid.protocols import SourceContext
from marker_mermaid.typed_contracts import (
    CORE_UML_NESTED_TYPES,
    NESTED_TYPED_IR_TYPES,
    PHASE_ONE_NESTED_TYPES,
    PHASE_THREE_CORE_NESTED_TYPES,
    PHASE_TWO_FALLBACK_NESTED_TYPES,
    PHASE_TWO_NATIVE_NESTED_TYPES,
    TYPED_IR_CONTRACTS,
    typed_ir_contract_prompt,
)


def test_system_prompt_requires_exact_scene_ids_and_prior_evidence_for_flow_nodes():
    prompt = " ".join(SYSTEM_PROMPT.split())

    assert "For flowchart and generic_network typed candidates" in prompt
    assert "exact IDs of matching scene_ir.elements from this same response" in prompt
    assert "do not rename, normalize, or invent node IDs" in prompt
    assert (
        "Every semantic typed node must include evidence_ids copied from supplied Prior evidence"
        in prompt
    )
    assert "reuse at least one of the same Prior evidence IDs" in prompt
    assert "Never self-declare or synthesize evidence IDs" in prompt


@pytest.mark.parametrize(
    "kwargs",
    [
        {"enabled_types": set()},
        {"enabled_types": {"not-a-type"}},
        {"max_prompt_chars": True},
        {"max_prompt_chars": MIN_VLM_PROMPT_CHARS - 1},
        {"max_evidence_items": 0},
        {"max_ocr_items": 4_097},
        {"max_views": 0},
        {"max_views": 17},
    ],
)
def test_marker_vlm_engine_rejects_invalid_prompt_budgets(kwargs):
    with pytest.raises(ValueError):
        MarkerStructuredVLMEngine(object(), **kwargs)


def test_marker_vlm_prompt_receives_prior_geometry_evidence():
    captured = {}

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        evidence=[
            VisualEvidence(
                id="geometry-contour-001",
                kind="contour",
                bbox=(1, 1, 10, 10),
                score=0.9,
            )
        ],
        ocr_texts=["Label"],
    )

    result = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart", "architecture"},
    ).observe(context)

    assert result.prediction.candidates == ["flowchart"]
    assert "geometry-contour-001" in captured["prompt"]
    assert "Label" in captured["prompt"]
    assert "- flowchart: nodes:list" in captured["prompt"]
    assert "- architecture: services:list" in captured["prompt"]
    assert (
        "  architecture.services[]: {id:string,label:string,name:string,icon:string,"
        "group:string,bbox:number[4],evidence_ids:string[]}"
    ) in captured["prompt"]
    assert "source_side:L|R|T|B,target_side:L|R|T|B" in captured["prompt"]
    assert (
        "  flowchart.nodes[]: {id:string,label:string,text:string,role:string,shape:string,"
        "bbox:number[4],evidence_ids:string[]}"
    ) in captured["prompt"]
    assert "sequence.participants[]" not in captured["prompt"]
    assert "- packet:" not in captured["prompt"]
    assert '"name": "original"' in captured["prompt"]
    assert '"width": 20' in captured["prompt"]
    assert "exact IDs of matching\nscene_ir.elements from this same response" in captured["prompt"]
    assert "evidence_ids copied from supplied Prior evidence" in captured["prompt"]
    assert len(captured["image"]) == 1
    assert type(captured["image"][0]) is Image.Image
    assert captured["image"][0] is not context.views["original"]
    assert captured["image"][0].mode == context.views["original"].mode
    assert captured["image"][0].size == context.views["original"].size
    assert captured["image"][0].tobytes() == context.views["original"].tobytes()
    assert result.prompt_supplied_prior_evidence_ids == {"geometry-contour-001"}
    assert result.prompt_budget_notice is not None
    assert result.prompt_budget_notice.evidence_included == 1
    assert (
        "prompt_supplied_prior_evidence_ids"
        not in EngineObservation.model_json_schema()["properties"]
    )
    assert "prompt_budget_notice" not in EngineObservation.model_json_schema()["properties"]


def test_marker_1102_response_schema_reserve_is_bounded_with_formatting_overhead():
    compact_schema_chars = len(
        json.dumps(
            EngineObservation.model_json_schema(),
            separators=(",", ":"),
        )
    )
    reserve_chars = MarkerStructuredVLMEngine(
        object(), enabled_types={"flowchart"}
    ).response_schema_chars_reserved

    assert compact_schema_chars < reserve_chars <= MAX_VLM_RESPONSE_SCHEMA_CHARS


@pytest.mark.parametrize(
    "value",
    ["plain", 'quote"slash\\', "\b\f\n\r\t", "\x00\x01\x1f", "한글 😀"],
)
def test_json_string_char_preflight_matches_compact_json(value):
    captured = {}

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    result = MarkerStructuredVLMEngine(service, enabled_types={"flowchart"}).observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=Image.new("RGB", (20, 20), "white"),
            views={"original": Image.new("RGB", (20, 20), "white")},
            ocr_texts=[value],
        )
    )

    assert json.dumps(value, ensure_ascii=False) in captured["prompt"]
    assert result.prompt_budget_notice.ocr_included == 1


def test_marker_vlm_adapter_overwrites_provider_private_authority_metadata():
    forged = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
    )
    forged._set_prompt_supplied_prior_evidence_ids({"forged"})

    result = MarkerStructuredVLMEngine(
        lambda **_kwargs: forged,
        enabled_types={"flowchart"},
    ).observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=Image.new("RGB", (20, 20), "white"),
            views={"original": Image.new("RGB", (20, 20), "white")},
            evidence=[VisualEvidence(id="actual", kind="contour", bbox=(1, 1, 2, 2))],
        )
    )

    assert result.prompt_supplied_prior_evidence_ids == {"actual"}
    assert result.prompt_budget_notice.selected_evidence_sha256


def test_marker_vlm_rejects_future_schema_above_reserve_limit(monkeypatch):
    monkeypatch.setattr(
        "marker_mermaid.engines.MAX_VLM_RESPONSE_SCHEMA_CHARS",
        1,
    )

    with pytest.raises(ValueError, match="response schema exceeds"):
        MarkerStructuredVLMEngine(object(), enabled_types={"flowchart"})


def test_marker_vlm_prompt_is_bounded_and_prioritizes_structural_evidence():
    prompts: list[str] = []

    def service(**kwargs):
        prompts.append(kwargs["prompt"])
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    evidence = [
        VisualEvidence(
            id=f"ocr-evidence-{index}",
            kind="ocr_token",
            text=f"Token {index}",
        )
        for index in range(300)
    ]
    evidence.extend(
        [
            VisualEvidence(id="critical-contour", kind="contour", bbox=(1, 1, 8, 8)),
            VisualEvidence(id="critical-arrow", kind="arrowhead", bbox=(8, 4, 9, 5)),
        ]
    )
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        evidence=evidence,
        ocr_texts=["x" * 20_000 for _ in range(20)],
    )
    evidence_before = [item.model_dump(mode="json") for item in context.evidence]
    ocr_before = list(context.ocr_texts)
    engine = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart"},
        max_prompt_chars=MIN_VLM_PROMPT_CHARS,
        max_evidence_items=2,
        max_ocr_items=3,
    )

    first = engine.observe(context)
    second = engine.observe(context)

    assert prompts[0] == prompts[1]
    assert len(prompts[0]) + engine.response_schema_chars_reserved <= MIN_VLM_PROMPT_CHARS
    assert "critical-arrow" in prompts[0]
    assert "critical-contour" in prompts[0]
    assert prompts[0].index("critical-arrow") < prompts[0].index("critical-contour")
    assert "ocr-evidence-0" not in prompts[0]
    assert '"evidence_included":2' in prompts[0]
    assert '"evidence_total":302' in prompts[0]
    assert '"ocr_included":0' in prompts[0]
    prior_json, ocr_json = (
        prompts[0]
        .rsplit("\nPrior evidence: ", 1)[1]
        .split(
            "\nOCR tokens: ",
            1,
        )
    )
    assert [item["id"] for item in json.loads(prior_json)] == [
        "critical-arrow",
        "critical-contour",
    ]
    assert json.loads(ocr_json) == []
    assert any("300 of 302 prior evidence" in warning for warning in first.warnings)
    assert any("20 of 20 OCR text" in warning for warning in first.warnings)
    assert first == second
    assert [item.model_dump(mode="json") for item in context.evidence] == evidence_before
    assert context.ocr_texts == ocr_before


def test_marker_vlm_prompt_prioritizes_trusted_label_provenance():
    captured = {}

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        evidence=[
            VisualEvidence(id="generic-arrow", kind="arrowhead", bbox=(1, 1, 2, 2)),
            VisualEvidence(id="trusted-label", kind="ocr_token", text="Approved"),
        ],
        trusted_label_evidence_ids={"trusted-label"},
    )

    result = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart"},
        max_evidence_items=2,
    ).observe(context)

    assert "trusted-label" in captured["prompt"]
    assert "generic-arrow" in captured["prompt"]
    assert captured["prompt"].index("generic-arrow") < captured["prompt"].index("trusted-label")
    assert not result.warnings


def test_marker_vlm_skips_single_oversized_ocr_before_json_encoding():
    captured = {}

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        ocr_texts=["x" * 2_000_000, "small-label"],
    )

    result = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart"},
        max_prompt_chars=MIN_VLM_PROMPT_CHARS,
        max_ocr_items=2,
    ).observe(context)

    assert (
        len(captured["prompt"]) + result.prompt_budget_notice.schema_reserve_chars
        <= MIN_VLM_PROMPT_CHARS
    )
    assert "small-label" in captured["prompt"]
    assert '"ocr_included":1' in captured["prompt"]
    assert any("1 of 2 OCR text" in warning for warning in result.warnings)


def test_marker_vlm_skips_oversized_ocr_before_escape_scan(monkeypatch):
    captured = {}
    oversized = "💣" * 2_000_000
    real_ord = ord

    def guarded_ord(value):
        if value == "💣":
            raise AssertionError("non-fitting OCR text reached the escape scanner")
        return real_ord(value)

    monkeypatch.setattr(engines_module, "ord", guarded_ord, raising=False)

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    result = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart"},
        max_prompt_chars=MIN_VLM_PROMPT_CHARS,
        max_ocr_items=2,
    ).observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=Image.new("RGB", (20, 20), "white"),
            views={"original": Image.new("RGB", (20, 20), "white")},
            ocr_texts=[oversized, "small-label"],
        )
    )

    assert "small-label" in captured["prompt"]
    assert result.prompt_budget_notice.ocr_included == 1


def test_marker_vlm_rejects_selected_ocr_aggregate_before_escape_scan(monkeypatch):
    called = False

    def service(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(
        engines_module,
        "ord",
        lambda _value: (_ for _ in ()).throw(
            AssertionError("over-budget OCR reached the escape scanner")
        ),
        raising=False,
    )
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        ocr_texts=["x" * (MAX_VLM_OCR_INPUT_CHARS // 2 + 1)] * 2,
    )

    with pytest.raises(RuntimeError, match="selected OCR context exceeds"):
        MarkerStructuredVLMEngine(
            service,
            enabled_types={"flowchart"},
            max_ocr_items=2,
        ).observe(context)

    assert not called


def test_marker_vlm_requires_exact_plain_selected_ocr_type():
    class TextSubclass(str):
        pass

    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        ocr_texts=[TextSubclass("label")],
    )

    with pytest.raises(RuntimeError, match="plain strings"):
        MarkerStructuredVLMEngine(object(), enabled_types={"flowchart"}).observe(context)


def test_marker_vlm_rejects_stateful_ocr_container_before_slicing_or_iteration():
    class StatefulOCRList(list):
        def __init__(self):
            super().__init__(["safe"])
            self.slice_count = 0
            self.iteration_count = 0

        def __getitem__(self, key):
            if isinstance(key, slice):
                self.slice_count += 1
            return super().__getitem__(key)

        def __iter__(self):
            self.iteration_count += 1
            yield from super().__iter__()

    ocr_texts = StatefulOCRList()
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        ocr_texts=ocr_texts,
    )

    with pytest.raises(RuntimeError, match="exact plain list"):
        MarkerStructuredVLMEngine(object(), enabled_types={"flowchart"}).observe(context)

    assert ocr_texts.slice_count == 0
    assert ocr_texts.iteration_count == 0


def test_marker_vlm_rejects_escape_amplified_items_before_json_encoding(monkeypatch):
    captured = {}
    control_heavy = "\x00" * 3_000
    real_dumps = json.dumps

    def guarded_dumps(value, *args, **kwargs):
        if value == control_heavy or (
            isinstance(value, dict) and value.get("text") == control_heavy
        ):
            raise AssertionError("non-fitting control-heavy item was serialized")
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr("marker_mermaid.engines.json.dumps", guarded_dumps)

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    evidence = [
        VisualEvidence(id="huge-control", kind="arrowhead", text=control_heavy),
        VisualEvidence(id="small-contour", kind="contour", bbox=(1, 1, 2, 2)),
        VisualEvidence(id="small-label", kind="ocr_token", text="Approved"),
    ]
    result = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart"},
        max_prompt_chars=MIN_VLM_PROMPT_CHARS,
        max_evidence_items=2,
        max_ocr_items=2,
    ).observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=Image.new("RGB", (20, 20), "white"),
            views={"original": Image.new("RGB", (20, 20), "white")},
            evidence=evidence,
            ocr_texts=[control_heavy, "small-ocr"],
        )
    )

    assert "small-contour" in captured["prompt"]
    assert "small-label" in captured["prompt"]
    assert "small-ocr" in captured["prompt"]
    assert "huge-control" not in captured["prompt"]
    assert "evidence_char_limit" in result.prompt_budget_notice.omission_reasons
    assert "ocr_char_limit" in result.prompt_budget_notice.omission_reasons


def test_marker_vlm_fixed_prompt_overhead_fails_before_provider_call():
    called = False

    def service(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
    )
    engine = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart"},
        max_prompt_chars=MIN_VLM_PROMPT_CHARS,
    )
    engine.response_schema_chars_reserved = MIN_VLM_PROMPT_CHARS

    try:
        engine.observe(context)
    except RuntimeError as exc:
        assert "fixed content exceeds" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("oversized fixed prompt content was accepted")
    assert not called


@pytest.mark.parametrize(
    ("field", "value"),
    [("text", "\ud800"), ("score", float("nan"))],
)
def test_marker_vlm_revalidates_mutated_evidence_before_provider(field, value):
    called = False

    def service(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    item = VisualEvidence(id="mutable", kind="ocr_token", text="safe", score=0.5)
    setattr(item, field, value)
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        evidence=[item],
    )

    with pytest.raises(RuntimeError, match="canonical structure preflight"):
        MarkerStructuredVLMEngine(service, enabled_types={"flowchart"}).observe(context)
    assert not called


def test_marker_vlm_rejects_aggregate_evidence_chars_before_copy_or_provider():
    called = False

    def service(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    max_text = "x" * 50_000
    evidence = [
        VisualEvidence(id=f"item-{index}", kind="ocr_token", text=max_text)
        for index in range(MAX_VLM_EVIDENCE_INPUT_CHARS // len(max_text) + 1)
    ]
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        evidence=evidence,
    )

    with pytest.raises(RuntimeError, match="aggregate input character budget"):
        MarkerStructuredVLMEngine(service, enabled_types={"flowchart"}).observe(context)

    assert not called


def test_marker_vlm_requires_exact_canonical_evidence_type():
    class EvidenceSubclass(VisualEvidence):
        pass

    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        evidence=[EvidenceSubclass(id="subclass", kind="contour")],
    )

    with pytest.raises(RuntimeError, match="canonical VisualEvidence"):
        MarkerStructuredVLMEngine(object(), enabled_types={"flowchart"}).observe(context)


def test_marker_vlm_rejects_stateful_evidence_container_before_snapshot_iteration():
    class StatefulEvidenceList(list):
        def __init__(self):
            super().__init__([VisualEvidence(id="safe", kind="contour")])
            self.slice_count = 0
            self.iteration_count = 0

        def __getitem__(self, key):
            if isinstance(key, slice):
                self.slice_count += 1
            return super().__getitem__(key)

        def __iter__(self):
            self.iteration_count += 1
            yield from super().__iter__()

    evidence = StatefulEvidenceList()
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        evidence=evidence,
    )

    with pytest.raises(RuntimeError, match="exact plain list"):
        MarkerStructuredVLMEngine(object(), enabled_types={"flowchart"}).observe(context)

    assert evidence.slice_count == 0
    assert evidence.iteration_count == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_block_ids", [""] * (MAX_EVIDENCE_REFS + 1)),
        ("bbox", (False, 0, 1, 1)),
        ("bbox", (0, 0, 1, 1, 2)),
        ("bbox", (0, 0, 1, float("inf"))),
    ],
)
def test_marker_vlm_rejects_mutated_nested_evidence_before_canonical_copy(field, value):
    called = False

    def service(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    item = VisualEvidence(id="mutable", kind="contour", bbox=(0, 0, 1, 1))
    setattr(item, field, value)
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        evidence=[item],
    )

    with pytest.raises(RuntimeError, match="canonical structure preflight"):
        MarkerStructuredVLMEngine(service, enabled_types={"flowchart"}).observe(context)

    assert not called


@pytest.mark.parametrize(
    "service",
    [
        lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("provider timeout")),
        lambda **_kwargs: {},
    ],
)
def test_marker_vlm_request_failures_carry_canonical_prompt_notice(service):
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        evidence=[VisualEvidence(id="kept", kind="contour", bbox=(1, 1, 2, 2))],
    )
    engine = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart"},
        max_evidence_items=3,
        max_ocr_items=4,
    )

    with pytest.raises(StructuredVLMRequestError) as captured:
        engine.observe(context)

    notice = captured.value.prompt_budget_notice
    assert type(notice) is PromptBudgetNotice
    assert notice.max_evidence_items == 3
    assert notice.max_ocr_items == 4
    assert notice.evidence_total == notice.evidence_considered == notice.evidence_included == 1
    assert notice.prompt_chars + notice.schema_reserve_chars <= notice.max_prompt_chars
    assert captured.value.__cause__ is not None


@pytest.mark.parametrize(
    "views",
    [
        {},
        {"edge_map": Image.new("RGB", (20, 20), "white")},
        {"original": object()},
        {"original": Image.new("L", (20, 20), "white")},
        {
            "original": Image.new("RGB", (20, 20), "white"),
            **{f"view_{index}": Image.new("RGB", (1, 1), "white") for index in range(8)},
        },
    ],
)
def test_marker_vlm_rejects_invalid_or_excess_views_before_provider(views):
    called = False

    def service(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views=views,
    )

    with pytest.raises(RuntimeError, match="Structured VLM view"):
        MarkerStructuredVLMEngine(
            service,
            enabled_types={"flowchart"},
            max_views=8,
        ).observe(context)
    assert not called


def test_marker_vlm_rejects_non_string_first_view_name_without_equality_hook():
    class StatefulViewName:
        def __init__(self):
            self.equality_calls = 0

        def __eq__(self, _other):
            self.equality_calls += 1
            return False

        __hash__ = object.__hash__

    first_name = StatefulViewName()
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (1, 1), "white"),
        views={first_name: Image.new("RGB", (1, 1), "white")},
    )

    with pytest.raises(RuntimeError, match="start with the original image"):
        MarkerStructuredVLMEngine(object(), enabled_types={"flowchart"}).observe(context)

    assert first_name.equality_calls == 0


def test_marker_vlm_materializes_only_max_views_plus_one(monkeypatch):
    stops = []

    def bounded_islice(iterable, stop):
        stops.append(stop)
        return stdlib_islice(iterable, stop)

    monkeypatch.setattr("marker_mermaid.engines.islice", bounded_islice)
    views = {
        "original": Image.new("RGB", (1, 1), "white"),
        **{f"view_{index}": Image.new("RGB", (1, 1), "white") for index in range(100)},
    }
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (1, 1), "white"),
        views=views,
    )

    with pytest.raises(RuntimeError, match="between 1 and 3 images"):
        MarkerStructuredVLMEngine(
            object(),
            enabled_types={"flowchart"},
            max_views=3,
        ).observe(context)

    assert stops == [4]


@pytest.mark.parametrize(
    "field",
    ["trusted_connector_evidence_ids", "trusted_label_evidence_ids"],
)
def test_marker_vlm_rejects_stateful_trusted_evidence_set_before_membership(field):
    class StatefulEvidenceSet(set):
        def __init__(self):
            super().__init__({"evidence-1"})
            self.contains_count = 0

        def __contains__(self, value):
            self.contains_count += 1
            return super().__contains__(value)

    trusted_ids = StatefulEvidenceSet()
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (1, 1), "white"),
        views={"original": Image.new("RGB", (1, 1), "white")},
        evidence=[VisualEvidence(id="evidence-1", kind="contour")],
    )
    setattr(context, field, trusted_ids)

    with pytest.raises(RuntimeError, match="must be an exact set"):
        MarkerStructuredVLMEngine(object(), enabled_types={"flowchart"}).observe(context)

    assert trusted_ids.contains_count == 0


@pytest.mark.parametrize("invalid_id", ["", "x" * (MAX_ID_CHARS + 1)])
def test_marker_vlm_rejects_invalid_trusted_evidence_ids(invalid_id):
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (1, 1), "white"),
        views={"original": Image.new("RGB", (1, 1), "white")},
        trusted_label_evidence_ids={invalid_id},
    )

    with pytest.raises(RuntimeError, match="contains an invalid identifier"):
        MarkerStructuredVLMEngine(object(), enabled_types={"flowchart"}).observe(context)


def test_marker_vlm_rejects_oversized_trusted_evidence_id_set():
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (1, 1), "white"),
        views={"original": Image.new("RGB", (1, 1), "white")},
        trusted_connector_evidence_ids={
            f"evidence-{index}" for index in range(MAX_OBSERVATION_EVIDENCE + 1)
        },
    )

    with pytest.raises(RuntimeError, match="exceeds the evidence item limit"):
        MarkerStructuredVLMEngine(object(), enabled_types={"flowchart"}).observe(context)


def test_marker_vlm_rejects_oversized_views_before_loading_them():
    class ProbeImage(Image.Image):
        def __init__(self, size):
            super().__init__()
            self._mode = "RGB"
            self._size = size
            self.load_count = 0

        def load(self):
            self.load_count += 1
            return None

    oversized = ProbeImage((4_097, 1))
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=oversized,
        views={"original": oversized},
    )

    with pytest.raises(RuntimeError, match="size boundary"):
        MarkerStructuredVLMEngine(object(), enabled_types={"flowchart"}).observe(context)

    assert oversized.load_count == 0


def test_marker_vlm_rejects_aggregate_overflow_before_loading_overflow_view():
    class ProbeImage(Image.Image):
        def __init__(self, source):
            super().__init__()
            self.im = source.im
            self._mode = source.mode
            self._size = source.size
            self.load_count = 0

        def load(self):
            self.load_count += 1
            raise AssertionError("caller-owned load hook must not run")

    source = Image.new("RGB", (4_096, 2_731), "white")
    views = [ProbeImage(source) for _ in range(3)]
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=views[0],
        views={"original": views[0], "edge_map": views[1], "arrow_overlay": views[2]},
    )

    with pytest.raises(RuntimeError, match="aggregate pixel boundary"):
        MarkerStructuredVLMEngine(object(), enabled_types={"flowchart"}).observe(context)

    assert [view.load_count for view in views] == [0, 0, 0]


def test_marker_vlm_provider_receives_only_bounded_plain_view_snapshots():
    class StatefulImage(Image.Image):
        def __init__(self):
            super().__init__()
            self._size_reads = 0
            self._mode_reads = 0
            self._mode = "RGB"
            self._size = (1, 1)
            self.load_count = 0
            self.copy_count = 0

        @property
        def size(self):
            self._size_reads += 1
            if self._size_reads <= 2:
                return (1, 1)
            return (100_000, 100_000)

        @property
        def mode(self):
            self._mode_reads += 1
            return "RGB"

        def load(self):
            self.load_count += 1
            raise AssertionError("caller-owned load hook must not run")

        def copy(self):
            self.copy_count += 1
            return self

    called = False

    def service(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not receive the mutable view")

    hostile = StatefulImage()
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (1, 1), "white"),
        views={"original": hostile},
    )

    with pytest.raises(RuntimeError, match="canonical RGB pixel core"):
        MarkerStructuredVLMEngine(service, enabled_types={"flowchart"}).observe(context)

    assert not called
    assert hostile._size_reads == 0
    assert hostile._mode_reads == 0
    assert hostile.load_count == 0
    assert hostile.copy_count == 0


def test_marker_vlm_snapshots_legitimate_pillow_imagefile_subclasses():
    encoded = BytesIO()
    Image.new("RGB", (3, 2), "purple").save(encoded, format="PNG")
    encoded.seek(0)
    source_view = Image.open(encoded)
    source_view.load()
    source_pixels = source_view.tobytes()
    captured = {}

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    MarkerStructuredVLMEngine(service, enabled_types={"flowchart"}).observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=Image.new("RGB", (3, 2), "purple"),
            views={"original": source_view},
        )
    )

    assert type(source_view) is not Image.Image
    assert type(captured["image"][0]) is Image.Image
    assert captured["image"][0].size == (3, 2)
    assert captured["image"][0].tobytes() == source_pixels


def test_marker_vlm_rejects_unloaded_pillow_imagefile_without_calling_loader():
    encoded = BytesIO()
    Image.new("RGB", (3, 2), "purple").save(encoded, format="PNG")
    encoded.seek(0)
    source_view = Image.open(encoded)
    called = False

    def service(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    with pytest.raises(RuntimeError, match="canonical RGB pixel core"):
        MarkerStructuredVLMEngine(service, enabled_types={"flowchart"}).observe(
            SourceContext(
                source_id="figure-1",
                source_block_ids=["/page/0/Figure/1"],
                source_image_name="figure.png",
                image=Image.new("RGB", (3, 2), "purple"),
                views={"original": source_view},
            )
        )

    assert not called
    assert source_view.im is None


def test_marker_vlm_ignores_image_subclass_shared_copy_override():
    class SharedSnapshotImage(Image.Image):
        def __init__(self):
            super().__init__()
            source = Image.new("RGB", (1, 1), "purple")
            self.im = type(source.im).copy(source.im)
            self._mode = source.mode
            self._size = source.size
            self.shared = Image.new("RGB", (1, 1), "white")
            self.load_count = 0
            self.copy_count = 0

        def load(self):
            self.load_count += 1
            raise AssertionError("caller-owned class load hook must not run")

        def copy(self):
            self.copy_count += 1
            return self.shared

    hostile = SharedSnapshotImage()
    captured = {}

    def service(**kwargs):
        hostile.shared._size = (100_000, 100_000)
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    MarkerStructuredVLMEngine(service, enabled_types={"flowchart"}).observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=Image.new("RGB", (1, 1), "white"),
            views={"original": hostile},
        )
    )

    assert captured["image"][0] is not hostile.shared
    assert type(captured["image"][0]) is Image.Image
    assert captured["image"][0].size == (1, 1)
    assert captured["image"][0].getpixel((0, 0)) == (128, 0, 128)
    assert hostile.load_count == 0
    assert hostile.copy_count == 0


def test_marker_vlm_ignores_exact_image_instance_copy_override():
    source = Image.new("RGB", (1, 1), "white")
    hook_calls = {"copy": 0, "load": 0}

    def shared_copy():
        hook_calls["copy"] += 1
        return source

    def hostile_load():
        hook_calls["load"] += 1
        raise AssertionError("instance load override must not run")

    source.copy = shared_copy
    source.load = hostile_load
    captured = {}

    def service(**kwargs):
        source._size = (100_000, 100_000)
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    MarkerStructuredVLMEngine(service, enabled_types={"flowchart"}).observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=Image.new("RGB", (1, 1), "white"),
            views={"original": source},
        )
    )

    [snapshot] = captured["image"]
    assert snapshot is not source
    assert type(snapshot) is Image.Image
    assert snapshot.size == (1, 1)
    assert snapshot.getpixel((0, 0)) == (255, 255, 255)
    assert hook_calls == {"copy": 0, "load": 0}


def test_marker_vlm_structural_quota_survives_trusted_ocr_saturation():
    captured = {}

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    evidence = [
        VisualEvidence(id=f"trusted-{index}", kind="ocr_token", text=f"Label {index}")
        for index in range(256)
    ]
    evidence.extend(
        [
            VisualEvidence(id="late-contour", kind="contour", bbox=(1, 1, 4, 4)),
            VisualEvidence(id="late-vector", kind="vector_text", text="Vector"),
        ]
    )
    result = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart"},
        max_evidence_items=8,
    ).observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=Image.new("RGB", (20, 20), "white"),
            views={"original": Image.new("RGB", (20, 20), "white")},
            evidence=evidence,
            trusted_label_evidence_ids={item.id for item in evidence[:256]},
        )
    )

    assert "late-contour" in captured["prompt"]
    assert "late-vector" in captured["prompt"]
    assert result.prompt_budget_notice.evidence_included == 8
    assert result.prompt_budget_notice.omission_reasons == ["evidence_item_limit"]


def test_marker_vlm_oversized_evidence_is_skipped_and_slots_are_backfilled():
    captured = {}

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    evidence = [
        VisualEvidence(id="huge-edit", kind="user_edit", text="x" * 50_000),
        VisualEvidence(id="small-arrow", kind="arrowhead", bbox=(1, 1, 2, 2)),
        VisualEvidence(id="small-label", kind="ocr_token", text="Approved"),
    ]
    result = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart"},
        max_prompt_chars=MIN_VLM_PROMPT_CHARS,
        max_evidence_items=2,
    ).observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=Image.new("RGB", (20, 20), "white"),
            views={"original": Image.new("RGB", (20, 20), "white")},
            evidence=evidence,
            trusted_label_evidence_ids={"small-label"},
        )
    )

    assert "huge-edit" not in captured["prompt"]
    assert "small-arrow" in captured["prompt"]
    assert "small-label" in captured["prompt"]
    assert result.prompt_budget_notice.evidence_considered == 3
    assert result.prompt_budget_notice.evidence_included == 2
    assert "evidence_char_limit" in result.prompt_budget_notice.omission_reasons


def test_marker_vlm_oversized_structural_records_backfill_later_structure():
    captured = {}

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    evidence = [
        VisualEvidence(id="huge-arrow", kind="arrowhead", text="x" * 50_000),
        VisualEvidence(id="huge-line", kind="line_segment", text="x" * 50_000),
        *[
            VisualEvidence(id=f"trusted-{index}", kind="ocr_token", text=f"Label {index}")
            for index in range(20)
        ],
        VisualEvidence(id="late-contour", kind="contour", bbox=(1, 1, 4, 4)),
        VisualEvidence(id="late-vector", kind="vector_text", text="Vector"),
    ]
    result = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart"},
        max_prompt_chars=MIN_VLM_PROMPT_CHARS,
        max_evidence_items=8,
    ).observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=Image.new("RGB", (20, 20), "white"),
            views={"original": Image.new("RGB", (20, 20), "white")},
            evidence=evidence,
            trusted_label_evidence_ids={item.id for item in evidence[2:22]},
        )
    )

    prior_json = captured["prompt"].rsplit("\nPrior evidence: ", 1)[1].split("\nOCR tokens: ", 1)[0]
    included_ids = {item["id"] for item in json.loads(prior_json)}
    assert {"late-contour", "late-vector"}.issubset(included_ids)
    assert {"huge-arrow", "huge-line"}.isdisjoint(included_ids)
    assert result.prompt_budget_notice.evidence_included == 8
    assert "evidence_char_limit" in result.prompt_budget_notice.omission_reasons


def test_nested_contract_prompt_is_deterministic_and_enabled_type_only():
    first = typed_ir_contract_prompt({"timeline", "flowchart"})
    second = typed_ir_contract_prompt({"flowchart", "timeline"})

    assert first == second
    assert (
        "  flowchart.edges[]: {id:string,source:string,target:string,label:string,"
        "relation_type:string,semantic_relation:string,style:string,"
        "bidirectional:boolean,evidence_ids:string[]}"
    ) in first
    assert (
        "  timeline.events[]: {id:string,time:string,period:string,label:string,"
        "events:string[],bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert "architecture.services[]" not in first
    assert "packet.fields[]" not in first
    assert first.index("- flowchart:") < first.index("- timeline:")


def test_every_phase_one_type_and_alias_has_nested_prompt_records():
    expected_records = {
        "architecture": ("services[]", "groups[]", "edges[]"),
        "bpmn": ("lanes[]", "lanes[].nodes[]", "edges[]"),
        "flowchart": ("nodes[]", "edges[]", "groups[]"),
        "gantt": ("sections[]", "sections[].tasks[]"),
        "generic_network": ("nodes[]", "edges[]", "groups[]"),
        "mindmap": ("root",),
        "sequence": ("participants[]", "messages[]"),
        "swimlane": ("lanes[]", "lanes[].nodes[]", "edges[]"),
        "timeline": ("events[]",),
    }

    expected_phase_one_types = PHASE_ONE_TYPES | {"generic_network"}
    assert expected_phase_one_types == PHASE_ONE_NESTED_TYPES
    assert set(expected_records) == PHASE_ONE_NESTED_TYPES
    for diagram_type, prefixes in expected_records.items():
        contract = TYPED_IR_CONTRACTS[diagram_type]
        prompt = typed_ir_contract_prompt({diagram_type})
        assert contract.nested_model is not None
        assert len(contract.prompt_records) == len(prefixes)
        assert tuple(record.split(":", 1)[0] for record in contract.prompt_records) == prefixes
        assert all(f"  {diagram_type}.{record}" in prompt for record in contract.prompt_records)
        assert all(
            f"  {other_type}." not in prompt
            for other_type in PHASE_ONE_NESTED_TYPES - {diagram_type}
        )


def test_core_uml_nested_contract_prompt_is_deterministic_and_enabled_type_only():
    first = typed_ir_contract_prompt({"state", "class", "er"})
    second = typed_ir_contract_prompt({"er", "state", "class"})

    assert first == second
    assert (
        "  state.states[]: {id:string,label:string,kind:state|choice|fork|join,"
        "bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert (
        "  class.classes[].members[]: {name:string,type:string,visibility:+|-|#|~,"
        "kind:field|method,parameters:string[],return_type:string,"
        "classifier:static|abstract,bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert (
        "  er.relationships[]: {id:string,source:string,target:string,"
        "source_cardinality:one|only_one|zero_or_one|one_or_more|zero_or_more,"
        "target_cardinality:one|only_one|zero_or_one|one_or_more|zero_or_more,"
        "identifying:boolean,label:string,bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert "flowchart.nodes[]" not in first
    assert "architecture.services[]" not in first
    assert first.index("- class:") < first.index("- er:") < first.index("- state:")


def test_every_core_uml_type_has_nested_prompt_records():
    expected_records = {
        "class": ("classes[]", "classes[].members[]", "relations[]"),
        "er": ("entities[]", "entities[].attributes[]", "relationships[]"),
        "state": ("states[]", "transitions[]"),
    }

    assert set(expected_records) == CORE_UML_NESTED_TYPES
    assert NESTED_TYPED_IR_TYPES == (
        PHASE_ONE_NESTED_TYPES
        | CORE_UML_NESTED_TYPES
        | PHASE_TWO_NATIVE_NESTED_TYPES
        | PHASE_TWO_FALLBACK_NESTED_TYPES
        | PHASE_THREE_CORE_NESTED_TYPES
    )
    assert {
        diagram_type
        for diagram_type, contract in TYPED_IR_CONTRACTS.items()
        if contract.nested_model is not None
    } == NESTED_TYPED_IR_TYPES
    for diagram_type, prefixes in expected_records.items():
        contract = TYPED_IR_CONTRACTS[diagram_type]
        prompt = typed_ir_contract_prompt({diagram_type})
        assert contract.nested_model is not None
        assert tuple(record.split(":", 1)[0] for record in contract.prompt_records) == prefixes
        assert all(f"  {diagram_type}.{record}" in prompt for record in contract.prompt_records)
        assert all(
            f"  {other_type}." not in prompt
            for other_type in CORE_UML_NESTED_TYPES - {diagram_type}
        )


def test_phase_two_native_nested_prompt_is_deterministic_and_enabled_type_only():
    first = typed_ir_contract_prompt({"requirement", "block"})
    second = typed_ir_contract_prompt({"block", "requirement"})

    assert first == second
    assert "  block.columns: auto|integer" in first
    assert (
        "  block.blocks[]: {id:string,label:string,text:string,"
        "shape:rectangle|round|stadium|circle|diamond|hexagon|cylinder|subroutine,"
        "bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert (
        "  block.edges[]: {id:string,source:string,target:string,label:string,style:string,"
        "bidirectional:boolean,bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert (
        "  requirement.requirements[]: {id:string,requirement_id:string,text:string,"
        "label:string,type:requirement|functional|functional_requirement|interface|"
        "interface_requirement|performance|performance_requirement|physical|"
        "physical_requirement|design_constraint,risk:low|medium|high,"
        "verify_method:analysis|demonstration|inspection|test,bbox:number[4],"
        "evidence_ids:string[]}"
    ) in first
    assert (
        "  requirement.elements[]: {id:string,type:string,label:string,docref:string,"
        "bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert (
        "  requirement.relations[]: {id:string,source:string,target:string,"
        "type:contains|copies|derives|satisfies|verifies|refines|traces,"
        "bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert "flowchart.nodes[]" not in first
    assert "class.classes[]" not in first
    assert first.index("- block:") < first.index("- requirement:")


def test_every_phase_two_native_type_has_exact_nested_prompt_records():
    expected_records = {
        "block": ("columns", "blocks[]", "edges[]"),
        "requirement": ("requirements[]", "elements[]", "relations[]"),
    }

    assert set(expected_records) == PHASE_TWO_NATIVE_NESTED_TYPES
    assert NESTED_TYPED_IR_TYPES == (
        PHASE_ONE_NESTED_TYPES
        | CORE_UML_NESTED_TYPES
        | PHASE_TWO_NATIVE_NESTED_TYPES
        | PHASE_TWO_FALLBACK_NESTED_TYPES
        | PHASE_THREE_CORE_NESTED_TYPES
    )
    assert {
        diagram_type
        for diagram_type, contract in TYPED_IR_CONTRACTS.items()
        if contract.nested_model is not None
    } == NESTED_TYPED_IR_TYPES
    for diagram_type, prefixes in expected_records.items():
        contract = TYPED_IR_CONTRACTS[diagram_type]
        prompt = typed_ir_contract_prompt({diagram_type})
        assert contract.nested_model is not None
        assert tuple(record.split(":", 1)[0] for record in contract.prompt_records) == prefixes
        assert all(f"  {diagram_type}.{record}" in prompt for record in contract.prompt_records)
        assert all(
            f"  {other_type}." not in prompt
            for other_type in PHASE_TWO_NATIVE_NESTED_TYPES - {diagram_type}
        )


def test_c4_fallback_nested_prompt_is_exact_deterministic_and_enabled_type_only():
    first = typed_ir_contract_prompt({"c4"})
    second = typed_ir_contract_prompt({"c4"})

    assert first == second
    assert "  c4.level: context|container|component" in first
    assert (
        "  c4.elements[]: {id:string,label:string,name:string,kind:person|external_person|"
        "system|external_system|database|external_database|queue|external_queue|"
        "container|container_database|container_queue|component|component_database|"
        "component_queue,boundary:string,description:string,technology:string,"
        "bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert (
        "  c4.boundaries[]: {id:string,label:string,type:string,bbox:number[4],"
        "evidence_ids:string[]}"
    ) in first
    assert (
        "  c4.relations[]: {id:string,source:string,target:string,label:string,"
        "technology:string,bidirectional:boolean,source_side:L|R|T|B,"
        "target_side:L|R|T|B,bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert "c4.elements[]:" in first
    assert "type:person" not in first
    assert "architecture.services[]" not in first
    assert "requirement.requirements[]" not in first


def test_architecture_fallback_nested_prompts_are_exact_and_enabled_type_only():
    deployment = typed_ir_contract_prompt({"deployment"})
    component = typed_ir_contract_prompt({"component"})

    assert (
        "  deployment.nodes[]: {id:string,label:string,name:string,icon:string,group:string,"
        "bbox:number[4],evidence_ids:string[]}"
    ) in deployment
    assert (
        "  deployment.artifacts[]: {id:string,label:string,name:string,icon:string,"
        "group:string,bbox:number[4],evidence_ids:string[]}"
    ) in deployment
    assert (
        "  deployment.groups[]: {id:string,label:string,icon:string,bbox:number[4],"
        "evidence_ids:string[]}"
    ) in deployment
    assert (
        "  deployment.links[]: {id:string,source:string,target:string,label:string,"
        "bidirectional:boolean,source_side:L|R|T|B,target_side:L|R|T|B,"
        "bbox:number[4],evidence_ids:string[]}"
    ) in deployment
    assert "  deployment.edges[]" not in deployment
    assert "  component.components[]" not in deployment

    assert (
        "  component.components[]: {id:string,label:string,name:string,icon:string,"
        "group:string,bbox:number[4],evidence_ids:string[]}"
    ) in component
    assert (
        "  component.interfaces[]: {id:string,label:string,name:string,icon:string,"
        "group:string,bbox:number[4],evidence_ids:string[]}"
    ) in component
    assert (
        "  component.groups[]: {id:string,label:string,icon:string,bbox:number[4],"
        "evidence_ids:string[]}"
    ) in component
    assert (
        "  component.dependencies[]: {id:string,source:string,target:string,label:string,"
        "bidirectional:boolean,source_side:L|R|T|B,target_side:L|R|T|B,"
        "bbox:number[4],evidence_ids:string[]}"
    ) in component
    assert "  component.edges[]" not in component
    assert "  deployment.nodes[]" not in component


def test_usecase_fallback_nested_prompt_is_exact_and_enabled_type_only() -> None:
    first = typed_ir_contract_prompt({"usecase"})
    second = typed_ir_contract_prompt({"usecase"})

    assert first == second
    assert (
        "  usecase.actors[]: {id:string,label:string,name:string,bbox:number[4],"
        "evidence_ids:string[]}"
    ) in first
    assert (
        "  usecase.use_cases[]: {id:string,label:string,name:string,bbox:number[4],"
        "evidence_ids:string[]}"
    ) in first
    assert (
        "  usecase.relations[]: {id:string,source:string,target:string,type:string,"
        "label:string,bbox:number[4],evidence_ids:string[]}"
    ) in first
    assert "usecase.groups[]" not in first
    assert "system_boundary" not in first
    assert "usecase.relations[]" in first
    assert "deployment.nodes[]" not in first


def test_every_phase_two_fallback_type_has_exact_nested_prompt_records():
    expected_records = {
        "c4": ("level", "elements[]", "boundaries[]", "relations[]"),
        "component": ("components[]", "interfaces[]", "groups[]", "dependencies[]"),
        "deployment": ("nodes[]", "artifacts[]", "groups[]", "links[]"),
        "usecase": ("actors[]", "use_cases[]", "relations[]"),
    }

    assert set(expected_records) == PHASE_TWO_FALLBACK_NESTED_TYPES
    assert NESTED_TYPED_IR_TYPES == (
        PHASE_ONE_NESTED_TYPES
        | CORE_UML_NESTED_TYPES
        | PHASE_TWO_NATIVE_NESTED_TYPES
        | PHASE_TWO_FALLBACK_NESTED_TYPES
        | PHASE_THREE_CORE_NESTED_TYPES
    )
    assert {
        diagram_type
        for diagram_type, registered in TYPED_IR_CONTRACTS.items()
        if registered.nested_model is not None
    } == NESTED_TYPED_IR_TYPES
    for diagram_type, prefixes in expected_records.items():
        contract = TYPED_IR_CONTRACTS[diagram_type]
        prompt = typed_ir_contract_prompt({diagram_type})
        assert contract.nested_model is not None
        assert tuple(record.split(":", 1)[0] for record in contract.prompt_records) == prefixes
        assert all(f"  {diagram_type}.{record}" in prompt for record in contract.prompt_records)
        assert all(
            f"  {other_type}." not in prompt
            for other_type in PHASE_TWO_FALLBACK_NESTED_TYPES - {diagram_type}
        )


def test_phase_three_core_chart_nested_prompts_are_exact_and_enabled_type_only() -> None:
    expected_records = {
        "pie": (
            "show_data: boolean",
            "slices[]: {label:string,value:number,bbox:number[4],evidence_ids:string[]}",
        ),
        "quadrant": (
            "x_axis: {low:string,high:string,bbox:number[4],evidence_ids:string[]}",
            "y_axis: {low:string,high:string,bbox:number[4],evidence_ids:string[]}",
            "quadrants: string[4]|{quadrant-1:string,quadrant-2:string,"
            "quadrant-3:string,quadrant-4:string}",
            "points[]: {label:string,x:number,y:number,bbox:number[4],evidence_ids:string[]}",
        ),
        "xychart": (
            "x_axis: {label:string,categories:string[],min:number,max:number,"
            "bbox:number[4],evidence_ids:string[]}",
            "y_axis: {label:string,min:number,max:number,bbox:number[4],evidence_ids:string[]}",
            "series[]: {kind:line|bar,values:number[],points:point[],bbox:number[4],"
            "evidence_ids:string[]}",
            "series[].points[]: {x:number,y:number,bbox:number[4],evidence_ids:string[]}",
        ),
    }

    assert set(expected_records) == PHASE_THREE_CORE_NESTED_TYPES
    assert NESTED_TYPED_IR_TYPES == (
        PHASE_ONE_NESTED_TYPES
        | CORE_UML_NESTED_TYPES
        | PHASE_TWO_NATIVE_NESTED_TYPES
        | PHASE_TWO_FALLBACK_NESTED_TYPES
        | PHASE_THREE_CORE_NESTED_TYPES
    )
    assert {
        diagram_type
        for diagram_type, contract in TYPED_IR_CONTRACTS.items()
        if contract.nested_model is not None
    } == NESTED_TYPED_IR_TYPES

    combined = typed_ir_contract_prompt(set(PHASE_THREE_CORE_NESTED_TYPES))
    assert combined == typed_ir_contract_prompt({"xychart", "pie", "quadrant"})
    assert combined.index("- pie:") < combined.index("- quadrant:") < combined.index("- xychart:")
    assert "xychart.series[].label" not in combined
    assert "xychart.series[].name" not in combined

    for diagram_type, records in expected_records.items():
        contract = TYPED_IR_CONTRACTS[diagram_type]
        prompt = typed_ir_contract_prompt({diagram_type})
        assert contract.prompt_records == records
        assert all(f"  {diagram_type}.{record}" in prompt for record in records)
        assert all(
            f"  {other_type}." not in prompt
            for other_type in PHASE_THREE_CORE_NESTED_TYPES - {diagram_type}
        )


def test_all_enabled_nested_contract_prompts_fit_minimum_request_budget():
    captured: dict[str, object] = {}

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    engine = MarkerStructuredVLMEngine(
        service,
        enabled_types=set(ALL_TYPES),
        max_prompt_chars=MIN_VLM_PROMPT_CHARS,
    )
    engine.observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=Image.new("RGB", (20, 20), "white"),
            views={"original": Image.new("RGB", (20, 20), "white")},
        )
    )

    prompt = captured["prompt"]
    assert isinstance(prompt, str)
    assert len(prompt) + engine.response_schema_chars_reserved <= MIN_VLM_PROMPT_CHARS
    assert EngineObservation.model_json_schema()["$defs"]["TypedIRCandidate"]["properties"][
        "ir"
    ] == {"additionalProperties": True, "title": "Ir", "type": "object"}
