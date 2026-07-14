# 품질 평가와 availability

점수를 만들 수 있다는 사실과 정확하다는 사실을 혼동하지 않도록 각 metric은 `MetricResult`로
`value`, `available`, `warning`, `evidence_ids`를 함께 반환합니다. 필요한 구조가 없으면 0을 넣지 않고
`available=false`로 두며 aggregate는 실제로 존재하는 weight만 다시 정규화합니다.

## 구조 metric

| metric | 비교 | unavailable 조건의 예 |
| --- | --- | --- |
| `edge_agreement` | 정렬된 node 사이 edge multiset F1 | source relation 또는 node alignment 없음 |
| `arrow_agreement` | edge의 명시적 arrow endpoint multiset F1 | source에 arrowhead flag 없음 |
| `layout_similarity` | node 쌍의 좌/우·상/하 상대 순서 | 2개 미만 정렬, explicit generated position 없음 |
| `path_consistency` | 명시적 방향 root→terminal simple path multiset F1 | root/terminal 없음, cycle, path budget 초과 |

node 정렬은 같은 ID, collision-free portable emitted-ID alias 순서로 사용하고, 그다음 중복되지 않는
NFKC/casefold label만 사용합니다. normalized ID collision은 alias로 강제 정렬하지 않습니다. 방출 ID가
다른 raw source ID와 우연히 같아지는 collision cluster는 raw exact-ID provenance도 부여하지 않고 unique
label/evidence 같은 독립 근거가 있을 때만 정렬합니다. Geometry로 node를 맞추지 않으므로 layout metric이
자신의 가정을 검증하는 순환을 피합니다. edge topology는 방향을
무시하고 방향 오류는 arrow metric이 별도로 측정합니다.
Flowchart/Swimlane/BPMN/Architecture와 Sequence 계열의 generated Scene 방향은 raw
`arrow_at_start`/`arrow_at_end` hint가 아니라 serializer가 실제 방출하는 단방향 또는 `bidirectional`
connector에서 파생합니다.

typed IR은 serializer가 실제 방출하는 node/edge 구조로 다시 변환합니다. bbox가 IR에 명시되지 않으면
layout을 추측하지 않습니다. Scene IR portable fallback은 deterministic serializer 보존 여부를 평가할 수
있습니다. raw/direct Mermaid는 아직 일반 AST→Scene 변환이 없으므로 구조 점수가 unavailable일 수 있습니다.
평가 Scene adapter는 sequence/ZenUML, hierarchy/organization, planning/event, Packet/Ishikawa/TreeView,
Wardley/Cynefin, data-lineage, Venn까지 포함하며 typed record의 evidence ID를 보존합니다.
Event Modeling의 generated Scene은 fallback serializer와 같은 normalized frame ID, typed/time label,
lane subgraph membership, `LR` 방향을 사용합니다. Wardley의 label 없는 component와 ZenUML의 label 없는
participant도 임의 `text`가 아니라 serializer가 실제 표시하는 safe source ID를 사용합니다.
Wardley Scene은 raw record bbox 대신 native 좌표를 화면에 맞게 바꾼 `(x, 1-y)`만
`normalized` explicit position으로 씁니다. IR의 수평/수직 `x`/`y`는 native에 `[y, x]`로
방출하고 token 반올림을 Scene 값에도 적용합니다. `->` link는 실제 SVG에 marker가 없으므로
무방향 relation으로 평가해 arrow/path 점수를 만들지 않습니다.
Cynefin Scene은 domain·item·transition과 domain group membership을 공유 plan에서 복원하고,
runtime이 항상 만드는 고정 domain/practice/response/disorder text를 무근거 template element로
추가합니다. `confusion` item은 처음 세 개와 `+N more`만 실제 렌더에 맞게 투영합니다.
Native placement가 없어 zero geometry를 쓰므로 layout similarity는 unavailable이며, 고정 template의
source provenance 계약이 없는 현재 Cynefin은 aggregate와 무관하게 review를 요구합니다.
Wardley·Cynefin의 entity-like 원문을 Mermaid 11.16 호환 glyph로 표시하는 경우 OCR projection도
해당 호환 label이 실제 SVG에 보이는 text를 사용합니다. 원문을 projection에 넣어 렌더러 손실을
숨기지 않습니다. Wardley 축·evolution stage처럼 grammar 고정 chrome을 전체 source label로
간주하지는 않습니다.

Packet Scene은 serializer의 field plan에서 나온 reserved-safe emitted ID, label,
bbox/evidence를 그대로 사용하고 입력에 없는 field 간 edge를 추가하지 않습니다.
Bit range는 같은 plan에서 검증되지만 Scene element로 승격하지 않고 별도 numeric
projection/source gate에서 비교합니다.
Ishikawa/TreeView는 serializer와 공유하는 DFS plan의 정확한 parent/emitted ID로 containment를
만듭니다. Duplicate/normalized collision, missing-ID ambiguity, alias conflict, cycle, object reuse 또는
resource 한도로 planner가 거부하면 Scene adapter는 충돌 node를 조용히 제거해 attribution
분모를 줄이지 않고 전체 metric을 unavailable로 둡니다.

C4 자동 후보의 generated Scene은 진단용 native C4 macro를 재구성하지 않습니다. 자동 serializer가 실제
게시 대상으로 만드는 Architecture와 필요 시 nested Flowchart fallback을 따라, C4 element·boundary·relation을
공용 bounded Architecture service/group/edge plan에 넣습니다. 따라서 collision-safe emitted ID, boundary
membership, 표시 label, endpoint와 arrow semantics는 두 fallback grammar 및 OCR projection에서
동일합니다. element bbox/evidence, relation evidence, boundary bbox는 원 record에서 보존하지만,
relation polyline, technology, description, relation label, native boundary notation과 기타 fallback이
표시하지 않는 raw metadata는 구조나 OCR label로 승격하지 않습니다. `reading_direction`은 runtime의
Architecture→Flowchart 선택을 generated Scene이 미리 알 수 없으므로 IR 값 또는 `unknown`을 유지합니다.
형식이 잘못됐거나 reference 예산을 넘은 C4 `evidence_ids`는 기존 Mermaid 게시를 막지 않고 해당
generated Scene attribution에서 제외합니다.

## 기존 metric과 결합

- syntax/render는 게시 hard gate이면서 score input입니다.
- pipeline은 최종 source, 사후 보안 검사를 통과한 비어 있지 않은 SVG, 선택적 runtime PNG의 SHA-256,
  security profile, emitted/runtime type을 validation receipt로 함께 봉인합니다. Receipt 설치에는
  `CandidateValidator`가 exact source/SVG/PNG 검사를 끝낸 뒤 발급한 process-local certificate가 필요하며,
  단순히 candidate의 valid flag를 설정해서는 발급되지 않습니다. 별도의 publication receipt는 freshly
  recomputed publish policy, status, 자동 `review_required` routing과 선택 후보 receipt digest를
  고정합니다. 사용자 승인·거절은 generation receipt를 바꾸지 않고 review state/revision/history에
  기록합니다. Markdown renderer는 boolean flag만 신뢰하지 않고 두 receipt와 process-private seal이 현재 상태에 모두
  일치할 때만 fence를 삽입합니다. 객체를 JSON으로 왕복하면 공개 digest는 audit용으로 남지만 private
  trust는 복원되지 않으므로 다시 검증하지 않은 역직렬화 결과는 자동 게시할 수 없습니다.
  Publication receipt의 quality digest는 표시되는 aggregate score와 grade, metric map, generation
  warning을 함께 고정합니다. Pipeline은 선택 후보 warning을 중복 제거하고 최대 256개·항목당 4,096자로
  제한한 뒤 결정하므로, 점수나 `scores.json`만 바꿔 신뢰도가 높은 것처럼 표시할 수 없습니다. Digest의
  확률 값은 exponent 없는 decimal string으로 encode하고 negative zero를 `"0"`으로 정규화하므로 Python과
  JavaScript verifier가 같은 bytes를 재현할 수 있습니다.
- OCR recall은 NFKC/casefold한 원 OCR token multiset의 occurrence recall입니다. 같은 text라도 다른 bbox에서
  관찰되면 별도 occurrence로 유지하고, context OCR과 OCR/vector evidence가 겹치면 token별 최대 count를
  사용합니다. bbox가 없는 동일 text evidence는 공간적으로 다른 occurrence임을 입증하지 못하므로 하나로
  합칩니다. Typed/Scene 후보는 generated Scene의 node, relation, group label을 비교하며 Gantt task와
  section도 Scene 의미 label로 복원합니다. 따라서 Mermaid ID, schedule field, header,
  `accTitle`/`accDescr`가 recall을 올릴 수 없습니다. Scene adapter가 없는 direct 후보는 quoted label과
  문법별 보수적 label fallback을 적용합니다.
- OCR/vector reference와 생성 semantic label은 각각 최대 50,000개 observation, 1,000,000자,
  100,000 token의 평가 예산을 적용합니다. 어느 한도를 넘으면 일부 입력을 잘라 점수를 만들지 않고
  semantic evaluation을 unavailable로 표시하여 자동 게시를 막습니다. Token occurrence는 `Counter`로
  유지하며 반복 횟수만큼 list를 확장하지 않습니다. Parse/render에 실패한 후보는 구조 변환과 OCR 같은
  고비용 semantic scoring을 건너뛰고, typed Scene 변환 오류는 후보 단위 warning으로 격리합니다.
- 구조 Scene은 topology를 위해 class member나 ER attribute를 node로 만들지 않습니다. 별도의 지연형
  typed semantic projection이 실제 serializer가 표시하는 Class field/method/parameter/cardinality,
  ER attribute type/name/key/comment, Timeline period/title/모든 event label을 OCR 비교에 추가합니다.
  이 projection도 생성 label 예산 안에서 소비되므로 큰 typed IR이 제한을 우회하지 못합니다.
- Requested type이 fallback으로 방출되는 경우 projection은 요청 문법이 아니라 실제 emitted serializer를
  따릅니다. C4의 `architecture` 또는 nested Flowchart 결과는 위 공용 plan의 emitted boundary group과
  service label만 세고 technology, relation label, description은 제외합니다. Architecture도 native와
  nested Flowchart에서 service `label`/`name` alias, group label과 label 없는 topology를 동일하게
  평가합니다. label 없는 Architecture group은 두 serializer가 같은 portable emitted ID를 표시합니다.
  Deployment와 Component fallback에서 보존만 되는 relation label은 세지 않으며, Use-case Flowchart
  relation은 serializer와 같은 `type` 우선, `label` fallback 순서로 셉니다. 이 세 software fallback의
  Scene node도 serializer의 record planner를 공유해 missing/colliding ID, `label`/`name` alias와 endpoint를
  실제 방출 결과와 같은 공간으로 정규화합니다. Use-case planner는 Actor와 UseCase의 최종 namespace를
  함께 할당해 prefix 뒤의 2차 collision도 suffix로 분리합니다. serializer가 소비하지 않는 raw
  `text`/`role`/`shape`/style/semantic metadata와 relation ID는 의미 구조로 승격하지 않으며, node와 relation
  수가 Scene budget을 넘으면 serializer와 projection이 같은 경계에서 거부합니다.
  Requirement는 serializer와 같은 normalized/collision-safe output ID,
  requirement type·ID·text·risk·verification, element type·docref, relation type을 셉니다. 접근성 metadata와
  serializer가 무시한 대체 label은 포함하지 않습니다. Event Modeling은 lane label과 실제 fallback의
  time·frame type·label 조합 및 relation label, Wardley는 native title·component·link label, Cynefin은
  native 고정 template·실제 visible item(`confusion`은 세 개+`+N more`)·transition label, ZenUML은
  Sequence fallback의 participant alias·message label만 셉니다. 내부 endpoint ID, 좌표, anchor 같은 문법
  구조와 접근성 text는 OCR 의미 증거로 세지 않습니다. 각 유형의 record planning은 serializer와 projection이
  같은 deterministic helper를 공유합니다.
- Typed semantic projection이 malformed data나 adapter defect로 예외를 내면 해당 candidate의 OCR을
  direct-code fallback으로 바꾸지 않습니다. 예외를 candidate warning으로 격리하고 aggregate를
  unavailable로 유지하여 다른 candidate 선택과 문서 변환은 계속합니다.
- numeric consistency는 source/generated 숫자 occurrence multiset의 precision·recall F1입니다. Bounded
  evidence 안의 동일 normalized text+bbox는 한 관측으로 합치고, OCR context와 evidence 채널의 numeric
  Counter는 token별 최대 occurrence로 병합합니다. 따라서 위치가 다른 반복값은 보존하면서 채널 간 중복
  보고는 다시 세지 않습니다. 생성한 숫자가 source에 없거나 occurrence 수가 다르면 precision/recall을
  낮춥니다. Generated projection은 Mermaid `%%` comment를 제외하고, detected grammar가 지원할 때만
  `title ...`/`title: ...`, `accTitle: ...`, 한 줄 `accDescr: ...`와 block `accDescr { ... }`를 chart
  metadata로 제외합니다. Sankey의 metadata-like CSV label과 weight는 실제 data로 보존합니다. Quadrant의
  `quadrant-1`~`quadrant-4` slot index도 문법 토큰으로 제외하지만 directive label과 point 좌표 안의 실제
  숫자는 보존합니다. Block metadata 뒤 같은 줄의 statement는 다시 평가하며 bounded suffix budget이
  소진되면 부분 점수 대신 `0.0`으로 fail closed합니다.
- visual entailment precision은 생성된 node를 source node ID 또는 유일한 정규화 label로 정렬한
  evidence coverage proxy입니다. source scene 자체를 후보 precision으로 재사용하지 않습니다. model scorer는 후속입니다.
- 구조 edge를 평가할 수 없고 render PNG가 있으면 raster edge IoU를 fallback으로 사용합니다.

path enumeration은 기본 10,000개 completed path와 100,000개 탐색 state/stack에서 중단합니다. Terminal로
이어지지 않는 cyclic dead branch도 state budget을 소비하므로 simple-path 조합 폭발이 완료 path 수를 우회할
수 없습니다. Source 또는 generated graph가 path/state/depth budget을 넘으면 부분 결과로 점수를 만들지 않고
metric 전체를 unavailable로 둡니다.
표시용 total score와 별도로 non-runtime semantic score를 계산합니다. syntax/render는 hard gate와 total
score에는 참여하지만 0인 의미 점수를 게시 가능 등급으로 희석할 수 없습니다. `extended`/`maximal`의
구조 후보는 생성 node provenance가 80% 미만이거나 계산 불가능하면 review 대상으로 둡니다.
Packet도 이 구조 provenance gate에 포함되며, bit 숫자가 일치하는 것만으로
unattributed field를 자동 게시하지 않습니다.

`best_effort_validated`와 `strict_validated`에서 여러 parse/render 후보가 있으면 각 후보에 같은
aggregate·semantic threshold와 provenance/numeric hold를 적용한 뒤, publish 가능한 class를 먼저
선택합니다. 같은 class 안에서는 aggregate, OCR recall, generation method, candidate ID 순서를 유지합니다.
따라서 metric availability가 적은 높은 total 후보가 실제 게시 가능한 evidence-rich 대안을 가리고 문서
전체를 review 상태로 내리지 않습니다. 강제 review/sidecar 정책은 이 class 우선순위를 사용하지 않습니다.
Typed/Scene 후보의 numeric hold는 fallback grammar와 무관하게 semantic type을 유지합니다. Direct 후보는
typed semantic contract가 없으므로 prediction/requested type 대신 parse/render validation으로 확인한
emitted/runtime grammar type을 기준으로 결정합니다.

Semantic repair 후보도 초기 후보와 같은 reference text 집합과 평가 함수를 사용합니다. OCR/vector,
provenance, edge, arrow, layout, path, numeric gate를 새 typed IR에서 다시 계산하며 aggregate 엄격 개선과
semantic score 비감소를 동시에 요구합니다. Held aggregate를 repair가 임의로 해제하지 않습니다. 방향
반전과 무라벨 누락 edge proposal은 source relation confidence 0.6, 내장 Geometry engine이 생성한 exact
endpoint/relation ownership, ID 충돌이 없는 bbox/score 0.6 이상의 line/arrow evidence, 동일 source block
attribution을 모두 요구합니다. 이 threshold는 기본 detector의 line 0.6/arrow 0.65 범위를 포함하되 engine
identity와 geometry relation 일치를 별도 gate로 둡니다. VLM이 새로 선언한 connector evidence와 약한 것을
포함한 engine 간 방향 충돌, 상충·병렬·라벨·conditional relation과 decision/gateway/diamond source의
outgoing edge는 자동 topology repair에서 제외됩니다.
Label repair도 trusted Marker OCR/built-in Vector origin, source block, bbox containment, ID collision gate를
통과해야 합니다. Proposal typed IR은 입력과 같은 resource budget을 다시 통과하고 code가 deterministic
재직렬화 결과와 정확히 일치해야 평가 단계로 진입합니다.

Typed/Scene semantic type 또는 direct 후보의 validated emitted/runtime type이
Gantt/Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn/Packet이면 OCR/vector numeric evidence가 하나도 없거나
numeric consistency가 게시 threshold보다 낮을 때 aggregate를 `None`으로 두어 자동 게시하지 않습니다.
