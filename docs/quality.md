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

typed IR은 serializer가 실제 방출하는 node/edge 구조로 다시 변환합니다. bbox가 IR에 명시되지 않으면
layout을 추측하지 않습니다. Scene IR portable fallback은 deterministic serializer 보존 여부를 평가할 수
있습니다. raw/direct Mermaid는 아직 일반 AST→Scene 변환이 없으므로 구조 점수가 unavailable일 수 있습니다.
평가 Scene adapter는 sequence/ZenUML, hierarchy/organization, planning/event, Ishikawa, Wardley,
data-lineage, Venn까지 포함하며 typed record의 evidence ID를 보존합니다.
Event Modeling의 generated Scene은 fallback serializer와 같은 normalized frame ID, typed/time label,
lane subgraph membership, `LR` 방향을 사용합니다. Wardley의 label 없는 component와 ZenUML의 label 없는
participant도 임의 `text`가 아니라 serializer가 실제 표시하는 safe source ID를 사용합니다.

## 기존 metric과 결합

- syntax/render는 게시 hard gate이면서 score input입니다.
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
  따릅니다. C4는 architecture fallback에 남는 boundary와 service label만 세고 technology, relation
  label, description은 제외합니다. Requirement는 serializer와 같은 normalized/collision-safe output ID,
  requirement type·ID·text·risk·verification, element type·docref, relation type을 셉니다. 접근성 metadata와
  serializer가 무시한 대체 label은 포함하지 않습니다. Event Modeling은 lane label과 실제 fallback의
  time·frame type·label 조합 및 relation label, Wardley는 native title·component·link label, ZenUML은
  Sequence fallback의 participant alias·message label만 셉니다. 내부 endpoint ID, 좌표, anchor 같은 문법
  구조와 접근성 text는 OCR 의미 증거로 세지 않습니다. 각 유형의 record planning은 serializer와 projection이
  같은 deterministic helper를 공유합니다.
- Typed semantic projection이 malformed data나 adapter defect로 예외를 내면 해당 candidate의 OCR을
  direct-code fallback으로 바꾸지 않습니다. 예외를 candidate warning으로 격리하고 aggregate를
  unavailable로 유지하여 다른 candidate 선택과 문서 변환은 계속합니다.
- numeric consistency는 source/generated 숫자 multiset의 precision·recall F1입니다. source에 실제
  숫자가 있을 때만 사용하며 추가 생성한 숫자도 precision을 낮춥니다. `accTitle`/`accDescr`/title
  metadata 안의 숫자는 chart data multiset에서 제외합니다.
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

`best_effort_validated`와 `strict_validated`에서 여러 parse/render 후보가 있으면 각 후보에 같은
aggregate·semantic threshold와 provenance/numeric hold를 적용한 뒤, publish 가능한 class를 먼저
선택합니다. 같은 class 안에서는 aggregate, OCR recall, generation method, candidate ID 순서를 유지합니다.
따라서 metric availability가 적은 높은 total 후보가 실제 게시 가능한 evidence-rich 대안을 가리고 문서
전체를 review 상태로 내리지 않습니다. 강제 review/sidecar 정책은 이 class 우선순위를 사용하지 않습니다.

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

Gantt/Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn/Packet 후보는 OCR/vector numeric evidence가 하나도
없거나 numeric consistency가 게시 threshold보다 낮으면 aggregate를 `None`으로 두어 자동 게시하지 않습니다.
