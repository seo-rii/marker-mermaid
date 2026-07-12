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

Discovery는 구조에 포함된 block과 page의 current children을 함께 보며 ID로 중복을 제거합니다.
Reconstruction은 `Block.get_image(document, highres=True)`를 사용합니다. Span bbox는 page 좌표에서
crop pixel 좌표로 변환되어 OCR evidence가 됩니다.

## LLM service adapter

Marker의 dependency resolver가 `llm_service`라는 생성자 parameter에 service를 주입합니다.
`MarkerStructuredVLMEngine`만 Marker service API를 알고 core pipeline은 `CandidateEngine` Protocol만
사용합니다. 호출은 `prompt`, 다중 view image list, 원 block, `EngineObservation` response schema를
전달합니다.

## 전용 renderer가 필요한 이유

Marker 1.10.2 기본 renderer는 Figure/Picture만 image block으로 취급하고 internal metadata를
document metadata에 복사하지 않습니다. `MermaidMarkdownRenderer`는 다음을 추가합니다.

- ComplexRegion 원본 추출
- 원본 image 뒤 검증된 Mermaid 1회 삽입
- B/C warning
- Mermaid metadata 수집
- image reference를 `images/` 아래로 이동
- `extract_images=false` 거부

Marker 기본 `save_output`은 nested sidecar를 지원하지 않으므로 CLI는 `save_document_output`을
사용합니다.

## metadata

각 block에는 `set_internal_metadata("mermaid", data)`로 status, stability, type, score, selected ID,
code, sidecar path가 저장됩니다. Pydantic private metadata는 기본 renderer에 노출되지 않으므로
전용 renderer가 JSON-safe summary를 생성합니다.

