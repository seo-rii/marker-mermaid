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

node 정렬은 같은 ID를 먼저 사용하고, 그다음 중복되지 않는 NFKC/casefold label만 사용합니다. geometry로
node를 맞추지 않으므로 layout metric이 자신의 가정을 검증하는 순환을 피합니다. edge topology는 방향을
무시하고 방향 오류는 arrow metric이 별도로 측정합니다.

typed IR은 serializer가 실제 방출하는 node/edge 구조로 다시 변환합니다. bbox가 IR에 명시되지 않으면
layout을 추측하지 않습니다. Scene IR portable fallback은 deterministic serializer 보존 여부를 평가할 수
있습니다. raw/direct Mermaid는 아직 일반 AST→Scene 변환이 없으므로 구조 점수가 unavailable일 수 있습니다.

## 기존 metric과 결합

- syntax/render는 게시 hard gate이면서 score input입니다.
- OCR recall은 원 OCR token coverage입니다.
- numeric consistency는 source/generated 숫자 multiset의 precision·recall F1입니다. source에 실제
  숫자가 있을 때만 사용하며 추가 생성한 숫자도 precision을 낮춥니다.
- visual entailment precision은 현재 evidence coverage proxy이며 model scorer는 후속입니다.
- 구조 edge를 평가할 수 없고 render PNG가 있으면 raster edge IoU를 fallback으로 사용합니다.

path enumeration은 기본 10,000개 path에서 중단합니다. budget을 넘은 부분 결과로 점수를 만들지 않습니다.
Gantt/Pie/XY/Quadrant/Sankey/Radar/Treemap/Packet 후보는 OCR/vector numeric evidence가 하나도 없으면
aggregate를 `None`으로 두어 자동 게시하지 않습니다.
