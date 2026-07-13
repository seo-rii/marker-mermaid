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

Scene node는 동일 ID 또는 정규화된 bbox IoU로 cluster합니다. relation endpoint와 group member는 fused
Scene node ID로 다시 매핑하며 provenance와 source block ID를 합칩니다. 서로 다른 값이 경쟁하면
우선순위로 선택하고 warning을 남깁니다. typed/direct candidate는 canonical JSON/code 기준으로 중복
제거합니다. 이 일반 Scene cluster 규칙 자체는 typed IR의 ID를 바꿀 권한이 아닙니다.

### Flow node ID 정합화

typed IR ID 정합화는 현재 `flowchart`와 `generic_network`의 평면 flow 구조에만 적용합니다. 먼저 typed
`nodes[].id`가 그 후보를 낸 owner의 같은 응답 `scene_ir.elements[].id`를 정확히 재사용해야 합니다. 그
Scene element가 별도 owner의 명시적 `vector` 또는 `geometry` input에서 온 하나의 fused node cluster와
최소 IoU 0.45 이상으로 유일하게 대응하고, 양쪽 provenance가 비어 있지 않으며 evidence ID collision이
없을 때만 fused Scene ID를 authority로 인정합니다. label 일치, VLM bbox 단독, evidence kind 문자열의
self-declaration은 authority가 아닙니다.

source 쪽 evidence는 semantic engine 호출 전에 이미 pipeline context에 있던 ID와 payload여야 하고
same-owner Scene element와 typed node가 최소 하나를 공유해야 합니다. evidence bbox 중심은 node 안에
있어야 하며 OCR/vector text는 NFKC·casefold·공백 정규화 뒤 node text와 일치하거나 포함 관계여야
합니다. authority 쪽은 해당 vector/geometry observation이 직접 낸 `contour` evidence만 허용하고 contour
bbox도 authority node와 최소 IoU 0.45로 대응해야 합니다. 다른 owner가 같은 ID를 뒤늦게 선언하거나
어느 단계에서든 ID가 중복되면 mapping 권한을 잃습니다. 따라서 VLM이 evidence record와 ID를 함께
만들어 `Prior evidence`인 것처럼 보이게 하거나 geometry reference를 대신 선언할 수 없습니다.
Pixel Scene의 `canvas_size`는 engine self-claim으로 사용하지 않고 현재 reconstruction source image의
trusted width/height와 정확히 같을 때만 mapping 좌표계로 사용합니다. source/authority evidence가
공유하는 block ID도 pipeline의 현재 trusted source block 집합과 교차해야 합니다. 따라서 작은 가짜
canvas나 양쪽에 같은 가짜 block ID를 넣어 멀리 떨어진 bbox를 겹쳐 보이게 할 수 없습니다. Evidence
coordinate metadata가 별도로 보존되지 않는 현재 단계에서는 이 인증 경로가 trusted pixel Scene만
받으며 normalized Scene은 일반 fusion에는 참여해도 typed ID mapping 권한은 얻지 않습니다.

후보의 모든 node가 이 gate를 통과하고 target ID가 서로 겹치지 않는 full/injective mapping일 때만 한
번에 다음 reference를 다시 씁니다.

- `nodes[].id`
- `edges[].source`와 `edges[].target`
- `groups[].member_ids`

그 밖의 문자열이나 nested reference는 재귀적으로 치환하지 않습니다. duplicate/missing node ID,
dangling endpoint/member, 모호한 IoU, evidence collision, many-to-one target, 부분 coverage 중 하나라도
있으면 후보 전체를 원본 그대로 유지합니다. 따라서 한 후보 안에 원래 ID와 fused ID가 섞이는 partial
remap은 없습니다. 안전하게 완료된 mapping만 `node-id-map.json`의 audit record가 됩니다.
같은 type에서 mapping-backed 후보와 mapping 없는 후보가 canonical IR 또는 emitted code를 공유하면
fusion은 audit record가 있는 후보를 먼저 budget에 배치합니다. 낮은 confidence의 안전한 mapping이
동일 출력의 비인증 후보에 가려져 sidecar만 사라지는 것을 막기 위한 결정 규칙입니다.

후속 자동 semantic repair는 label/edge 수정 중에도 mapped node set을 바꿀 수 없습니다. node 추가,
삭제 또는 ID 교체 proposal은 mapping audit와 typed IR가 어긋나므로 검증 전에 거부합니다.

relation 방향 충돌도 remap 전 owner ID에서 fused ID pair로 옮겨 보존합니다. 반대 방향을 낸 독립
engine들이 같은 canonical pair로 매핑되면 그 pair는 계속 conflicted 상태이며 semantic repair 권한을
얻지 않습니다. ID 변경이 방향 disagreement를 숨기거나 해결한 것으로 간주하지 않습니다.

Swimlane/BPMN처럼 nested flow container를 쓰는 후보, 다른 typed diagram 유형, direct Mermaid 및 generic
Scene fallback은 이 정합화의 지원 대상이 아닙니다. 이 경계가 확장되기 전까지는 해당 구조를 추측해
바꾸지 않습니다.

bold를 Mermaid로 방출하는 단계는 fused Scene 값만 신뢰하지 않습니다. 실제 vector engine origin,
provenance ID의 비충돌, evidence text/bbox와 generated candidate node mapping을 다시 확인합니다. 이
경계 덕분에 VLM이나 fixture가 임의로 넣은 `font_weight` 또는 self-declared `vector_text`가 자동 style로
승격되지 않습니다.

pipeline은 fusion 자체도 failure-isolated 처리합니다. fused observation을 첫 후보 group으로 두되 원
engine observation도 유지하고 code hash 중복 제거와 round-robin budget을 적용합니다. 따라서 fusion이
실패하거나 특정 후보를 과도하게 병합해도 독립 후보를 검토할 수 있습니다.
