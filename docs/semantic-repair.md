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

조건 분기 relation의 label은 다음 이중 gate를 모두 통과한 **기존 edge**에만 추가하거나 교정합니다.

- typed edge label이 비어 있거나 source label과의 NFKC/casefold 문자열 유사도가 0.60 이상인 오타 후보여야
  합니다. 이미 존재하는 의미가 다른 label은 자동으로 덮어쓰지 않습니다.
- source Scene의 conditional/branch/decision/gateway relation과 typed IR edge가 같은 exact source/target을
  가지며, 각 unordered endpoint pair에 source relation과 typed edge가 하나씩만 존재해야 합니다. Typed edge는
  이미 source와 같은 방향인 단방향 edge여야 합니다.
- ID 충돌과 engine 간 방향 충돌이 없는 built-in `GeometryEngine` relation 하나가 같은 endpoint와 방향을
  지지해야 합니다. score 0.6 이상의 trusted `line_segment`와 `arrowhead`가 relation에 연결되고 현재 source의
  같은 block을 공유해야 합니다.
- source relation label과 NFKC/casefold 정규화 후 정확히 일치하는 trusted `vector_text`, 또는 score 0.8
  이상의 trusted `ocr_token`이 relation에 직접 연결되어야 합니다. Text evidence는 connector와 같은 block에
  있어야 하며 다른 source relation과 evidence ID를 공유하지 않아야 합니다. 양의 면적을 가진 text bbox의
  중심은 node 내부가 아니어야 하고, bbox의 짧은 변 길이의 2배 이내에서 source polyline과 확장된 trusted
  line bbox 양쪽에 모두 근접해야 합니다. 같은 중심이 다른 trusted connector의 두 corridor에도 동시에
  들어가면 어느 edge의 label인지 추측하지 않고 거부합니다.

이 repair는 typed edge의 `label`과 해당 text/connector evidence attribution만 갱신합니다. Node, endpoint,
방향, relation 수와 source Scene은 변경하지 않으며 before/after label과 두 evidence 집합을 repair history에
기록합니다. 기존 edge가 reverse 방향이거나 병렬·양방향이면 label과 방향을 한 번에 추측하지 않습니다.

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
누락 branch, conditional/decision/gateway topology, decision/gateway/diamond source node의 새 outgoing edge,
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

현재 기본 repair는 node label, 이중 text/connector gate를 통과한 기존 조건 분기 edge의 label-only 교정,
강한 line/arrow evidence가 있는 반전 edge와 무라벨 누락 edge만 다룹니다. 누락 node, 조건 분기 topology,
endpoint·방향 변경, 새 branch 생성, Yes/No 의미 추론, 병렬 relation, layout, raw Mermaid 수정은 code와 typed
IR을 동시에 안전하게 갱신할 더 넓은 AST/semantic patch 계층이 마련되기 전까지 자동 수행하지 않습니다.
또한 typed node ID와 fused source Scene node ID가 exact match하지 않으면 공간만으로 ID를 추정하지 않고
repair를 건너뜁니다. 서로 다른 engine의 node ID remapping은 별도 fusion 과제로 남아 있습니다.
