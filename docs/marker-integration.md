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
사용합니다. 기본 engine 순서는 Vector Primitive, Geometry, Structured VLM입니다. merged source는
모든 source block과 assembly `page_to_canvas` mapping을 vector engine에 전달합니다. VLM 호출은 OCR
token과 앞선 vector/geometry evidence 중 bounded selector가 고른 subset을 포함한 `prompt`, 갱신된 다중
view image list, anchor block, `EngineObservation` response schema를 전달합니다. Marker 1.10.2 Claude가
같은 schema를 system text로 추가하는 크기를 보수적으로 request budget에 예약하며, 다른 built-in
provider에도 같은 reserve를 적용합니다. adapter는 실제 선택한 prior ID와 `PromptBudgetNotice`를 provider
schema에 없는 private metadata로 최종 observation에 붙입니다. Pipeline은 observation을 다시 검증한 뒤
이 metadata를 별도로 보존하고 fused 후보와 개별 engine 후보에 candidate budget을 round-robin으로
적용합니다.

Marker processor가 넘긴 block/page ID, OCR, evidence, block/vector source collection은 engine adapter보다
앞선 pipeline 경계에서 exact plain list와 hard cap을 검사합니다. 잘못된 collection 하나는 다른 source
metadata나 문서 변환을 실패시키지 않고 안전한 기본값으로 격리됩니다. Composite/merged source의
`source_mapping`도 bounded canonical JSON snapshot으로 고정되며, 유효하지 않으면 mapping만 생략하고
review 가능한 failure metadata를 남깁니다. 각 engine은 새로 복원한 source snapshot을 받으므로 앞선
custom engine의 context mutation이 다음 engine이나 semantic repair로 전달되지 않습니다.

## 전용 renderer가 필요한 이유

Marker 1.10.2 기본 renderer는 Figure/Picture만 image block으로 취급하고 internal metadata를
document metadata에 복사하지 않습니다. `MermaidMarkdownRenderer`는 다음을 추가합니다.

- ComplexRegion 원본 추출
- 원본 image 뒤 source별 검증된 Mermaid 1회 삽입
- 최종 Mermaid/SVG와 게시 결정에 결합된 pipeline receipt 재확인
- panel/merged virtual 원본 image 추가
- B/C warning
- Mermaid metadata 수집
- image reference를 `images/` 아래로 이동
- `extract_images=false` 거부

Marker 기본 `save_output`은 nested sidecar를 지원하지 않으므로 CLI는 `save_document_output`을
사용합니다.

`publish=true`나 `syntax_valid/render_valid` flag만으로 renderer를 우회할 수 없습니다. 검증 뒤
source/SVG 또는 게시 정책 결과가 변경되었거나 pipeline의 process-private receipt seal이 없는 결과는
해당 source의 Mermaid와 preview를 생략하고 원본 image를 계속 보존합니다. Preview PNG는 별도 digest가
현재 bytes와 일치할 때만 추가하며, 불일치하면 Mermaid code는 유지하고 preview만 격리합니다.
Renderer는 boolean 검사 뒤 live candidate를 다시 읽지 않습니다. Code, grade/score, optional PNG와 두
receipt를 한 번에 캡처한 immutable publication snapshot을 process-private HMAC으로 봉인하고, 같은
snapshot에서 Mermaid fence와 preview를 만듭니다. Snapshot을 직접 만들거나
`model_copy(update=...)`로 값을 바꾸면 seal 검사가 실패합니다. Preview 생략 진단은 이미 봉인된 candidate
warning list를 수정하지 않으므로 이후 sidecar 기록의 품질 receipt도 그대로 유효합니다.
Snapshot과 Marker의 JSON-safe `mermaid` summary는 grade에서 안정도를 추측하지 않고 선택 serializer의
`stable`/`extended`/`experimental` 값을 그대로 사용합니다. 따라서 grade A experimental 후보도 Markdown과
internal metadata 양쪽에서 안정도 표시를 잃지 않습니다. `publish=true`인데 같은 trusted snapshot을 만들 수
없는 변이 결과는 live candidate의 안정도를 다시 믿지 않고 summary에서도 `experimental`로 낮춥니다.

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
