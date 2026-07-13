# Evidence-backed semantic repair

Marker 기본 pipeline은 typed Flowchart/Generic Network의 label과 명확한 directed relation에 한해
deterministic semantic repair를 시도합니다. 이는 자유 형식 LLM self-correction이 아니라 원본 evidence와
공통 품질 평가가 동시에 지지하는 좁은 enrichment입니다.

교정은 다음 조건을 모두 만족해야 합니다.

- 후보가 typed IR에서 생성된 Flowchart 또는 Generic Network이다.
- source Scene과 typed node가 exact ID로 일치한다.
- source Scene label이 비어 있지 않고 현재 typed label과 실제로 다르다.
- source element가 참조한 `vector_text`, 또는 score 0.8 이상의 `ocr_token` text가 source label과
  NFKC/casefold 정규화 후 정확히 일치한다.
- text evidence가 초기 Marker OCR 또는 exact built-in `VectorPrimitiveEngine`에서 왔고 ID 충돌이 없다.
- evidence bbox 중심이 source node bbox 안에 있으며 현재 source block ID를 공유한다. VLM이 새로 선언한
  OCR/vector evidence는 label repair 권한이 없다.

충족한 label을 한 번에 모두 교정하며 node ID와 node 수는 바꾸지 않습니다. Supporting evidence ID를
typed node에 추가하고 accessibility description도 generated text였던 경우에만 다시 계산합니다. 명시적으로
작성된 accessibility text는 유지합니다.

방향 반전 또는 누락 edge 추가는 다음 조건을 추가로 모두 만족해야 합니다.

- source relation이 서로 다른 exact node ID를 연결하고 confidence 0.6 이상인 단방향 relation이다.
- 내장 `GeometryEngine`이 생성한 directed relation의 endpoint와 connector evidence 집합이 source relation과
  일치한다. ID 충돌이 없는 `line_segment`와 `arrowhead`는 각각 bbox와 score 0.6 이상을 가져야 한다.
  이 하한은 기본 Hough line 0.6/arrowhead 0.65 신호가 실제 경로에 참여하게 하면서 engine identity와
  relation geometry를 별도 hard gate로 둡니다. VLM이 새로 선언한 connector evidence는 repair 권한이 없다.
- 두 evidence가 현재 source의 동일한 Marker block ID를 공유한다.
- 같은 unordered endpoint pair에 상충하거나 병렬인 source relation이 없다.
- fusion 전 engine observation 사이에도 방향/arrow 상태 충돌이 없고 trusted Geometry pair가 정확히 하나다.
- 방향 반전은 typed IR에 반대 방향 무라벨 edge가 정확히 하나 있고 양방향 edge가 아닐 때만 수행한다.
- 누락 edge 추가는 어느 방향 edge도 없고 source relation label이 없을 때만 수행한다.

방향 반전은 기존 style과 그 밖의 edge metadata를 유지하고 connector evidence ID를 추가합니다. 누락
edge는 source의 relation/semantic type만 복사하며 deterministic `repair_edge_N` ID를 사용합니다. 라벨이 있는
누락 branch, conditional/decision/gateway relation, decision/gateway/diamond source node의 outgoing edge,
ambiguous/parallel relation, self-loop, dangling endpoint, malformed IR은 자동 수정하지 않습니다.
이미 style recovery를 채택한 후보는 재직렬화가 style을 버릴 수 있으므로 기본 semantic repair에서
제외합니다.

## 채택 gate

Repair proposal은 code와 갱신된 typed IR을 함께 반환합니다. Pipeline은 typed IR을 deterministic serializer로
다시 입력과 동일한 depth/item/text budget으로 검증한 후 직렬화해 proposal code와 byte-for-byte 일치하고
emitted type이 유지되는지 먼저 확인합니다. 그 뒤 수정
code를 다시 security scan/parse/render합니다. 초기 후보와 같은 평가 함수로 OCR,
numeric, provenance, edge, arrow, layout, path, type fitness를 모두 다시 계산합니다. 다음 조건을 모두
만족할 때만 채택합니다.

- 이전 aggregate와 새 aggregate가 모두 평가 가능하다.
- aggregate가 epsilon보다 크게 엄격 개선된다.
- non-runtime semantic score가 감소하지 않는다.
- 기존 numeric/provenance publication gate를 그대로 통과한다.

따라서 `aggregate_score=None`인 held candidate를 semantic repair만으로 게시 가능하게 만들 수 없습니다. 원본
baseline candidate는 변경하지 않고 alternative에 남으며 repair candidate에는 구조화된 correction,
before/after score, 채택 여부가 기록됩니다.

현재 기본 repair는 label, 강한 line/arrow evidence가 있는 반전 edge, 무라벨 누락 edge만 다룹니다. 누락
node, labeled/conditional branch, 병렬 relation, layout, raw Mermaid 수정은 code와 typed IR을 동시에 안전하게
갱신할 더 넓은 AST/semantic patch 계층이 마련되기 전까지 자동 수행하지 않습니다.
