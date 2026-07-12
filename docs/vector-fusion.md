# Vector extraction과 fusion

## VectorPrimitiveEngine

PDF provider 구현에 결합되지 않도록 duck typing으로 다음 source를 읽습니다.

- `get_drawings()`와 `get_text("dict" | "words")`
- `vector_primitives`와 `vector_texts`
- block의 `page`, `document_page`, `page_ref`

closed rectangle, ellipse, polygon만 node가 됩니다. open line/path는 양 끝점이 서로 다른 node에 유일하게
닿을 때만 relation이 되며, provider가 명시한 arrow flag만 방향으로 사용합니다. vector text 중심이
node 하나에만 포함될 때 label로 결합합니다. fill/stroke color와 line style도 Scene IR에 보존합니다.
PyMuPDF span의 integer bold flag `16`은 `vector_text` evidence에 보존합니다. 한 node에 포함된 span이
전부 bold일 때만 node bold를 복원하며 mixed/partial weight는 warning과 함께 생략합니다. 동일
text+bbox의 weight 충돌도 label을 중복하지 않고 emphasis만 생략합니다.

panel/merged source는 `source-map.json`과 같은 assembly placement의 `page_to_canvas` affine을 사용합니다.
block/page mapping이 모호하거나 없으면 bbox fallback warning을 남기며, primitive가 없으면 unknown empty
observation으로 종료합니다. PyMuPDF cubic curve에서 ellipse를 추측하거나 raster 선을 vector로 간주하지
않습니다.

## FusionEngine

각 engine은 `fusion_source`를 명시합니다. evidence ID 문자열로 출처를 추측하지 않습니다.

| 필드 | 우선순위 |
| --- | --- |
| node/edge geometry | vector → geometry → other → VLM → OCR |
| node label | vector text → OCR consensus → other → VLM |
| font weight | 단일 합의값만 유지; bold/normal 충돌 시 생략 |
| semantic relation | VLM → other → vector → geometry → OCR |
| type distribution | source별 고정 weight의 결정적 합성 |

node는 동일 ID 또는 정규화된 bbox IoU로 cluster합니다. relation endpoint와 group member는 fused node ID로
다시 매핑하며 provenance와 source block ID를 합칩니다. 서로 다른 값이 경쟁하면 우선순위로 선택하고
warning을 남깁니다. typed/direct candidate는 canonical JSON/code 기준으로 중복 제거합니다.

bold를 Mermaid로 방출하는 단계는 fused Scene 값만 신뢰하지 않습니다. 실제 vector engine origin,
provenance ID의 비충돌, evidence text/bbox와 generated candidate node mapping을 다시 확인합니다. 이
경계 덕분에 VLM이나 fixture가 임의로 넣은 `font_weight` 또는 self-declared `vector_text`가 자동 style로
승격되지 않습니다.

pipeline은 fusion 자체도 failure-isolated 처리합니다. fused observation을 첫 후보 group으로 두되 원
engine observation도 유지하고 code hash 중복 제거와 round-robin budget을 적용합니다. 따라서 fusion이
실패하거나 특정 후보를 과도하게 병합해도 독립 후보를 검토할 수 있습니다.
