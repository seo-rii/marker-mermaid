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

## 입력 budget

VLM/fixture 입력은 신뢰하지 않습니다. typed IR은 깊이 64, 전체 item 100,000개, 문자열 필드
50,000자로 제한합니다. observation candidate, evidence, warning 수와 Scene IR element/relation/group,
polygon/polyline, ID, bbox도 별도 상한과 finite-number 검사를 거칩니다. `NaN`/무한 좌표와 범위를
벗어난 confidence는 sidecar에 도달하기 전에 거부됩니다. JSON sidecar는 `allow_nan=false`로
직렬화합니다.

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

새 유형을 추가할 때는 `ALL_TYPES`, `TYPED_IR_CONTRACTS`, serializer, 해당 serializer의 평가 Scene
adapter 및 contract/serialization test를 함께 갱신해야 합니다.
