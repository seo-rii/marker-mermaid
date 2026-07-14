# Typed serializer와 fallback 계약

`SerializationResult`는 Mermaid 문자열만 반환하지 않고 다음을 함께 기록합니다.

- `requested_type`: classifier/typed IR이 요청한 의미 type
- `emitted_type`: serializer가 실제 사용한 Mermaid grammar
- `fallback_chain`: requested에서 emitted까지의 전체 경로
- `warnings`: 표현 손실 또는 parser/compatibility 제한
- `stability`: stable, extended, experimental

native 결과는 한 항목 chain만 허용합니다. fallback은 requested로 시작하고 emitted로 끝나야 하며
warning이 필수입니다. cycle, 빈 code, 중복 chain, 잘못 보고한 result serializer는 dispatch 단계에서
거부합니다. 기존 문자열 serializer는 `SerializationRegistry`가 같은 계약으로 감쌉니다.

## 현재 type mapping

| 요청 type | 실제 grammar | 비고 |
| --- | --- | --- |
| Flowchart, Sequence, Mindmap, Timeline, Gantt | 동일 | Phase 1 native |
| Architecture | `architecture → flowchart` | `architecture-beta` 우선, runtime 거부 시 nested Flowchart |
| State, Class, ER | 동일 | node/relation/member/attribute evidence 필수 |
| Requirement | `requirement` | Mermaid `requirementDiagram` |
| Block | `block` | Mermaid 11.16은 이 grammar의 accTitle/accDescr를 거부하여 접근성 text를 typed IR에 보존 |
| Swimlane | `flowchart` | subgraph fallback |
| BPMN | `bpmn → swimlane → flowchart` | BPMN 전용 notation 손실 |
| Generic Network | `flowchart` | portable node/edge 표현 |
| C4 | `c4 → architecture → flowchart` | native C4 SVG가 strict gate의 data/xlink 정책과 불일치하며 Architecture runtime 거부 시 nested fallback |
| Deployment, Component | `deployment/component → architecture → flowchart` | primary/secondary record를 service로 평탄화; Architecture runtime 거부 시 nested fallback, 특수 notation/relation label은 typed IR에 유지 |
| Use-case | `flowchart` | stadium actor/round use-case proxy와 typed relation label; actor glyph, system boundary, group/style/bidirectional metadata는 typed IR에 유지 |
| Pie, XY, Quadrant | 동일 | explicit finite values/axis/coordinates 필수 |
| Sankey | `sankey` 또는 `flowchart` | native-safe positive DAG, 그 외 exact-weight fallback |
| Radar | `radar` 또는 `flowchart` | non-negative native domain, 음수 domain은 tabular fallback |
| Treemap | `treemap` 또는 `flowchart` | leaf value 필수, internal-node value는 fallback에서 보존 |
| Venn | `venn` 또는 `flowchart` | 모든 크기가 관측되면 native, 누락 시 숫자 합성 없는 set graph |
| Journey | `timeline` | strict SVG에서 금지된 `foreignObject`를 피하고 score/actor를 event text로 보존 |
| Kanban, GitGraph | 동일 | card/branch/commit/merge ID와 reference evidence 필수 |
| Packet | `packet` 또는 `flowchart` | 명시적 contiguous bit range만 native; gap을 임의 field로 채우지 않음 |
| Ishikawa, TreeView | 동일 | cycle/duplicate ID/depth를 검증한 hierarchy |
| Event Modeling | `flowchart` | Mermaid 11.16 renderer 불안정으로 lane-aware fallback |
| Wardley, Cynefin, Railroad | 동일 | 좌표/domain/rule AST evidence가 완전할 때 experimental native |
| ZenUML | `sequence` | pinned runtime에 ZenUML extension이 없어 명시적 fallback |
| Organization | `treeview` | reporting hierarchy 보존, organization 전용 notation 없음 |
| Data Lineage | `flowchart` | dataset/process endpoint를 모두 확인한 portable graph |

State/Class/ER serializer는 provenance 없는 구조를 문법적으로 만들 수 있어도 거부합니다. unknown
endpoint, 추측 cardinality, ER의 identifying flag 누락도 `SerializationError`입니다. Requirement/Block과
fallback serializer 역시 unknown relation endpoint를 임의 node로 만들지 않습니다.

Requirement·Block은 serializer 전에 strict nested extraction 계약을 통과합니다. Requirement의
requirement/element/relation과 Block의 block/edge는 각각 object list여야 하고, 알려진 scalar,
`bbox`, `evidence_ids`의 형을 검사합니다. Requirement `type`·`risk`·verify method·relation
type과 Block `shape`는 serializer가 이미 수용하는 닫힌 token을 대소문자 구분 없이 검사하며,
원본 문자열은 재작성하지 않습니다. Legacy `verifymethod`도 같은 verify token으로 후처리
검증하지만 provider prompt에는 canonical `verify_method`만 보입니다.

Root list를 제외한 개별 field와 `evidence_ids`는 partial/legacy candidate 호환을 위해 model에서
선택으로 남겨 둡니다. 추가 metadata는 `extra="allow"`로 보존하고, validation result로 IR을
대체하지 않아 serializer와 sidecar에 원본 dict를 그대로 전달합니다. 따라서 non-empty,
필수 label, ID/endpoint 정합성과 provenance 게시 여부는 각각 serializer와 publication gate가
계속 판정합니다. Block `columns` 계약은 provider에 `auto|integer`를 요구하고 nested
validation에서 scalar 형만 확인하며, 누락/자동 배치와 양의 정수 조건은 serializer가 최종
판정합니다.

Flowchart `groups`는 명시적 `id`, label, non-empty `member_ids`가 있는 flat/disjoint group만 portable
`subgraph`로 방출합니다. 공용 emission plan이 source node/group ID를 한 번만 정규화하고 serializer와
generated Scene adapter가 emitted node/group/relation/member ID까지 같은 mapping으로 사용합니다. Unknown
member, duplicate source node, overlapping membership, nested intent, normalized node/group collision과 Scene
node/group/member/ID cap 초과와 non-scalar/oversized group label은 주석으로 손실시키거나 추측하지 않고
`SerializationError`로 거부합니다.
Group이 없을 때의 Mermaid output은 기존과 byte-identical합니다. Swimlane/BPMN도 lane을 같은 공용 plan
입력으로 준비해 한 번만 Flowchart serialization하며, missing node ID fallback과 bidirectional edge를 포함한
실제 subgraph membership을 generated `SceneGroup`에 보존합니다. Top-level `groups`는 lane topology에 섞지
않고 lane에서 nested intent를 발견하면 거부합니다. 이 단계는 group style을 복원하지 않습니다.

validation 이후 Mermaid runtime이 보고한 diagram type도 `runtime_diagram_type`에 저장합니다. deterministic
typed serializer의 declared emitted type과 runtime type이 다르면 render-valid 후보로 취급하지 않습니다.
direct Mermaid는 실제 runtime type으로 재분류하고 type-fitness를 0으로 두어 검토 경고를 유지합니다.

Architecture, C4, Deployment, Component의 typed 후보가 `architecture-beta`에서 runtime 거부되면 같은 typed
IR로 nested Flowchart를 한 번만 만들고 같은 candidate slot에서 다시 검증합니다. 이 재시도도 source security
scan, Mermaid parse/render, SVG 검사와 terminal runtime type 일치를 모두 통과해야 하며, 실패하면 해당
후보만 invalid로 남고 다른 후보 처리는 계속됩니다. 새 candidate를 만들지 않으므로 type/candidate/repair
budget은 증가하지 않습니다. 성공하면 requested type은 유지하되 `emitted_type`과
`runtime_diagram_type`은 `flowchart`가 되고, `fallback_chain`은 `architecture → flowchart` 또는
`c4|deployment|component → architecture → flowchart` 전체 경로를 기록합니다. 전환은 warning과
`runtime_portable_fallback` repair history에도 남습니다.

### C4 자동 후보와 진단용 native C4

C4 typed extraction은 `elements: list` root와 선택 `boundaries`·`relations`·`level`을 strict nested
contract로 검사합니다. Element의 canonical `kind` 14종과 root `level`(`context`, `container`,
`component`)은 대소문자를 구분하지 않고 검증하며, legacy `type`도 같은 kind 집합으로 검사한 뒤 원본
field와 casing을 유지합니다. Relation port는 Architecture가 소비하는 대문자 `L/R/T/B`만 허용하고
`bidirectional`은 strict boolean입니다. Boundary `type`은 string 형만 확인하고 닫힌 native token으로
제한하지 않습니다. 자동 fallback은 boundary notation을 방출하지 않으므로 진단용 native 문법의 제약으로
Architecture/Flowchart 후보를 불필요하게 거부하지 않기 위한 호환성 계약입니다.

각 element/boundary/relation field와 evidence는 partial reconstruction을 위해 선택이고, 공통 strict
`bbox`·string evidence list 및 `extra="allow"` 보존 규칙을 따릅니다. 검증 model은 입력 dict를
재작성하지 않습니다. Non-empty element, normalized ID collision, boundary reference/membership, endpoint와
Scene budget은 nested model이 추측하지 않고 아래의 공용 bounded C4-to-Architecture plan이 계속
판정합니다.

`serialize_c4_native`는 pinned Mermaid의 native macro를 확인하는 trusted diagnostic 경로이며 자동 C4
게시·평가의 구조 기준이 아닙니다. 자동 경로는 C4 element를 Architecture service로, boundary를 group으로,
relation을 unlabeled service edge로 변환한 뒤 Architecture native와 nested Flowchart가 공유하는 bounded
identity/group/topology plan을 사용합니다. Serializer는 이 plan의 kind 기반 icon과 relation port side를
Architecture output에 사용합니다. Generated Scene과 OCR projection도 같은 collision-safe emitted
service/group ID, 실제 표시 label, membership, endpoint 및 bidirectional connector를 사용합니다. 따라서
runtime이 Architecture를 받아들이거나 Flowchart로 재시도해도 평가 구조는 게시된 의미 표현과 같은 ID
공간에 있습니다.

원 element의 bbox/`evidence_ids`, relation evidence와 boundary bbox는 attribution을 위해 유지합니다.
relation polyline, technology, description, relation label, exact C4 boundary notation처럼 fallback이
표시하지 않는 raw metadata는 generated node/edge/group label이나 topology로 승격하지 않고 typed IR에만
남깁니다. 잘못된 형식이거나 reference 예산을 초과한 `evidence_ids`는 이전과 같은 Mermaid 출력을
유지하기 위해 attribution에서만 제외합니다. `reading_direction`도 runtime grammar를 추측하지 않고 IR
값 또는 `unknown`을 유지합니다. 이 손실은 기존 `c4 → architecture → flowchart` chain, limitation warning과
runtime fallback repair history에서
계속 공개됩니다. native C4가 표현할 수 있다는 이유만으로 자동 품질 점수를 높이거나 warning을 제거하지
않습니다. Service가 없는 C4 boundary는 Architecture group과 generated Scene에는 보존하지만 portable
Flowchart가 empty subgraph를 안전하게 표현하지 못하므로, 해당 nested retry는 기존처럼 후보 단위로
실패하고 다른 후보 처리를 계속합니다.

### Deployment·Component의 Architecture projection

Deployment와 Component도 자동 후보가 각각 `nodes`·`components` 필수 root와 내부 record를 strict nested
contract로 통과한 뒤 Architecture로 방출됩니다. Deployment `artifacts`와 Component `interfaces`는 별도
notation으로 그리지 않고 primary record 뒤에 평탄화된 generic service가 됩니다. `groups`는 service
`group` reference와 함께 공용 Architecture plan의 실제 group/membership으로 방출됩니다. 따라서 artifact나
interface의 ID와 `label`/`name`은 보이지만, artifact containment·stereotype과 provided/required interface
notation은 extra typed IR/review metadata에만 남습니다.

Deployment는 canonical `links`, Component는 canonical `dependencies`를 prompt에 사용합니다. 해당 key가
root에 있으면 빈 list여도 legacy `edges`를 병합하거나 대신 사용하지 않으며, canonical key가 없을 때만
`edges`를 compatibility alias로 읽습니다. Nested validation은 legacy edge record도 같은 형으로 검사하지만
원본 dict에 default key를 삽입하거나 field를 coercion하지 않습니다. Link/dependency label, raw relation ID와
bbox는 Mermaid에 방출하지 않고 typed IR에 보존합니다. Endpoint와 strict `bidirectional`, 대문자
`source_side`/`target_side`(`L/R/T/B`)가 unlabeled Architecture edge를 결정하며 evidence는 generated Scene
attribution에 유지됩니다.

Service-like icon은 open string입니다. Serializer가 지원하는 `cloud`·`database`·`disk`·`internet`·`server`는
대소문자를 정규화해 사용하고 알 수 없는 값은 `server`로 낮추므로, extraction contract가 fallback 가능한
후보를 icon enum만으로 거부하지 않습니다. Architecture output은 service/group icon과 connector port를
표시합니다. Runtime rejection 뒤 Flowchart retry는 같은 collision-safe service/group ID, label, membership,
무라벨 endpoint와 bidirectional topology를 유지하지만 icon과 port side는 표시하지 않습니다. Root
`direction`도 Architecture grammar에서는 무시되고 generated Scene과 Flowchart retry에서만 사용됩니다.

Known record/container/scalar 형, bbox/evidence, strict boolean과 port는 nested contract가 판정합니다. 결합된
service 목록의 non-empty 조건, normalized ID/group collision, unknown group/endpoint와 Scene resource cap은
기존 record ID planner와 Architecture structure plan이 계속 fail closed로 처리합니다. 빈 group은
Architecture에는 보존되지만 portable Flowchart가 안전하게 표현하지 못하므로 해당 runtime retry에서 후보
단위로 거부됩니다. Deployment/Component 전용 native serializer나 특수 notation을 복원했다는 뜻이 아닙니다.

### Use-case의 portable Flowchart projection

Use-case는 `actors: list`와 `use_cases: list`가 모두 필수이고 `relations`가 선택인 strict nested contract를
사용합니다. Actor/use-case record는 `id`·`label`·`name`·bbox/evidence, relation은
`id`·`source`·`target`·open string `type`/`label`·bbox/evidence의 known 형을 검사합니다. Model은 이
known field를 coercion하지 않고, partial record와 선택 evidence, 미등록 extra metadata 및 원본 dict를
그대로 보존합니다.

Serializer와 generated Scene은 `plan_usecase_fallback`의 같은 node/relation projection을 소비합니다. Actor와
use case는 하나의 collision-safe namespace를 공유하고 `label` → `name` → source ID 순서의 text를 사용합니다.
Actor는 stadium proxy, use case는 구별되는 round node이며 둘 다 Mermaid Flowchart shape입니다. 입력 group과
UML system boundary, actor glyph, raw role/shape/text metadata는 자동 구조에 넣지 않습니다. Serializer가
명시적으로 빈 `groups`를 사용하므로 unsupported group extra가 Scene에서 되살아나지도 않습니다.

Relation label은 닫힌 UML enum이 아니라 `type` 우선, 없으면 `label`을 사용하는 open text입니다. 둘 다
없으면 unlabeled edge가 됩니다. Raw relation ID, `bidirectional`, arrow/style/semantic metadata는 무시하고
일반 단방향 Flowchart connector만 방출합니다. Generated Scene도 같은 endpoint·label·순서와 deterministic
relation ID를 사용하고, node/relation evidence를 attribution에 유지합니다. Node bbox는 Scene source 위치이지
Mermaid layout 지시가 아니며 relation bbox는 typed IR/review metadata에만 남습니다.

Nested contract가 object/container와 known scalar/bbox/evidence 형을 확인한 뒤에도 두 root list의 non-empty,
Actor/UseCase source ID 분리, normalized ID와 `usecase_` prefix 이후의 2차 collision, unknown endpoint,
node/relation cap은 공용 plan과 serializer가 계속 fail closed로 판정합니다. 기본 direction은 `LR`이고
허용되지 않은 값은 serializer와 Scene 모두 `TB`를 사용합니다. 이 계약으로 모든 Phase 2 type이 nested
검증을 갖지만, provider envelope는 계속 generic `TypedIRCandidate.ir: dict`입니다.
