# Marker 1.10.2 통합

## Processor 순서

`MarkerMermaidPdfConverter`는 Marker의 전체 기본 processor tuple을 유지하고 정확히 다음 위치에
두 processor를 삽입합니다.

```text
ReferenceProcessor
MermaidCandidateDiscoveryProcessor
MermaidDiagramProcessor
BlankPageProcessor
DebugProcessor
```

Marker에 `processor_list`를 전달하면 기본 목록 전체가 대체되므로, Mermaid processor만 넘기면
안 됩니다. 전용 converter를 쓰는 이유입니다. Marker의 전역 Block registry는 변경하지 않습니다.

## 대상 block

```python
DEFAULT_BLOCK_TYPES = (
    BlockTypes.Figure,
    BlockTypes.Picture,
    BlockTypes.ComplexRegion,
)
```

Discovery, reconstruction, renderer는 같은 candidate iterator를 사용하여 구조에 포함된 block과 page의
`current_children`을 함께 봅니다. 각 anchor는 original/full-page, panel, merged source를 가질 수 있으며
순서는 original/full-page → panel ID → merged로 고정됩니다.

Reconstruction은 `Block.get_image(document, highres=True)`를 expansion 없이 사용합니다. original은
정확한 block crop, panel은 raw crop 안의 bbox, merge는 여러 raw fragment를 virtual canvas에 배치합니다.
각 Span은 fragment page bbox와 교차·clip한 뒤 page→canvas affine으로 변환되므로 다른 panel의 label이
OCR recall에 섞이지 않고 다음 page의 label에는 올바른 canvas offset이 적용됩니다.

## LLM service adapter

Marker의 dependency resolver가 `llm_service`라는 생성자 parameter에 service를 주입합니다.
`MarkerStructuredVLMEngine`만 Marker service API를 알고 core pipeline은 `CandidateEngine` Protocol만
사용합니다. 기본 engine 순서는 Geometry, Structured VLM입니다. VLM 호출은 OCR token과 앞선 geometry
evidence가 포함된 `prompt`, 다중 view image list, 원 block, `EngineObservation` response schema를
전달합니다. candidate budget은 두 engine 후보에 round-robin으로 적용됩니다.

## 전용 renderer가 필요한 이유

Marker 1.10.2 기본 renderer는 Figure/Picture만 image block으로 취급하고 internal metadata를
document metadata에 복사하지 않습니다. `MermaidMarkdownRenderer`는 다음을 추가합니다.

- ComplexRegion 원본 추출
- 원본 image 뒤 source별 검증된 Mermaid 1회 삽입
- panel/merged virtual 원본 image 추가
- B/C warning
- Mermaid metadata 수집
- image reference를 `images/` 아래로 이동
- `extract_images=false` 거부

Marker 기본 `save_output`은 nested sidecar를 지원하지 않으므로 CLI는 `save_document_output`을
사용합니다.

## metadata

직렬화 경계가 흐려지지 않도록 metadata key를 분리합니다.

| key | 내용 |
| --- | --- |
| `mermaid_candidate` | JSON-safe source registry summary |
| `mermaid_candidate_images` | runtime-only fragment ID → raw PIL image |
| `mermaid` | JSON-safe reconstruction summary와 source별 오류 |
| `mermaid_results` | runtime-only `ReconstructionResult` 목록 |
| `mermaid_source_images` | runtime-only virtual output PIL image |

document metadata writer는 `default=str` fallback을 쓰지 않습니다. PIL이나 Marker 객체가 JSON summary에
잘못 유입되면 저장 전에 즉시 실패합니다.
