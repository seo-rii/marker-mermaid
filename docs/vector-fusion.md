# Vector extraction과 fusion

## VectorPrimitiveEngine

PDF provider 구현에 결합되지 않도록 duck typing으로 다음 source를 읽습니다.

- `get_drawings()`와 `get_text("dict" | "words")`
- `vector_primitives`와 `vector_texts`
- block의 `page`, `document_page`, `page_ref`

closed rectangle, ellipse, polygon만 node가 됩니다. open line/path는 양 끝점이 서로 다른 node에 유일하게
닿을 때만 relation이 되며, provider가 명시한 arrow flag만 방향으로 사용합니다. vector text 중심이
node 하나에만 포함될 때 label로 결합합니다. fill/stroke color와 line style도 Scene IR에 보존합니다.

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
| semantic relation | VLM → other → vector → geometry → OCR |
| type distribution | source별 고정 weight의 결정적 합성 |

node는 동일 ID 또는 정규화된 bbox IoU로 cluster합니다. relation endpoint와 group member는 fused node ID로
다시 매핑하며 provenance와 source block ID를 합칩니다. 서로 다른 값이 경쟁하면 우선순위로 선택하고
warning을 남깁니다. typed/direct candidate는 canonical JSON/code 기준으로 중복 제거합니다.

pipeline은 fusion 자체도 failure-isolated 처리합니다. fused observation을 첫 후보 group으로 두되 원
engine observation도 유지하고 code hash 중복 제거와 round-robin budget을 적용합니다. 따라서 fusion이
실패하거나 특정 후보를 과도하게 병합해도 독립 후보를 검토할 수 있습니다.
