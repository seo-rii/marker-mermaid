# Typed extraction 계약

Structured VLM은 `diagram_type`만 맞춘 임의 JSON을 내보내지 않습니다. 활성화된 Mermaid 유형마다
root 필드와 container 종류를 고정한 `TypedIRContract`를 prompt로 받고, 응답은 Pydantic 모델 생성
시점에 같은 registry로 다시 검사됩니다. Phase 1 유형은 record 내부의 알려진 필드와 recursive container도
전용 Pydantic model로 검사합니다. serializer의 세부 의미 검사는 그 다음 단계에서 수행합니다.

이 경계는 두 문제를 분리합니다.

- extraction contract는 `sequence` 후보에 `nodes`가 들어오는 식의 유형 간 root 혼동을 빠르게 거부합니다.
- serializer는 endpoint, cardinality, 숫자 근거, 날짜, 허용 문법처럼 유형 내부 의미를 검증합니다.

registry는 `ALL_TYPES`와 정확히 같은 key 집합이어야 하며 누락 또는 여분이 있으면 import 단계에서
실패합니다. Prompt에는 현재 `enabled_types`만 들어가므로 비활성 유형의 schema가 token budget을
소비하지 않습니다. 공통 선택 필드는 `title`, `description`, `acc_title`, `acc_description`,
`direction`이며 semantic node/relation record에는 prior에서 얻은 `evidence_ids`를 요구합니다.

## Phase 1 중첩 계약

다음 유형은 root container 검사 뒤 strict nested model을 통과해야 합니다.

| 유형 | 검사하는 record 구조 |
| --- | --- |
| Flowchart / Generic Network | node, edge, group과 member/evidence list |
| Swimlane / BPMN | lane, lane 안의 node, top-level edge |
| Sequence | string 또는 object participant, message |
| Mindmap | 재귀 root/children hierarchy |
| Timeline | event와 여러 label을 담는 `events: string[]` |
| Gantt | section과 task/date/duration field |
| Architecture | service, group, edge/port field |

알려진 scalar field에는 object/list를 넣을 수 없고, record와 child container의 종류도 고정합니다. `bbox`는
정확히 네 개의 finite number, `evidence_ids`와 membership은 string list여야 합니다. `extra="allow"`를
사용하므로 style, geometry, plugin 또는 향후 Mermaid field 같은 미등록 metadata는 삭제하지 않습니다.
검증 결과 model은 원본 IR을 대체하지 않습니다. 기존 dict를 그대로 serializer, repair, canonical hash,
sidecar에 전달하므로 coercion이나 field stripping이 일어나지 않습니다.

Prompt의 record 목록은 각 serializer가 실제 출력하는 canonical field만 광고합니다. Nested model은 보존할
style/compatibility metadata의 알려진 type도 추가로 검사할 수 있으므로 Pydantic field 집합과 prompt 문자열을
기계적으로 동일하게 만들지는 않습니다. 예를 들어 Architecture `name`과 relation `label`, Sequence participant
`text`, Flow edge의 raw arrow hint처럼 compatibility metadata의 type도 검사합니다. Architecture `name`은
`label`의 serializer-visible alias지만 relation `label`, Sequence participant `text`, raw arrow hint는 원
IR/sidecar에 남아도 node/relation label 또는 구조 방향으로 평가하지 않습니다. 접근성 description에 보존된
metadata도 OCR 구조 점수에서는 제외합니다.

이 경계는 구조를 확인할 뿐 의미를 추측하지 않습니다. 빈 후보, 읽을 수 없는 label, 누락 label처럼 부분
복원 placeholder로 처리할 수 있는 입력은 허용합니다. non-empty 조건, ID uniqueness, endpoint/group reference,
Gantt 날짜와 Mermaid 표현 가능성은 serializer 및 evaluation gate가 계속 판정합니다. Architecture port는
nested contract에서 `L/R/T/B`만 허용합니다.
`evidence_ids`도 prompt에서는 필수지만 legacy/partial candidate 호환을 위해 model에서는 선택 사항이며,
실제 자동 게시 여부는 provenance gate가 결정합니다.

평가 Scene은 serializer-visible fallback을 그대로 사용합니다. label이 없는 Flowchart/Generic Network,
Swimlane/BPMN, Mindmap node는 내부 ID가 아니라 `[unreadable]`로 기록합니다. Sequence participant는 serializer와
공유하는 planner가 portable ID 충돌에 suffix를 붙이고, 같은 logical ID가 중복되면 모호한 message mapping을
거부합니다. 같은 planner가 message container/record 형태와 Scene relation 예산을 검사한 뒤 실제로 해석되는
message만 serializer와 Scene에 함께 전달하고, raw message ID와 무관한 고유 emitted relation ID를 순서대로
부여합니다. 따라서 Mermaid에서 합쳐진 actor나 생략된 message를 평가 Scene이 별도 구조로 세지 않습니다.

현재 Marker `response_schema`의 외부 envelope는 여전히 `TypedIRCandidate.ir: dict`입니다. 따라서 이 단계는
prompt와 응답 후 검증을 중첩 구조까지 강화하지만, 모든 Mermaid 유형을 하나의 discriminated JSON Schema로
직접 노출하지는 않습니다. 나머지 유형의 전용 model과 envelope-level discriminated schema는 후속 작업입니다.

Marker 1.10.2의 stock Ollama service는 원래 schema의 최상위 `properties`와 `required`만 복사해 `$defs`를
버립니다. 이 adapter를 감지하면 local `#/$defs/*` 참조를 재귀적으로 inline한 schema-only
`EngineObservation` subclass를 전달합니다. 외부·재귀·sibling reference와 65,536자 초과 schema는 거부하고,
응답은 provider 종류와 무관하게 원래 `EngineObservation`으로 다시 정규 검증합니다.

## Prompt 선택 경계

Marker service 호출 전 provider-visible text에는 별도 문자 예산을 적용합니다. system instruction, 활성
type 계약, view manifest, 빈 selection section과 Marker 1.10.2 canonical response-schema reserve만으로
예산을 넘으면 provider를 호출하지 않습니다. user edit/trusted connector 뒤 남은 evidence slot의 최소
25%는 arrow/line/contour/vector에 round-robin으로 예약하고, trusted label 및 전역 우선순위로 남은 slot을
backfill합니다. Evidence/OCR root container는 exact plain list여야 하며, 한 번 만든 bounded shallow
snapshot을 preflight와 canonical selection에 공통 사용합니다. canonical copy 전 evidence 문자열 합계에는
8,000,000자 hard cap을 적용합니다. 문자 예산에 맞지 않는 큰 record는 JSON escape 길이를 allocation 없이
계산해 전체 직렬화 전에 건너뜁니다.
설정된 item 상한으로 자른 OCR prefix에도 plain-string 및 8,000,000자 aggregate preflight를 적용합니다.
남은 prompt보다 raw string lower bound가 큰 OCR은 escape scan 없이 건너뜁니다. 선택된 evidence와 OCR
text는 완전한 compact JSON item으로만 추가합니다.

각 exact `VisualEvidence`의 scalar와 nested source-block list도 mutable 입력으로 취급합니다. Nested list는
reference 상한보다 하나 많은 항목까지만 snapshot하고, bbox/score의 shape·type·finite 값과 모든 문자열의
type·길이·UTF-8을 `model_dump()` 전에 확인합니다. 검증 뒤에는 이 field snapshot으로 만든 payload만
canonicalize합니다. Trusted label/connector set도 같은 방식으로 bounded immutable snapshot을 만들어
selection 전체에서 재사용합니다.

Selection manifest는 입력/검사/포함 수, schema reserve와 선택 profile을 prompt에 기록합니다. 누락은
candidate warning에도 표시하지만, 후보가 없는 prediction-only 응답에서도 사라지지 않는 source of truth는
`ReconstructionResult.prompt_budget_notices`입니다. sidecar `manifest.json`과 Marker internal metadata가
같은 구조화 notice를 보존합니다. 입력 `SourceContext`의 evidence/OCR 배열은 재정렬하거나 수정하지
않습니다. 이 경계는 provider 응답 token limit, image encoding, SDK 내부 wire overhead와는 별개인 bounded
text request 계약입니다.

`flowchart`와 `generic_network` prompt에는 더 좁은 identity 계약이 있습니다. typed `nodes[].id`는 같은
VLM 응답에서 대응하는 `scene_ir.elements[].id`를 byte-for-byte 재사용해야 하며 rename, normalize 또는
새 ID 생성은 허용하지 않습니다. 각 semantic typed node의 `evidence_ids`도 prompt에 전달한 `Prior
evidence`의 ID를 인용하고 대응하는 same-response Scene element와 최소 하나를 공유해야 하며, 응답이
스스로 만든 evidence ID는 근거가 아닙니다. Pipeline은 각 engine 호출 직전의 비충돌 evidence payload를
실제 prompt-selected private ID 집합과 교차하고, 뒤늦게 선언되거나 충돌한 ID를 제외합니다. 이 private
집합과 prompt notice는 response schema에 없으며 provider payload로 설정할 수 없습니다. Prompt 준수만으로
신뢰하지 않고 fusion에서 prior payload의 bbox/text와 same-owner Scene 연결, 독립 vector/geometry node의
unique IoU 대응, authority observation이 직접 선언한 spatially aligned contour provenance를 다시 검사합니다.

## 입력 budget

VLM/fixture 입력은 신뢰하지 않습니다. typed IR은 깊이 64, 전체 item 100,000개, 문자열 필드
50,000자로 제한합니다. observation candidate, evidence, warning 수와 Scene IR element/relation/group,
polygon/polyline, ID, bbox도 별도 상한과 finite-number 검사를 거칩니다. `NaN`/무한 좌표와 범위를
벗어난 confidence는 sidecar에 도달하기 전에 거부됩니다. JSON sidecar는 `allow_nan=false`로
직렬화합니다.
Canonical candidate key는 string-key object, list/tuple, finite number, boolean, string, null만
받습니다. set, bytes 같은 비결정적·비 JSON 값도 dedup이나 private mapping lookup에 들어가기 전에
거부하며 tuple은 canonical JSON array로 정규화합니다.
Flowchart/Generic Network record의 ID·label·endpoint 같은 알려진 scalar field에는 object/list를 넣을 수
없습니다. 또한 repair나 plugin이 모델 생성 뒤 mutable IR를 바꿀 수 있으므로 canonical key 계산과 fusion
입력 경계에서 현재 payload를 다시 Pydantic/typed-contract validation합니다. 변조된 후보 하나는 fusion
warning과 함께 제외되며 다른 후보나 문서를 실패시키지 않습니다.
이 재검증은 fusion 후보에만 한정되지 않습니다. Pipeline은 initial evidence와 각 engine 응답을 받은
직후 Scene IR, typed/direct candidate, evidence를 현재 payload로 각각 다시 모델 검증하고, invalid component만
`CandidateFailure`로 격리한 sanitized observation을 이후 original/fusion 경로 모두에서 사용합니다.
따라서 validation 뒤 mutation된 original observation이 fusion fallback을 우회해 게시되지 않습니다.

## 평가 Scene adapter

Typed IR serializer가 만든 결과를 provenance 및 구조 점수에 사용할 때는 `candidate_scene.py`가 실제
방출 구조를 `DiagramSceneIR`로 바꿉니다. Flow/UML/architecture/chart 외에도 sequence/ZenUML,
mindmap/treemap/tree/organization, timeline/journey/Kanban, event modeling, Ishikawa, Wardley,
data lineage, Venn adapter가 있습니다. Adapter가 없는 유형은 구조를 추측하지 않고 metric을
`unavailable`로 둡니다.

계층 child, Kanban column-card, Venn set-intersection처럼 serializer가 암시하는 관계는 deterministic
containment relation으로 만들되, node 및 relation의 evidence ID는 typed IR에서 그대로 보존합니다.
따라서 `extended`의 generated-node attribution gate는 원 Scene을 재사용하지 않고 실제 후보 구조를
기준으로 판단합니다.

Flow node ID 정합화가 성공한 후보는 adapter에 들어오기 전에 fused Scene ID를 사용합니다. 정합화는
`flowchart`/`generic_network`의 `nodes[].id`, `edges[].source`/`target`, `groups[].member_ids`에만
적용하며 모든 node가 대응하는 full/injective mapping일 때만 후보 전체를 원자적으로 바꿉니다. 하나라도
모호하거나 dangling/colliding reference가 있으면 아무 field도 바꾸지 않습니다. `candidate_scene.py`는
이렇게 확정된 serializer-visible 구조를 평가 Scene으로 옮길 뿐, ID mapping authority를 만들지 않습니다.
이미 모든 typed node ID가 fused Scene ID와 동일한 후보는 ID remap이나 mapping sidecar를 만들지 않고
기존 attribution/publication gate로 평가합니다. 이 identity-only 경로는 prompt 준수를 신뢰 근거로
승격하지 않습니다.

Swimlane/BPMN의 nested lane/subgraph, hierarchy child, software/chart/planning/special typed IR와 direct
Mermaid는 현재 이 ID 정합화를 지원하지 않습니다. 이 유형들의 adapter가 존재한다는 사실은 nested
reference를 안전하게 다시 쓸 수 있다는 뜻이 아닙니다.

새 유형을 추가할 때는 `ALL_TYPES`, `TYPED_IR_CONTRACTS`, serializer, 해당 serializer의 평가 Scene
adapter 및 contract/serialization test를 함께 갱신해야 합니다.
