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
결합 label·font·provenance는 새 `SceneElement`로 다시 검증합니다. Contour 1개를 포함해 record당
evidence reference는 최대 256개이며, 결합 text 또는 reference가 상한을 넘으면 일부 ID만 자르지
않습니다. 해당 text enrichment 전체를 생략하고 원래 contour-only node와 개별 `vector_text` evidence,
명시적 warning을 유지하므로 뒤 engine은 원본 span을 계속 사용할 수 있습니다.

### Reconstruction-global 자원 예산

Vector budget은 provider·page·fragment별로 새로 시작하지 않고 reconstruction 하나의 모든
vector source에 공유됩니다. 기본값과 절대 상한은 다음과 같습니다.

| 자원 | 기본값 | 추가 제약 |
| --- | ---: | --- |
| raw primitive/command record | 2,048 | 설정 최대 5,000 (`SceneElement` 상한) |
| raw vector text record | 5,000 | primitive+text 설정 최대의 합이 20,000 이하 |
| vector text 문자 | 8,000,000 | reconstruction evidence 문자 상한을 늘릴 수 없음 |
| provenance reference fan-out | 20,000 | 생성 예정 evidence 전체의 source-block logical reference 합계 |
| provenance 문자 fan-out | 8,000,000 | Python 문자열 길이 기준, evidence별 반복 복제도 모두 계산 |
| vector source | 256 | source 순서의 bounded prefix만 검사 |
| 전체 보존 point | 100,000 | source 전체의 polygon/polyline geometry 합계 |
| vector metadata token | 256자 | kind, command, color, style, coordinate-space 등 |
| warning | 256 | 초과·비정규 warning은 하나의 종단 warning으로 정규화 |

이 수치는 최종에 남은 node/span 개수가 아니라 provider에서 읽은 원시 작업량을
제한합니다. 파싱에 실패한 record, crop 밖으로 mapping된 record, deduplication으로 사라진
record, 빈 nested drawing container도 해당 dimension의 예산을 소모합니다. 그렇지 않으면
유효한 Scene을 하나도 만들지 않는 입력이 무제한으로 뒤 source를 순회할 수 있습니다.

Provenance fan-out은 위 raw-work count와 별도 단계에서 검사합니다. 유효 record를 고르고
deduplication한 뒤 canonical source-block ID와 생성 예정 shape/text/open-line `VisualEvidence`
record의 곱을 reference 수로 계산하고, 같은 각 ID의 Python 문자열 길이도 evidence마다 반복해
문자 수에 합산합니다. 이 계산은 어떤 Scene 또는 evidence record도 만들기 전에 끝납니다.
20,000 reference와 8,000,000자는 각각 exact boundary까지 허용하며 어느 한쪽이라도 초과하면
vector 결과 전체를 원자적으로 격리합니다. 결과는 unknown prediction, `scene_ir=None`, 빈 evidence와
하나의 budget warning만 가지므로 일부 provenance prefix가 평가·게시 authority를 얻지 못합니다.
Pipeline은 이 payload 없는 warning observation을 bounded generation failure로 변환해 result와
sidecar manifest에 남기므로 sibling engine이 게시를 계속해도 초과 원인은 추적할 수 있습니다.

Source collection, raw record iterable, PyMuPDF drawing `items`는 필요할 때만 스트리밍하며,
상한 초과를 판정하기 위해 최대 한 개만 더 읽습니다. Primitive count, text count,
text character 중 하나가 닫히면 그 dimension은 나중 source에서 다시 열지 않습니다.
다른 dimension에 예산이 남아 있는 동안만 그 입력을 계속 읽습니다. Polygon/closed shape의
point는 256개, open polyline은 512개까지이며 초과 record는 부분 point로 복원하지 않고
전체를 생략합니다. Reconstruction 전체에서 보존하는 point도 100,000개로 제한하며,
point 예산을 소진한 뒤에도 point가 없는 rectangle 같은 record는 primitive count 예산 안에서
계속 처리할 수 있습니다.

건수 상한 안의 계산량도 별도로 제한합니다. Primitive는 exact key를 hash로 제거한 뒤
근사 bbox deduplication을 최대 250,000회만 비교합니다. Text-to-node ownership과 connector
endpoint ownership은 각각 최대 1,000,000회 비교하며, 상한 뒤 label은 미배정 상태로,
connector는 unresolved 상태로 남기고 warning을 기록합니다. Kind·command·color·style 같은
비-label token은 각 256자로 제한하고 임의 객체의 문자열 coercion을 호출하지 않습니다.
Direct text attribute와 `get_text("dict"/"words")`의 duck-typed span은 label을 한 번만 읽어 plain
record로 snapshot한 뒤 exact-string 길이를 파싱 전에 같은 aggregate character budget에 합산하고,
좌표·confidence·canvas·tolerance·source ID의 초대형 정수는 float/decimal 변환 전에 거부합니다.

Built-in extractor가 작업량 metadata를 남기지만 이 값 자체도 신뢰 경계 밖에 있습니다.
`VectorPrimitiveEngine`은 custom extractor의 observation을 다시 bound하고 보고된 작업량을
보존 record 수 이상·남은 예산 이하로 clamp합니다. `VectorObservation.to_engine_observation()`을
직접 호출해도 같은 primitive/text/문자/point/warning 및 aggregate provenance 상한을 다시
적용합니다. 따라서 built-in, direct, custom extraction은 모두 같은 최종 preflight를 통과합니다.
세부 예산은 현재 Marker JSON 공개 설정이 아니라 engine 생성자와 통합 계층의 조정
지점이며, aggregate provenance 상한은 공개 config/API를 추가하지 않는 내부 정책입니다.

이 예산은 provider가 값을 반환한 뒤의 소비와 정규화를 제한합니다. Provider property/callable,
custom extractor, PyMuPDF `get_text()`/`get_drawings()` 자체의 실행과 내부 materialization은 아직
별도 process로 격리되지 않으므로 trusted local integration 경계로 취급합니다.

panel/merged source는 `source-map.json`과 같은 assembly placement의 `page_to_canvas` affine을 사용합니다.
Built-in `VectorPrimitiveEngine.observe()`는 source를 순회하기 전에 reconstruction-local bounded
placement index를 한 번만 만듭니다. Index는 exact-dict placement reference를
all/page/block/page+block candidate tuple로 보존하며, 각 source는 page/block dictionary를 O(1)로
조회해 유일한 placement를 선택합니다. Index build 중에는 transform을 파싱하지 않습니다. 선택된
placement의 affine/bbox만 지연 파싱하고, 그 결과는 해당 source의
`page`/`document_page`/`page_ref` provider가 모두 공유합니다. 따라서 최대 256 source와 placement
조합에서도 placement/source-ID 목록을 source별로 다시 스캔하지 않습니다.
Standalone `extract_vector_observation()`도 page-coordinate mapping이 필요하면 해당 호출 내에서
같은 index를 한 번 만듭니다. Custom extractor는 built-in mapping index를 만들지 않습니다.

Placement input은 exact built-in list/tuple로 256개까지 손실 없이 받고 257번째를
한 개 lookahead하면 index 전체를 invalid로 둡니다. 각 placement의 `source_block_ids`도 256개까지
받으며 257개면 그 placement의 block/page+block key 전체를 생성하지 않습니다. 일부 ID
prefix만 authority로 사용하지 않지만 해당 placement은 all/page ambiguity에는 계속 포함됩니다.
Transform validity는 index candidate를 걸러내는 조건이 아니므로 malformed placement도 ambiguity에
남고, 이를 미리 제외해 허위로 유일한 placement를 만들지 않습니다. Match가 유일할 때 선택
placement의 affine/bbox를 파싱하며, 선택 affine이 invalid이거나 match가 0개/여러 개이면 bbox
fallback warning을 남깁니다.

Placement index key는 exact bounded string만 받습니다. Source identity의 exact bounded integer는
decimal string으로 바꾸고, Marker 1.10.2 `BlockId`는 임의 `str()` 호출 없이
`page_id`/`block_type.name`/`block_id`에서 canonical path로 복원합니다. Subclass
hash/equality/coercion hook은 실행하지 않습니다. Invalid·empty·surrogate·over-256-character
placement block ID는 index key에서 제외하고 한 placement 안의 duplicate는 한 번만 등록합니다.
Source가 exact page ID를 제공하면 같은 page placement만 허용하며, block-ID match여도
다른 page를 가로지 않습니다. Page identity가 명시됐지만 exact bounded integer가 아니면
유일 placement이 있어도 mapping을 fail closed합니다. 이 index는 공개 config/API를 추가하지 않는
built-in 통합 내부 구조입니다. PyMuPDF cubic curve에서 ellipse를 추측하거나 raster 선을
vector로 간주하지 않습니다.

## FusionEngine

각 engine은 `fusion_source`를 명시합니다. evidence ID 문자열로 출처를 추측하지 않습니다.

| 필드 | 우선순위 |
| --- | --- |
| node/edge geometry | vector → geometry → other → VLM → OCR |
| node label | vector text → OCR consensus → other → VLM |
| font weight | 단일 합의값만 유지; bold/normal 충돌 시 생략 |
| semantic relation | VLM → other → vector → geometry → OCR |
| type distribution | source별 고정 weight의 결정적 합성 |

### Aggregate evidence ingress/output budget

Fusion은 observation projection을 JSON으로 만들거나 evidence winner를 deep-copy하기 전에 모든
`observation.evidence`와 정렬 key에 직렬화되는 `FusionInput.prior_evidence`를 하나의 누적 snapshot으로
고정합니다. `VisualEvidence.source_block_ids` occurrence는 duplicate 포함 20,000개, 해당 ID의 Python
문자열 길이는 8,000,000자까지이며 `id`·`kind`·`text`·`font_weight`까지 포함한 full-evidence 문자도
독립적으로 8,000,000자까지입니다. Exact boundary는 허용하고 `+1`은 `_fuse_evidence`나 live
`model_copy` 전에 fusion 호출 전체를 원자적으로 거부합니다. Fused evidence도 새 예산으로 다시 detached
snapshot한 뒤에만 `EngineObservation`을 만듭니다.

공용 snapshot은 exact list/model field를 built-in access로 읽어 nested source-block list와 scalar를
분리하고, live `model_dump`·iteration/equality/coercion hook을 호출하지 않습니다. Pipeline의
initial/custom-engine collection, reconstruction-global whole-new-ID admission, final result 및
publication/Markdown/sidecar/output 경계도 같은 상수를 사용합니다. Vector의 prospective fan-out 검사는
Scene/evidence allocation 전에 실행하는 더 이른 최적화 경계이지만 상한은 이 generic runtime 계약과
공유합니다. 공개 config나 sidecar schema/manifest version은 바뀌지 않습니다. Marker OCR adapter와
Review provenance read/replacement/structured-add도 같은 공용 경계에 연결되어 있으며 evaluation
prediction ingestion은 후속입니다.

Scene node는 동일 ID 또는 정규화된 bbox IoU로 cluster합니다. relation endpoint와 group member는 fused
Scene node ID로 다시 매핑하며 provenance와 source block ID를 합칩니다. 서로 다른 값이 경쟁하면
우선순위로 선택하고 warning을 남깁니다. typed/direct candidate는 canonical JSON/code 기준으로 중복
제거합니다. 이 일반 Scene cluster 규칙 자체는 typed IR의 ID를 바꿀 권한이 아닙니다.

Element/relation `evidence_ids`와 같은 VisualEvidence의 `source_block_ids` 합집합도 record별 256개
상한 안에서만 적용합니다. 합집합이 넘치면 bounded prefix를 게시하거나 record를 삭제하지 않고,
같은 evidence ID의 모든 engine 입력을 한 번에 폐기해 앞선 부분 합집합도 남기지 않습니다. 이렇게
cross-input enrichment 전체를 생략해 위 우선순위의 원 winner record를 유지하고 warning을 남깁니다.
Relation endpoint remap과 direction-conflict 추적은 이 경우에도 유지됩니다. 모든 변형 record는 새
Pydantic record로 다시 검증되고, pipeline도 내부 fused Scene/evidence와 exact-list/20,000-item evidence
collection 및 aggregate source-block reference/문자·full-evidence 문자 계약을 후보 생성 전에 한 번 더
검증합니다. 따라서 정확히 256개는 손실 없이 합쳐지고 257번째
근거가 scoring·게시·sidecar 사이의 계약 차이를 만들지 않습니다.

### Flow node ID 정합화

typed IR ID 정합화는 현재 `flowchart`와 `generic_network`의 평면 flow 구조에만 적용합니다. 먼저 typed
`nodes[].id`가 그 후보를 낸 owner의 같은 응답 `scene_ir.elements[].id`를 정확히 재사용해야 합니다. 그
Scene element가 별도 owner의 명시적 `vector` 또는 `geometry` input에서 온 하나의 fused node cluster와
최소 IoU 0.45 이상으로 유일하게 대응하고, 양쪽 provenance가 비어 있지 않으며 evidence ID collision이
없을 때만 fused Scene ID를 authority로 인정합니다. label 일치, VLM bbox 단독, evidence kind 문자열의
self-declaration은 authority가 아닙니다.

source 쪽 evidence는 semantic engine 호출 전에 이미 pipeline context에 있던 비충돌 ID와 payload여야
합니다. Marker Structured VLM 입력은 그중 실제 bounded prompt에 선택된 private ID 집합에도 있어야 하며,
same-owner Scene element와 typed node가 최소 하나를 공유해야 합니다. evidence bbox 중심은 node 안에
있어야 하며 OCR/vector text는 NFKC·casefold·공백 정규화 뒤 node text와 일치하거나 포함 관계여야
합니다. authority 쪽은 해당 vector/geometry observation이 직접 낸 `contour` evidence만 허용하고 contour
bbox도 authority node와 최소 IoU 0.45로 대응해야 합니다. 다른 owner가 같은 ID를 뒤늦게 선언하거나
어느 단계에서든 ID가 중복되면 mapping 권한을 잃습니다. 따라서 VLM이 prompt에서 빠진 예측 가능한 ID를
인용하거나 evidence record와 ID를 같은 응답에 만들어 `Prior evidence`인 것처럼 보이게 하거나 geometry
reference를 대신 선언할 수 없습니다. Fused typed candidate는 선택된 원 owner의 닫힌 게시 evidence
집합에 독립 인증된 mapping source/authority ID만 더합니다.
각 `FusionInput.publication_evidence_ids`가 `None`인 legacy input만 기존 unrestricted 의미를 가지며,
명시적 빈 집합은 source prior와 authority contour 양쪽에서 완전히 닫힌 권한입니다. ID mapping 인증도
두 input의 이 경계를 통과한 record만 사용할 수 있습니다.

동일한 direct Mermaid code가 여러 input에서 중복되면 confidence/source 우선순위로 선택된 원 owner의
publication evidence authority만 canonical candidate key에 연결합니다. 다른 input의 권한을 합집합으로
넓히지 않으며, 선택 owner의 명시적 빈 집합도 그대로 보존합니다.

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
