from __future__ import annotations

from xml.etree import ElementTree

import pytest
from PIL import Image

import marker_mermaid.pipeline as pipeline_module
from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import MermaidConfig
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import DiagramTypePrediction, EngineObservation, TypedIRCandidate
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.scoring import numeric_consistency, ocr_recall
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_special import serialize_special
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime


def _packet_ir(*, title: object = "Packet title") -> dict[str, object]:
    return {
        "title": title,
        "fields": [
            {
                "id": "version",
                "start": 0,
                "end": 3,
                "label": "Version 2",
            }
        ],
    }


@pytest.mark.parametrize("emitted_diagram_type", ["packet", "packet-beta"])
def test_native_packet_semantic_texts_include_exact_serializer_visible_title(
    emitted_diagram_type: str,
) -> None:
    ir = _packet_ir(title="  Packet   &#34;  style  ")
    scene = typed_ir_to_scene(
        "packet",
        ir,
        emitted_diagram_type=emitted_diagram_type,
    )

    assert scene is not None
    serializer_title = "Packet ＆＃34; s\u200btyle"
    semantic_title = "Packet ＆＃34; style"
    native = serialize_special("packet", ir)
    assert f"    title {serializer_title}\n" in native.code
    semantic_texts = list(
        typed_ir_semantic_texts(
            "packet",
            ir,
            scene,
            emitted_diagram_type=emitted_diagram_type,
        )
    )
    assert semantic_texts == [semantic_title, "Version 2"]
    assert (
        ocr_recall(
            ["Packet &#34; style Version 2"],
            "",
            generated_texts=semantic_texts,
        )
        == 1
    )


@pytest.mark.parametrize("emitted_diagram_type", ["flowchart", "flowchart-v2"])
def test_packet_flowchart_fallback_semantic_texts_exclude_native_only_title(
    emitted_diagram_type: str,
) -> None:
    ir = _packet_ir(title="Native-only packet title")
    scene = typed_ir_to_scene(
        "packet",
        ir,
        emitted_diagram_type=emitted_diagram_type,
    )

    assert scene is not None
    fallback = serialize_special("packet", ir, native_runtime_valid=False)
    assert fallback.emitted_type == "flowchart"
    assert "\n    title Native-only packet title\n" not in fallback.code
    assert list(
        typed_ir_semantic_texts(
            "packet",
            ir,
            scene,
            emitted_diagram_type=emitted_diagram_type,
        )
    ) == ["Version 2"]


@pytest.mark.parametrize("title", ["   ", "Packet\x00title", "Packet\ntitle"])
def test_native_packet_semantic_title_rejects_every_title_rejected_by_serializer(
    title: str,
) -> None:
    ir = _packet_ir(title=title)
    scene = typed_ir_to_scene("packet", ir, emitted_diagram_type="packet")

    assert scene is not None
    with pytest.raises(SerializationError):
        serialize_special("packet", ir)
    with pytest.raises(SerializationError):
        list(
            typed_ir_semantic_texts(
                "packet",
                ir,
                scene,
                emitted_diagram_type="packet",
            )
        )


def test_packet_title_projection_does_not_change_field_or_numeric_scoring() -> None:
    ir = _packet_ir(title="Packet metadata 999")
    native = serialize_special("packet", ir)
    fallback = serialize_special("packet", ir, native_runtime_valid=False)

    assert numeric_consistency(["0 3 Version 2"], native.code) == 1
    assert numeric_consistency(["0 3 Version 2"], fallback.code) == 1

    projections = []
    for emitted_diagram_type in ("packet", "flowchart-v2"):
        scene = typed_ir_to_scene(
            "packet",
            ir,
            emitted_diagram_type=emitted_diagram_type,
        )
        assert scene is not None
        projections.append(
            list(
                typed_ir_semantic_texts(
                    "packet",
                    ir,
                    scene,
                    emitted_diagram_type=emitted_diagram_type,
                )
            )
        )

    assert projections == [["Packet metadata 999", "Version 2"], ["Version 2"]]


@pytest.mark.integration
def test_native_packet_semantic_title_matches_mermaid_11_16_canvas() -> None:
    ir = _packet_ir(title="Packet &#34; style")
    result = serialize_special("packet", ir)
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, MermaidConfig().security_profile)

    try:
        outcome = validator.validate(result.code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    root = ElementTree.fromstring(outcome.runtime.svg or "")
    canvas_titles = [
        " ".join("".join(node.itertext()).replace("\u200b", "").split())
        for node in root.findall(".//{*}text")
        if "packetTitle" in node.attrib.get("class", "").split()
    ]
    scene = typed_ir_to_scene("packet", ir, emitted_diagram_type="packet")
    assert scene is not None
    semantic_texts = list(
        typed_ir_semantic_texts(
            "packet",
            ir,
            scene,
            emitted_diagram_type="packet",
        )
    )
    assert canvas_titles == ["Packet ＆＃34; style"]
    assert semantic_texts[0] == canvas_titles[0]


class _PacketRuntime:
    def __init__(self, *, reject_native: bool) -> None:
        self.reject_native = reject_native

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        if code.startswith("packet-beta") and self.reject_native:
            return RuntimeResult(False, False, error="native packet rejected")
        return RuntimeResult(
            True,
            True,
            diagram_type="packet" if code.startswith("packet-beta") else "flowchart-v2",
            svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
        )

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("reject_native", "expected_terminal_type"),
    [(False, "packet"), (True, "flowchart-v2")],
)
def test_pipeline_passes_validated_packet_terminal_type_to_semantic_projection(
    monkeypatch: pytest.MonkeyPatch,
    reject_native: bool,
    expected_terminal_type: str,
) -> None:
    observed_terminal_types: list[str | None] = []
    original_projection = typed_ir_semantic_texts

    def recording_projection(
        diagram_type,
        ir,
        scene,
        *,
        emitted_diagram_type=None,
    ):
        observed_terminal_types.append(emitted_diagram_type)
        yield from original_projection(
            diagram_type,
            ir,
            scene,
            emitted_diagram_type=emitted_diagram_type,
        )

    monkeypatch.setattr(pipeline_module, "typed_ir_semantic_texts", recording_projection)
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["packet"], scores=[1.0]),
        typed_candidates=[TypedIRCandidate(diagram_type="packet", ir=_packet_ir())],
    )
    config = MermaidConfig(candidate_count=1)
    pipeline = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_PacketRuntime(reject_native=reject_native), config.security_profile),
    )

    result = pipeline.reconstruct(
        "packet-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert observed_terminal_types
    assert set(observed_terminal_types) == {expected_terminal_type}
