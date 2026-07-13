# Typed extraction 계약

Structured VLM은 `diagram_type`만 맞춘 임의 JSON을 내보내지 않습니다. 활성화된 Mermaid 유형마다
root 필드와 container 종류를 고정한 `TypedIRContract`를 prompt로 받고, 응답은 Pydantic 모델 생성
시점에 같은 registry로 다시 검사됩니다. serializer의 세부 의미 검사는 그 다음 단계에서 수행합니다.

이 경계는 두 문제를 분리합니다.

- extraction contract는 `sequence` 후보에 `nodes`가 들어오는 식의 유형 간 root 혼동을 빠르게 거부합니다.
- serializer는 endpoint, cardinality, 숫자 근거, 날짜, 허용 문법처럼 유형 내부 의미를 검증합니다.

registry는 `ALL_TYPES`와 정확히 같은 key 집합이어야 하며 누락 또는 여분이 있으면 import 단계에서
실패합니다. Prompt에는 현재 `enabled_types`만 들어가므로 비활성 유형의 schema가 token budget을
소비하지 않습니다. 공통 선택 필드는 `title`, `description`, `acc_title`, `acc_description`,
`direction`이며 semantic node/relation record에는 prior에서 얻은 `evidence_ids`를 요구합니다.

`flowchart`와 `generic_network` prompt에는 더 좁은 identity 계약이 있습니다. typed `nodes[].id`는 같은
VLM 응답에서 대응하는 `scene_ir.elements[].id`를 byte-for-byte 재사용해야 하며 rename, normalize 또는
새 ID 생성은 허용하지 않습니다. 각 semantic typed node의 `evidence_ids`도 prompt에 전달한 `Prior
evidence`의 ID를 인용하고 대응하는 same-response Scene element와 최소 하나를 공유해야 하며, 응답이
스스로 만든 evidence ID는 근거가 아닙니다. Pipeline은 각 engine 호출 직전의 evidence ID와 payload
snapshot을 보존하고 뒤늦게 선언되거나 충돌한 ID를 제외합니다. Prompt 준수만으로 신뢰하지 않고
fusion에서 prior payload의 bbox/text와 same-owner Scene 연결, 독립 vector/geometry node의 unique IoU
대응, authority observation이 직접 선언한 spatially aligned contour provenance를 다시 검사합니다.

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
