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
| Pie | `pie` 또는 `flowchart` | 12-slice·binary64·1% visibility·`showData` exactness native gate, 그 외 및 native runtime 거부는 same-slot exact-value fallback |
| XY | `xychart` 또는 `flowchart` | bounded exact renderer grid·visible line/bar·binary64 gate, 그 외 및 native runtime 거부는 same-slot exact-value fallback |
| Quadrant | `quadrant` 또는 `flowchart` | exact normalized coordinate와 pinned canvas visibility를 통과하면 native, 그 외 및 runtime 거부는 same-slot exact-cell fallback |
| Sankey | `sankey` 또는 `flowchart` | native-safe positive DAG, 그 외 및 native runtime 거부 시 same-slot exact-weight fallback |
| Radar | `radar` 또는 `flowchart` | 12-series 이하의 zero-or-normal binary64·finite positive span/radius native domain, 그 외 및 native runtime 거부는 same-slot exact-value tabular fallback(최대 256 point) |
| Treemap | `treemap` 또는 `flowchart` | leaf value 필수; internal value·unsafe binary64/표시 합계·native runtime 거부는 same-slot exact-value fallback |
| Venn | `venn` 또는 `flowchart` | positive normal binary64·`200:1` visibility·explicit pair gate를 통과하면 native, 그 외 same-slot exact set graph |
| Journey | `timeline` | strict SVG에서 금지된 `foreignObject`를 피하고 score/actor를 event text로 보존 |
| Kanban, GitGraph | 동일 또는 `flowchart` | native runtime 거부 시 공용 planning plan으로 같은 candidate slot에서 portable fallback |
| Packet | `packet` 또는 `flowchart` | 명시적 contiguous bit range만 native; fallback은 가상 gap·edge가 없는 독립 field |
| Ishikawa, TreeView | 동일 또는 `flowchart` | cycle/object reuse/duplicate ID/depth를 검증한 hierarchy |
| Event Modeling | `flowchart` | Mermaid 11.16 renderer 불안정으로 lane-aware fallback |
| Wardley | `wardley` 또는 `flowchart` | native runtime 거부 시 좌표·축·anchor 손실을 공개하는 marker-less explicit-link fallback |
| Cynefin | `cynefin` 또는 `flowchart` | native 성공은 fixed runtime template 때문에 review-only; runtime 거부 시 explicit domain/item/directed-transition fallback |
| Railroad | 동일 | strict recursive rule AST와 bounded shared plan이 검증한 experimental native |
| ZenUML | `sequence` | pinned runtime에 ZenUML extension이 없어 명시적 fallback |
| Organization | `treeview` | reporting hierarchy 보존, organization 전용 notation 없음 |
| Data Lineage | `flowchart` | dataset/process endpoint를 모두 확인한 portable graph |

Event Modeling은 lane/frame/relation을 하나의 frozen plan에서 검증한 뒤
`eventmodeling_lane_*`·`eventmodeling_frame_*` emitted ID로 Flowchart fallback을 만듭니다.
Lane은 subgraph membership, frame은 node, relation은 ID 문법 없는 단방향 end-arrow로
표현하고 `eventmodeling_relation_*`는 Scene/provenance slot에만 부여합니다.
ZenUML도 `zenuml_participant_*`는 Sequence participant ID로 방출하지만,
ID 문법이 없는 message의 `zenuml_message_*`는 Scene/provenance slot에만 쓰고 source에는
namespaced endpoint만 방출합니다. 두 plan은 raw role/shape/style/direction/bidirectional/relation ID 같은
fallback이 표현하지 않는 metadata를 node·edge로 승격하지 않으며, generated Scene과
OCR projection이 같은 code-visible identity·Scene relation slot·화면 label·topology를 재사용합니다.

Organization은 `plan_organization_hierarchy()`가 고정한 logical `treeview_node_*` identity와
parent→child relation을 native TreeView와 runtime Flowchart fallback, Scene/OCR의 대응 slot에
공유합니다. Native TreeView source/SVG는 이 ID를 문법으로 방출하지 않고 validated
label/depth만 사용하며, Flowchart fallback이 reserved-safe ID를 실제 node ID로 사용합니다.
양쪽 배치 방향은 `LR`입니다. Data Lineage는 `plan_data_lineage_records()`가
dataset/process namespace, cylinder/rectangle shape, explicit data-flow endpoint, 실제 화면 label,
strict direction을 한 번만 결정합니다. Mermaid edge에 source relation ID 문법이 없으므로
`organization_relation_*`·`data_lineage_relation_*`는 Scene/provenance slot으로만 쓰입니다.
두 serializer는 source code 50,000자·5,000줄 예산을 반환 전에 검사하고, visible
compatibility glyph 치환을 warning·OCR·Scene에 공유합니다.

State/Class/ER serializer는 provenance 없는 구조를 문법적으로 만들 수 있어도 거부합니다. unknown
endpoint, 추측 cardinality, ER의 identifying flag 누락도 `SerializationError`입니다. Requirement/Block과
fallback serializer 역시 unknown relation endpoint를 임의 node로 만들지 않습니다.

State는 serializer와 generated Scene이 같은 emission plan을 소비합니다. 이 plan은 source state ID를
Mermaid-safe ID로 한 번만 정규화하고 transition endpoint, 화면 label, pseudo-state kind와 deterministic
Scene relation ID를 함께 고정합니다. `[*]` boundary transition도 endpoint와 provenance를 검증하고 source에는
보존하지만, 별도 Scene element가 없는 boundary marker를 가짜 node/relation으로 만들지는 않습니다. 따라서
malformed transition이나 unknown endpoint는 nodes-only partial Scene으로 축소되지 않고 전체 후보가
fail closed됩니다. 다만 boundary edge의 label은 SVG canvas에 실제로 표시되므로 structural Scene과 별도의
semantic OCR projection에는 포함합니다.

Gantt도 공용 plan이 section-local `Task N` fallback과 실제 task/section label을 serializer, generated Scene,
OCR projection에 공유합니다. Mermaid Gantt source에 Scene identity 문법은 없으므로 source code는 기존 task
ID를 그대로 보존하되, attribution용 section/task ID는 전체 diagram namespace에서 collision-free하게
배정합니다. 중복 source ID가 있어도 렌더링된 task나 group membership, record provenance를 누락하지 않습니다.

Requirement·Block은 serializer 전에 strict nested extraction 계약을 통과합니다. Requirement의
requirement/element/relation과 Block의 block/edge는 각각 object list여야 하고, 알려진 scalar,
`bbox`, `evidence_ids`의 형을 검사합니다. Requirement `type`·`risk`·verify method·relation
type과 Block `shape`는 serializer가 이미 수용하는 닫힌 token을 대소문자 구분 없이 검사하며,
원본 문자열은 재작성하지 않습니다. Legacy `verifymethod`도 같은 verify token으로 후처리
검증하지만 provider prompt에는 canonical `verify_method`만 보입니다.

Root list를 제외한 개별 field와 `evidence_ids`는 partial/legacy candidate 호환을 위해 model에서
선택으로 남겨 둡니다. `evidence_ids`가 존재하면 strict string list와 record별 최대 256개 상한을
적용합니다. 추가 metadata는 `extra="allow"`로 보존하고, validation result로 IR을
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

### Journey·Kanban·GitGraph의 planning projection

세 planning type은 strict nested extraction으로 known record/container/scalar와 bbox/evidence 형을 먼저
확인합니다. Journey는 `sections`와 nested task, Kanban은 `columns`/`cards`, GitGraph는 ordered
`operations`를 canonical prompt에 공개합니다. Compatibility alias는 검증하고 원본 IR에 보존하되 prompt에는
광고하지 않습니다. Non-empty, 1~5 score, ID/reference/collision, branch-head replay와 merge 가능성은
serializer-owned 의미 검사로 남습니다. 2,000개 record는 구조 탐색의 절대 상한이며, 실제 native/fallback
source는 candidate validator와 같은 50,000자·5,000줄 hard budget을 생성 직후 다시 검사합니다. 따라서
긴 label이나 많은 actor가 있으면 record 상한보다 먼저 실패합니다.

Journey는 native grammar의 `foreignObject` 때문에 처음부터 `journey → timeline`을 명시합니다. Section은
Scene group, task는 attributed Scene element가 되고 실제 Timeline text의 task label, `Score N`, actor 목록만
OCR projection에 들어갑니다. 별도 source 숫자가 없거나 score가 일치하지 않으면 parse/render 성공과
관계없이 review로 남습니다.

Timeline item 문법에서 literal colon과 entity-like spelling을 그대로 두면 delimiter로 분리되거나 text가
잘릴 수 있습니다. 따라서 Journey section/task/actor의 `:`는 `∶`, `&...;` prefix는 `＆...;` 또는
`＆＃...;`로 명시적으로 바꾸고 compatibility warning을 남깁니다. Title angle bracket도 `‹`/`›`로
표시합니다. 원본 evidence는 typed IR과 sidecar에 그대로 보존됩니다.

Kanban의 공용 plan은 column/card raw ID를 한 번만 검증하고 예약어와 충돌하지 않는 `kanban_` namespace로
정규화하며 label alias 및 card의 resolved column을 고정합니다. Native serializer, generated Scene과
runtime Flowchart fallback이 모두 이 emitted ID와 containment를 사용합니다. GitGraph 공용 plan은 정확한
`main`에서 operation을 재생해 commit/merge node,
부모 relation, branch membership을 고정합니다. Native rejection 뒤에도 같은 node/parent topology와 tag를
Flowchart로 방출하고 branch lane/order 및 commit-type glyph 손실을 warning으로 공개합니다. 두 fallback은
새 candidate를 소비하지 않으며 source security scan, parse/render, SVG inspection과 terminal `flowchart`
type을 다시 통과해야 채택됩니다.

Canonical field와 compatibility alias가 둘 다 존재하면 의미가 같아야 합니다. Journey `title/label`과
`label/text`, Kanban `label/title`과 `label/text`, GitGraph branch `name/id`와 commit `commit_type/style`이
서로 다르면 우선순위로 하나를 버리지 않고 fail closed입니다. GitGraph commit ID도 source 문자열뿐 아니라
grammar encoding 뒤의 표시 namespace까지 고유해야 합니다. Generic extraction record에 함께 기술되는 known
field도 operation type에 맞아야 하며, 예를 들어 branch의 `commit_type`이나 merge의 `name`은 metadata로
조용히 버리지 않고 거부합니다.

GitGraph text는 Mermaid 11.16 grammar 전용 quoting을 사용합니다. Quote/backslash와 일반 문장부호는 실제 SVG
glyph를 보존하고 active URL/directive/callback/entity token만 invisible separator로 끊습니다. Native SVG가
원문 angle bracket을 보존하지 못해 `‹`/`›`로 대체할 때는 compatibility warning을 result에 남깁니다.
Kanban native markdown label은 literal quote/backtick을 보존하지 못할 때 `″`/`ˋ`를 사용합니다. Flowchart
fallback은 native code quoting이 아니라 portable label encoder를 사용하며 literal quote/backslash를
`″`/`∖`로 바꾼 경우 warning을 남깁니다.

### Packet·Ishikawa·TreeView의 공유 projection

세 유형은 strict nested extraction 후 serializer와 generated Scene이 같은 bounded plan을
소비합니다. Native 문법은 검증된 label/range/depth를 사용하고 명시적 ID를 표현하는
fallback과 Scene만 plan의 reserved-safe emitted ID를 사용합니다. Packet plan은
source field record·raw ID·`packet_field_` emitted ID·label·
explicit start/end를 묶어 contiguous 여부를 판정합니다. Gap이 있거나 native runtime이
거부하면 모든 bit range를 ordered label로 보존하지만 필드 사이 관계를 추측하지 않는
disconnected Flowchart를 방출합니다. Generated Scene도 같은 ID/label/bbox/evidence의
`field` element만 `LR` 순서로 만들고 relation은 비워 둡니다.

Native Packet의 명시적 `title`은 field plan과 별도의 공용 title normalizer가 whitespace와
entity-compatible visible glyph를 한 번만 결정합니다. Serializer source는 active token을 끊는 invisible
separator를 추가할 수 있지만 semantic OCR projection은 화면상 동일한 text에서 그 separator를 제거합니다.
Pipeline은 parse/render로 확인한 terminal type을 projection에 넘기므로 `packet` terminal만 title을 세고,
같은 candidate slot의 `flowchart` fallback은 접근성 metadata에 남은 title을 canvas label로 오인하지 않습니다.
이 분기는 field label과 field-local numeric range association을 변경하지 않습니다.

Ishikawa plan은 child가 없는 effect와 categories를 하나의 DFS tree로, TreeView plan은
root/children tree로 검증합니다. Source ID·emitted ID·label·depth·parent·source record를
각 row에 고정하며 serializer, Flowchart fallback과 Scene containment relation이 이 row를 공유합니다.
따라서 missing-ID DFS 순서, reserved-safe `ishikawa_node_`/`treeview_node_` namespace와
attribution 분모가 같습니다. Raw/normalized/emitted ID 충돌, cycle, 이미 방문한 같은
dict object 재사용, 64 depth·2,000 node·500 fallback edge 한도를 넘으면 부분
hierarchy를 방출하지 않고 후보 전체를 거부합니다.

`label`/`name` alias가 동시에 있으면 정규화한 문자열이 같아야 하고 Ishikawa
effect의 `children`은 category root를 덮어쓰지 못하게 거부합니다. Entity-like literal을
pinned grammar가 안전하게 보존하지 못하면 보이는 `＆`/`＃` glyph와 compatibility warning을
사용합니다. 원 label, alias, bbox/evidence는 typed IR·review sidecar에 변경 없이
남습니다. Native/fallback 모두 serializer 반환 전에 50,000자·5,000줄 source budget을
다시 검사합니다.

### Wardley·Cynefin의 공유 projection

Wardley serializer와 generated Scene/OCR projection은 같은 bounded plan을 소비합니다.
Plan은 component source ID, 충돌 없는 표시 label, strict finite `x`/`y`, exact boolean
`anchor`, link endpoint·label을 한 번만 검증합니다. Mermaid 11.16이 entity-like literal을
손실시키는 경우 보이는 `＆`/`＃` compatibility glyph를 native source와 generated
semantic text에 같이 사용하고 warning을 남깁니다. 원문은 typed IR을 변경하지 않습니다.
IR `x`/`y`는 수평/수직 좌표입니다. Native의 `[visibility, evolution]` 문법에는 `[y, x]`로
직렬화하고 Scene은 실제 화면 좌표 `(x, 1-y)`를 normalized position으로 사용합니다. Native
token으로 반올림된 값을 plan 좌표에도 적용하며 record bbox·extra geometry를 layout 근거로
사용하지 않습니다. Wardley `->`는 11.16 SVG에서 marker가 없는 plain link이므로 Scene도
무방향 relation을 만듭니다.

`wardley-beta`가 runtime parse/render를 통과하지 못하면 같은 candidate slot에서 공용 plan을
`flowchart LR`로 한 번만 재직렬화합니다. Component는 입력 순서대로
`wardley_component_N` ID를 받고 explicit link만 `---` 또는 `---|label|`로 방출하므로 방향을
새로 만들지 않습니다. Flowchart가 Wardley 좌표·visibility/evolution 축·anchor 표기를 표현하지
못한다는 warning을 항상 남기고, generated Scene도 zero bbox·`pixels` coordinate space·rectangle
node를 사용해 원래 위치를 보존한 것처럼 layout 점수를 만들지 않습니다. Quote/backslash와
edge delimiter는 실제 fallback SVG가 표시하는 compatibility glyph로 투영합니다. Fallback은
source scan, parse/render와 terminal `flowchart` type을 다시 통과할 때만 채택됩니다. Native의
보이는 title도 canvas에서는 손실된다는 별도 warning을 남깁니다. 같은 title이 접근성 title로
선택되면 `accTitle`에 보존하지만, 명시적 `acc_title`이 다르면 그 값을 우선하고 visible title은
typed IR/review metadata에만 남는다고 구분해 경고합니다.

Cynefin plan은 다섯 official domain의 reserved-safe domain/group ID, 순서적 item ID,
명시적 transition ID와 표시 text를 고정합니다. Native 성공 시 11.16 runtime이 입력과 무관하게 만드는
다섯 domain·practice/response·disorder template element도 generated Scene/OCR에 포함하되 evidence는
비워 둡니다. `confusion` item은 native runtime이 보이는 처음 세 개와 `+N more`만 Scene/OCR에 넣고,
나머지 원문은 typed IR/sidecar에만 보존합니다. Domain membership을 containment edge로 추측하지 않고
native grammar이 명시적 item 좌표를 주지 않으므로 layout metric은 unavailable입니다. Object item의
record evidence만 provenance로 사용하며 legacy scalar item에 evidence를 발명하지 않습니다.

`cynefin-beta`가 runtime parse/render를 통과하지 못하면 같은 candidate slot에서 공용 plan을
`flowchart LR`로 한 번만 재직렬화합니다. Source가 실제 제공한 domain마다 하나의 subgraph를 만들고,
그 안에 모든 explicit item을 축약 없이 방출합니다. 따라서 source가 주지 않은 official domain이나
practice/response/disorder template, native 전용 `+N more`, domain-item membership connector는 만들지
않습니다. Explicit transition만 source/target subgraph 자체를 endpoint로 하는 directed edge가 됩니다.
Fallback Scene은 각 domain을 같은 ID의 conceptual element와 group으로 나타내되 OCR은 domain label을
한 번만 투영하고, domain/item/transition의 표시 text와 record-local provenance를 그대로 사용합니다.
Geometry는 모두 zero bbox, direction은 `LR`이며 quadrant/layout 의미 손실을 warning으로 공개합니다.

두 serializer는 source를 반환하기 전 50,000자·5,000줄 hard budget을 독립적으로
검사합니다. Wardley와 Cynefin은 native runtime rejection 후 각 Flowchart를 같은 candidate slot에서
재검증하므로 후보 수를 늘리지 않습니다. Fallback도 source scan, parse/render, SVG와 terminal
`flowchart` type을 독립적으로 통과해야 하며 requested/emitted/runtime type, fallback chain, repair history,
security profile과 requested-type 접근성 metadata를 유지합니다. Cynefin fallback은 generated-node
attribution threshold와 일반 semantic/publication gate를 통과하면 게시할 수 있습니다. 반면 native
Cynefin은 렌더에 성공해도 고정
template에 source provenance를 붙이는 계약이 없어 자동 게시하지 않고 review/sidecar로만 routing합니다.

### Railroad의 recursive AST projection

Railroad serializer는 strict nested expression을 직접 다시 순회하지 않고
`plan_railroad_records()`가 만든 frozen rule/expression/relation plan만 소비합니다. Rule은 source 순서,
expression은 rule별 preorder를 유지합니다. Plan은 rule 이름의 uniqueness, terminal/nonterminal/special의
문자열 payload, sequence/choice의 child list, optional/one-or-more/zero-or-more의 단일 child, 모든
nonterminal reference, 최대 depth 20과 rule/expression 각각 500개 한도를 한 번 판정합니다. Rule과
nonterminal name은 whitespace normalization 뒤 ASCII identifier 128자, 다른 visible text는 whitespace
normalization 뒤 field당 500자로 제한하며 raw input은 typed IR/sidecar에 보존합니다.

Native source는 `railroad-beta`에서 `native_name = expression;`을 방출합니다.
`railroad_expression_N`과 `railroad_relation_N`은 Mermaid source에 없는 Scene/provenance identity이고,
logical `railroad_rule_*`도 일반적으로 source rule name과 분리됩니다. Scanner/preprocessor에서
source-active인 rule name과 case-folded expression-word namespace, `railroad-beta`, case-folded lowercase
`title*` prefix는 Railroad identifier로 안전하게 방출할 수 없어 collision-safe `rrmapped_N[_suffix]`를
strict-safe `native_name`으로 사용하고 visible change warning을 남깁니다. `style`/`classDef` substring을
포함한 이름도 preprocessor source-active mapping 대상입니다.
정규화된 safe source name은 그대로 native name으로 유지하며 allocator는 모든 safe name을 먼저 reserve한
뒤 suffix로 충돌을 피합니다. `railroad_relation_N`은 rule→definition과 parent operator→child만 나타내며 native에 없는
nonterminal→rule edge를 발명하지 않습니다. 실제 SVG 기준 visible text는 rule `native_name =`,
terminal/nonterminal runtime label, special `? text ?`이고 구조 operator에는 label이 없습니다.

Railroad visible compatibility layer는 ASCII `<`/`>`를 `〈`/`〉`, 모든 ASCII `#`를 `＃`, entity-like
`&` prefix를 `＆`, NFKC에서 quote/backslash가 되는 호환 문자를 `″`/`∖`로 바꿉니다. 전역
`encodeEntities`가 변형하는 bare `#word;`/`#35;`도 예외가 아니며 치환을 compatibility warning으로
공개합니다. Plan은 raw
semantic field를 typed IR/sidecar에 남기고 이 exact compatibility text를 native visible output·Scene·OCR에
공유합니다. Rule identifier 외의 URL/directive/callback/HTML-like active token과
compatibility-normalized hazard에는 source에서만 zero-width separator를 넣습니다. Mermaid preprocessor가
statement로 오인할 수 있는 `style...:#...;`/`classDef...:#...;` substring도 source에서만 분리합니다.
Emitted source 원문과
NFKC-normalized source 모두 strict scanner를 통과해야 합니다. Production CandidateValidator의
parse/render hard gate는 raw source에 적용하고, NFKC parse/render는 integration safety probe에서 grammar
injection이 생기지 않는지만 고정합니다. NFKC SVG의 compatibility glyph가 원 glyph와 같다고 요구하지
않습니다. Mapped rule의 raw source name은 typed IR에, normalized name은 nonterminal label에 남습니다.
접근성 title/description도 plan이 같은 source-only 경계를 적용해 serializer가 raw IR을 다시 읽지
않습니다. 반환 전 50,000자·5,000줄 source preflight를 수행하며 strict scanner, parse, render와 SVG
inspection은 다른 experimental native와 동일하게 필수입니다.

Generated Scene은 rule/expression source record의 `evidence_ids`가 null/생략 또는 string list인지 직접
검사하고 다른 형이면 후보 전체를 fail closed합니다. 따라서 serializer, Scene, OCR, provenance가 동일한
bounded plan과 compatibility label을 소비합니다.

### Pie·XY·Quadrant terminal 경계

Pie·XY·Quadrant는 strict nested extraction contract를 통과합니다. Contract는 record/container, strict finite
JSON `int`/`float`, boolean, bbox/evidence와 XY `line|bar` token의 형을 검사합니다. Direct serializer가 받을
수 있는 `Decimal`은 provider structured input 계약이 아닙니다. 세 유형 모두 native terminal이 원본
값/구조를 손실할 경우 disconnected exact-value Flowchart로 낮춥니다. Missing/unreadable value의 structured
table/prose 대체는 아직 후속 범위입니다.

Pie의 non-empty·고유 label·non-negative value·positive total, XY의 categorical/numeric x-axis 배타성,
axis bounds와 모든 y의 범위 포함 여부, exactly-one values/points, category 길이 및 native uniform numeric grid,
Quadrant의 non-empty·고유 point label과 `[0,1]` 좌표는 serializer 소유 의미 검사입니다. XY series의
`label`/`name`은 Mermaid 11.16에 strict-safe series-label syntax가 없어 거부합니다. Quadrant label은
정확히 네 항목인 list 또는 canonical key를 가진 object로 prompt에 노출합니다. Object는 부분 label을
허용하지만 `1`과 `quadrant-1`처럼 같은 slot을 가리키는 alias가 함께 오면 모호한 덮어쓰기 대신 거부합니다.

Pie는 serializer·Scene·semantic OCR이 공유하는 bounded `PiePlan`을 사용합니다. Native `pie`는 최대 12개의
slice, zero-or-normal binary64 round-trip-safe value, JavaScript 왼쪽부터 합산한 finite positive total,
finite centroid, positive slice별 1% 이상 visibility를 요구합니다. Zero slice는 legend-only로 허용합니다.
`show_data=true`일 때는 Mermaid가 legend에 쓰는 JavaScript `String(value)`가 exact fixed-decimal source token과
같아야 합니다. 하나라도 맞지 않으면 최대 256개의 `label: exact-value` rectangle만 가진 disconnected
`flowchart TB`를 선택합니다. Native parse/render/SVG/type validation이 거부한 경우에도 같은 candidate slot에서
이 Flowchart를 한 번 재직렬화해 전체 gate를 다시 통과시킵니다. 두 terminal 모두 Mermaid JavaScript
`text.length`와 동일한 50,000 UTF-16 code-unit·5,000줄 source preflight를 적용합니다.

Native Scene은 `pie_slice_N` sector element를 renderer의 normalized percentage-label centroid에 배치하고 zero
slice에는 zero bbox를 사용합니다. Relation/group은 없고 direction은 `radial`입니다. OCR은 visible title,
legend와 positive slice percentage만 세며, `showData` value는 legend text에 포함됩니다. Flowchart Scene은
`TB` zero-geometry rectangle cell과 빈 relation/group을 사용하고 OCR은 exact cell label만 셉니다. Native-only
title과 접근성 metadata는 fallback canvas OCR에 들어가지 않습니다. Record-local evidence는 두 terminal의
slice element에 보존되어 Extended generated-node provenance gate에 참여합니다.

Slice label의 quote/backslash는 native canvas에서 보존하고 directive/URL/callback/CSS/icon/entity/statement
scanner-active token은 source에만 invisible separator를 둡니다. Native title의 quote/backslash/angle/hash/semicolon과 Flowchart cell의
quote/backslash/angle/hash는 terminal-visible compatibility glyph로 바꾸며 warning에 공개합니다. Semantic
원문과 source bbox는 typed IR/review metadata에 남습니다. Pie 자동 numeric gate는 각 non-overlapping slice
bbox 안의 candidate-authorized, slice-cited OCR/vector text가 punctuation-preserving 전체 label과 허용 separator,
exact value record를 증명하고, 전체 source/generated numeric occurrence도 exact하도록 요구합니다. 값 swap,
label suffix omission, uncited slice/extra number는 mismatch 또는 unavailable이고 누락 authority·모호한 공유
관측·invalid geometry·budget 소진도 review입니다. Explicit `title`/`acc_title`과
`description`/`acc_description`은 slice-owned observation과 겹치지 않는 독립 spatial OCR/vector exact text 또는
reconstruction 초기 입력의 exact `user_edit` evidence가 필요합니다. Engine-emitted `user_edit`는 스스로 이
신뢰를 만들 수 없습니다. 구조에서 파생한 기본 접근성 문구와 experimental notice만 추가 gate 없이
허용합니다.

XY는 serializer·Scene·semantic OCR이 공유하는 `XYPlan`에 axis/series/point source record,
exact fixed-decimal x/y, deterministic identity와 record-local evidence를 고정합니다. Native terminal은 축과
value가 zero-or-normal binary64로 exact round-trip되고 축 span이 positive normal finite이며, Mermaid 11.16의
numeric x loop를 bounded simulation했을 때 정확한 point 개수·endpoint·엄격한 진전을 만족해야 합니다.
Line은 두 point 이상, bar는 y minimum에서 positive height가 필요합니다. 두 bar series의 exact overlay,
동일 line path overlay, 10-series palette cap, non-uniform explicit x, point drop/stall 위험은 모두 native
limitation으로 기록하고 최대 256 point의 exact Flowchart로 낮춥니다. Fallback은 visible title,
두 axis, category, category-bound value 또는 explicit x/y를 독립 rectangle에 담고 relation을 만들지 않습니다.
Native runtime이 실패하면 같은 candidate slot에서 fallback source를 한 번 재직렬화해 security,
parse, render, SVG/type gate를 다시 통과시킵니다. 두 terminal은 50,000 UTF-16 code-unit·5,000줄
상한과 visible compatibility warning을 공유합니다.

Native XY Scene은 normalized axis/category/data geometry와 marker-less adjacent line relation을 만듭니다. Data value는
canvas text가 아니므로 Scene element에는 text를 붙이지 않고 semantic OCR에서도 제외합니다. Fallback
Scene/OCR은 emitted title/axis/category/data cell 순서와 text를 그대로 반영하며 zero geometry와 빈
relation/group을 사용합니다. 각 axis, series, explicit point는 자신의 candidate-authorized bbox 안에서
인용한 OCR/vector text가 완전한 label/category/value/x-y record를 증명해야 자동 게시됩니다. Record swap,
공유 observation/evidence, invalid bbox, 전역 numeric mismatch는 review로 내립니다. Explicit title·accessibility
metadata는 data-owned observation과 분리된 exact OCR/vector 근거 또는 reconstruction 초기의 exact `user_edit`가
필요하고 engine이 새로 만든 `user_edit`는 승인 근거가 되지 않습니다.

Quadrant serializer·Scene·OCR은 bounded `QuadrantPlan`을 공유합니다. Native `quadrantChart`는 최대 256개의
point가 exact `[0,1]` fixed decimal이고 zero-or-normal binary64로 round-trip되며, pinned 500×500 canvas의
title-dependent plot에서 finite·distinct pixel 위치와 point/label/quadrant/axis/title 비충돌·비클리핑을
만족할 때만 선택합니다. Pairwise visibility 비교는 100,000회로 제한합니다. Float collapse, subnormal,
duplicate/near point 또는 text occlusion은 source 값을 버리지 않고 optional title, 두 axis, supplied
quadrant slot, `label · x X, y Y` rectangle만 가진 disconnected `flowchart TB`로 낮춥니다. Native runtime
거부도 같은 slot의 fallback을 security/parse/render/SVG/type gate부터 다시 검사합니다. 두 terminal은
50,000 UTF-16 code-unit·5,000줄 source preflight를 공유합니다.

Point label/coordinate는 terminal-visible projection을 여러 벌 만들기 전에 native와 Flowchart source의
UTF-16 unit을 별도로 누적합니다. 두 terminal이 모두 50,000-unit limit을 넘는 즉시 fail closed하고, 한쪽만
넘으면 그 terminal만 unavailable로 두어 다른 쪽의 정확한 출력을 불필요하게 버리지 않습니다. Native
Mermaid 11.16이 point paint에 non-finite HSL component를 만드는 renderer 호환 문제는 candidate warning으로
공개하되 finite geometry/text 자체는 유지하며, exact Flowchart에는 해당 경고를 전파하지 않습니다.

Native Scene은 four visible axis endpoint element, `(x, 1-y)` normalized point circle과 upper-right,
upper-left, lower-left, lower-right `SceneGroup`을 사용합니다. 실제 canvas에 없는 axis line, point edge와
group membership은 만들지 않고 direction은 `unknown`입니다. Fallback Scene은 emitted title/axis/slot/point
cell을 source 순서의 zero geometry로 투영하고 relation/group은 비우며 `TB`입니다. OCR도 각 terminal의
visible text만 세고 native point coordinate와 accessibility metadata는 제외합니다.

Axis/point bbox와 evidence는 generated element의 record-local provenance에 연결되며 각 record의 complete
low/high 또는 label/x/y observation과 global numeric occurrence가 함께 맞아야 자동 게시됩니다. X axis의
horizontal·아래쪽 bbox와 y axis의 vertical·왼쪽 bbox 관계도 요구해 전체 axis record 교환을 차단합니다.
Typed schema에
별도 evidence가 없는 supplied slot label은 source 해당 사분면의 independent exact OCR/vector 또는 초기
exact `user_edit` 중 유효한 source-quadrant bbox가 있는 근거로 검증하고 axis/point evidence를 상속하지
않습니다. Explicit title/accessibility text도
data-owned bbox와 분리된 근거를 요구합니다. Direct candidate, observation/record reuse, swap, invalid geometry,
missing source evidence와 공유 100,000회 association budget 초과는 review-only입니다. Numeric projection은 directive의 구조
번호 `quadrant-1`~`quadrant-4`를 제외하되 visible label과 point coordinate의 실제 숫자를 유지합니다. Slot
spatial gate는 전체 source crop midpoint를 쓰는 보수적 heuristic이며 off-center plot bbox를 추정하지 않습니다.
Explicit title/description evidence에는 immutable semantic target이 없으므로 exact content existence만
검증합니다. Best-effort output은 role-attribution limitation을 경고하고 strict validated output은 review로
유지합니다.

### Sankey·Radar·Treemap·Venn의 fallback 경계

네 extended chart도 strict nested extraction 뒤 기존 serializer가 의미 completeness와 native 표현 가능성을
판정합니다. Sankey는 canonical `nodes`/`flows`, Radar는 `dimensions`/`series`, Treemap은 recursive `root`,
Venn은 `sets`/`intersections`를 사용합니다. Nested contract는 finite JSON number, record/container,
bbox/evidence와 Radar option scalar만 검사하며 non-empty, ID/reference, 값 범위, hierarchy budget과
native/fallback 선택은 serializer에 남깁니다. Radar `ticks`는 Mermaid renderer의 tick loop를 제한하기 위해
serializer에서 최대 100으로 제한합니다.

Sankey는 serializer, generated Scene, semantic OCR이 같은 bounded plan을 사용합니다. Plan은 source node와
flow record를 한 번 검증하고 native source ID, Flowchart collision-safe emitted ID, exact decimal weight,
record-local evidence, collision-free relation Scene ID를 고정합니다. Positive weighted DAG, native-safe 고유
label, 모든 node 참여뿐 아니라 Mermaid 11.16이 표시하는 node 합계를 안전하게 재현할 수 있어야 native를
선택합니다. Native canvas의 node total은 binary float incoming/outgoing 합계 중 큰 값에
`Math.round(value * 100) / 100`을 적용한 결과이며, 개별 flow weight와 arrow marker는 보이지 않습니다.
따라서 native Scene은 source node ID, 무라벨·marker-less `data_flow`, 고정 `LR`을 사용하고 OCR은 node label과
표시 합계만 투영합니다. `parseFloat` 변환에서 positive value가 0/무한대로 소실되거나 shortest decimal이
달라지고, 또는 합계의 cent 단위 문자열을 JavaScript와 동일하게 만들 수 없으면 native 지원 조건을 닫고
exact fallback을 사용합니다.

Plan은 flow 수를 Scene relation 상한에서 먼저 거부하고 optional flow ID를 bounded unique Scene slot으로
정규화합니다. 비문자·초과 길이·잘못된 Unicode ID는 deterministic `sankey_flow_N`으로 격리하고 중복은
bounded suffix를 붙입니다. Node/flow `evidence_ids`가 bounded string list 계약을 어기면 code와 topology를
버리지 않고 해당 record의 evidence tuple만 비워, malformed metadata가 허위 provenance나 Scene 전체 실패를
만들지 못하게 합니다. Native Sankey는 공통 Scene relation 상한까지 허용하되, portable projection은 pinned
Mermaid worker의 `maxEdges=500`을 넘으면 code를 반환하지 않습니다. 따라서 501개 이상의 valid native flow는
native로 남을 수 있지만, 같은 후보의 runtime fallback이 필요하면 명시적으로 unavailable 처리됩니다.

Native 조건을 벗어나거나 native runtime validation이 거부하면 각 exact weight를 directed edge label로
보존하는 Flowchart를 만듭니다. 이 경로는 공용 plan의 emitted node ID, end-arrow와 정규화된 requested
direction을 Scene/OCR에도 공유합니다. Runtime 재시도는 같은 candidate slot에서 한 번만 수행하고 새
candidate/type/repair budget을 소비하지 않으며, strict source scan과 parse/render/SVG/terminal-type gate를
전부 다시 적용합니다. 성공 시 requested type은 `sankey`, emitted/runtime type은 `flowchart` 계열,
fallback chain은 `sankey → flowchart`로 기록됩니다. Sankey title/description은 native canvas에 없고 fallback
SVG에서는 accessibility metadata일 뿐이므로 어느 terminal에서도 content OCR label로 세지 않습니다.

게시 gate는 terminal 표현과 별도로 각 planned flow의 exact `value_text`를 source record에 결합합니다. 각
flow는 source image 안에서 서로 양의 면적으로 겹치지 않는 bbox와, 그 bbox 안에 완전히 포함된
candidate-authorized `ocr_token`/`vector_text` observation을 직접 인용해야 하며 전역 numeric occurrence도
exact해야 합니다. 다른
flow와 evidence ID 또는 normalized text+bbox를 공유하거나 같은 bbox의 상충 text를 숨기는 입력, weight swap,
invalid geometry와 bounded association budget 초과는 fail closed입니다. Native, same-slot Flowchart와 semantic
repair는 같은 typed plan/scoped evidence로 이 검사를 다시 수행하고, direct/untyped Sankey는 flow-local
소유권을 만들 수 없어 review에 남습니다.

Raw accessibility metadata는 enrichment보다 먼저 별도 경계에서 검사합니다. Pipeline candidate 경계와 public
typed serializer 모두 Sankey의 `title`, `description`, `acc_title`, `acc_description`이 `None`이 아니면 exact
built-in `str`인지 확인합니다. Raw 길이를 whitespace 정규화보다 먼저 `MAX_TEXT_CHARS`로 제한하고, 호환용
exact empty string 외에는 정규화 결과도 non-empty·bounded여야 합니다. 또한 UTF-8 encoding이 가능하고
정규화된 text에 Unicode category `Cc`/`Cf`/`Zl`/`Zp` 문자가 없어야 합니다. 따라서 custom subclass,
non-text, huge-whitespace를 포함한 overlong raw/normalized text, whitespace-only, ZWSP/control-only,
lone-surrogate 값은 provider별 Mermaid 생성과 runtime validation에 도달하지 않습니다. `None`/JSON `null`은
absent입니다. Pie/XY의 기존 호환 규칙과 같이 exact empty string은 입력으로 허용하지만 omitted로 resolve해
deterministic title/description을 파생하며, explicit empty SVG metadata로 직렬화하지 않습니다.

Sankey accessibility gate도 terminal 결과에 맞춰 적용합니다. Native Sankey는 title/description을 방출하지
않으므로 exempt입니다. Same-slot Flowchart fallback은 resolved accessibility title과 description을 SVG
`<title>`/`<desc>` metadata로 직렬화하며 canvas node로 만들지 않습니다. `acc_title`이 `title`을,
`acc_description`이 `description`을 output에서 shadow하면 방출되지 않는 legacy text는 검사하지 않습니다.
실제로 직렬화되는 non-derived title과 description 두 역할은 서로 독립적으로, 모든 node/flow record evidence와
분리되고 그 record bbox와 겹치지 않는 candidate-authorized spatial OCR/vector exact observation 또는
reconstruction 초기 입력의 approved exact `user_edit`로 증명되어야 합니다.
결정적으로 파생한 default와 experimental notice만 별도 source attribution 없이 허용합니다.
Node/flow-record-owned 또는 재사용한 evidence/text+bbox, same-bbox ambiguity, node/flow-record overlap, 필요한
data-record bbox의 missing/invalid geometry, bounded reference/text/token/spatial work 초과와 engine-emitted
`user_edit` self-authorization은 review로 닫습니다. 선택된 OCR/vector metadata proof의 numeric token만
flow-weight reference multiset에서 제외해 metadata 숫자를 flow로 오인하지 않으며, Runtime fallback과 semantic
repair는 최종 terminal, 새 typed plan, scoped evidence를 기준으로 이 검사를 다시 수행합니다.

Radar는 `plan_radar_records()`에서 dimension/series와 exact fixed-decimal value, reserved-safe emitted
axis/series/cell ID, terminal별 visible text, record-local evidence를 한 번 고정합니다. Native는 12 series
이하이고 value 및 explicit bound가 zero 또는 normal binary64로 exact round-trip되며, effective scale의
Decimal/binary64 span과 pinned renderer radius 계산이 positive finite일 때만 사용합니다. 음수,
subnormal/overflow/precision loss, zero/non-finite span은 parse/render 성공 여부와 관계없이 exact tabular
fallback을 선택합니다.

Native generated Scene은 `normalized` 좌표의 axis와 data point, series element, 마지막 point에서 첫 point로
닫히는 marker/label 없는 `series_curve` association, `radial` direction을 사용합니다. Source bbox는 이 생성
배치에 복사하지 않으며 series bbox는 해당 point들의 normalized curve envelope입니다. OCR은 visible native
title·axis와 `showLegend=true`인 legend만 세고 curve value,
`min`/`max`, `ticks`, `graticule`, `accTitle`/`accDescr`는 hidden geometry/metadata로 제외합니다. Axis/series
evidence는 해당 element에, dimension+series evidence는 point에, series evidence는 curve relation에 연결하며
malformed evidence list는 그 record에서만 빈 tuple로 격리합니다.
Native point는 independently emitted node가 아니라 curve에서 파생된 geometry이므로 generated-node
provenance gate는 axis/series만 injective하게 평가하고 point value는 numeric consistency에 맡깁니다.

Fallback은 최대 256 point의 edge 없는 `flowchart TB`입니다. Visible native title은 isolated zero-geometry title
node로 보존하고, series별 zero-geometry group을 만들되 `showLegend=true`일 때만 label을 표시하며 각
dimension/value를 rectangle `dimension: exact-value` cell로 보존합니다. Relation은 없습니다. Bounds, ticks,
graticule과 native radial geometry는
typed IR/review metadata에만 남고 fallback OCR에는 실제 title·conditional group·cell visible text만 들어갑니다.
Native runtime rejection은 새 candidate budget을
쓰지 않고 같은 slot에서 이 fallback을 한 번 strict scan·parse·render·SVG·terminal-type 재검증합니다.
Fallback budget을 넘으면 partial projection을 만들지 않습니다. 두 terminal은 50,000 UTF-16 code-unit·5,000줄 source
preflight와 terminal 전체의 collision-safe reserved-word ID namespace를 공유합니다. Visible compatibility
glyph은 Scene/OCR에도 동일하게 사용하고 warning에 공개합니다. CandidateValidator는 SVG geometry attribute의
`NaN`/`Infinity`를 render failure로 취급합니다.
Fallback cell은 실제 emitted node이지만 하나의 dimension과 series record를 결합한 projection이므로, 각 cell에
evidence를 독점시키는 대신 모든 source record를 candidate-authorized spatial OCR/vector observation에 먼저
injective하게 결합합니다. Dimension은 exact label, series는 label과 원래 순서의 모든 fixed-decimal value를
증명해야 하며 local binding과 전역 numeric occurrence가 모두 exact일 때만 native/fallback 공통 numeric score가
`1.0`입니다. 겹치거나 image 밖인 record, owner 밖 evidence, evidence/observation 재사용, 같은 bbox의 상충 text,
missing typed plan 또는 association budget 초과는 source-wide multiset이나 runtime fallback으로 우회하지 않고
review로 닫습니다. Visible title과 non-derived explicit accessibility text는 record 밖의 독립 spatial evidence 또는
승인된 초기 user edit를 추가로 요구합니다. Semantic repair도 proposal의 새 IR로 같은 gate를 다시 계산합니다.

Treemap serializer, generated Scene, semantic OCR은 `plan_treemap_records()`의 같은 bounded DFS
preorder plan을 소비합니다. Plan은 source record·parent/child, unique bounded source ID 또는
collision-safe `treemap_node_N[_suffix]` Scene identity, Flowchart에 실제 방출할 preorder
`N1..Nn`, terminal별 source/canvas label, exact fixed-point value token을 한 번 고정합니다. Cycle,
object reuse, depth·node·relation 예산을 serializer 전에 거부합니다. Source image와 record bbox는
typed IR/review provenance에 보존하지만 generated native/fallback Scene은 모두 zero bbox를 써서
원본 위치를 generated layout으로 오인하지 않습니다. Valid evidence는 element, child evidence는 해당
logical containment relation에도 연결됩니다. Malformed·oversized·invalid-Unicode `evidence_ids`는 해당 record의 전체 evidence
tuple만 비워 부분 인증을 만들지 않고 code·hierarchy·다른 record provenance는 유지합니다.

Native `treemap-beta`는 internal node를 section, leaf를 value cell로 렌더합니다. Mermaid 11.16의
d3-hierarchy가 child를 역순으로 binary64 `+=`하는 방식으로 각 internal total을 다시 계산하고,
모든 section/leaf value는 d3 `format(",")`의 comma-grouped 12-digit text로 표시합니다.
Plan이 emitted decimal token을 JavaScript number로 읽었을 때 underflow/overflow, safe range 초과,
shortest-decimal 손실, 합계/표시 비재현 중 하나라도 있으면 native를 선택하지 않습니다.
Native Scene은 section/leaf role과 logical containment을 제공하지만 실제 SVG에 connector/arrow가
없고 spatial nesting에 flow axis를 부여하지 않아 `reading_direction=unknown`입니다. Zero Scene
geometry는 native/fallback 모두 source bbox를 생성 배치로 복사한 거짓 layout score를 막습니다.

Native의 explicit `title` directive는 canvas title을 별도로 만듭니다. `accTitle`/`accDescr`는
SVG `<title>`/`<desc>` accessibility metadata이며 content OCR text가 아닙니다. 따라서 native
semantic projection은 visible title, section/leaf label, d3로 표시된 합계만 포함합니다.
Renderer는 실제 layout에서 너무 작은 cell의 text를 `display:none`으로 숨길 수 있으므로
모든 label이 canvas에 보인다고 보장하지 않습니다.

Internal node에 explicit value가 있거나 native numeric contract를 만족하지 못하면 plan은
`flowchart TB`로 낮춥니다. Node는 DFS preorder `N1..Nn` rectangle이고 relation은 parent→child
end-arrow이며, 입력에 실제로 있는 value만 exact ` (value: x)` suffix로 붙습니다. 파생
internal total, raw direction, native-only visible title은 만들지 않고 title/description은 SVG accessibility
metadata로만 남습니다. Native runtime rejection도 이 fallback을 같은 candidate slot에서 한 번
재직렬화하고 strict source scan, parse/render/SVG/terminal-type gate를 모두 다시 적용합니다.
Flowchart terminal은 Mermaid worker의 500-relation 상한을 적용합니다. 그보다 큰 valid native
Treemap은 native로 남을 수 있지만 runtime fallback이 필요해지면 unavailable입니다.

직렬화가 성공해도 자동 게시 전에는 공용 plan의 모든 node를 source record에 다시 결합합니다. 모든 bbox는
finite·positive·in-image여야 하고 child는 parent에 완전히 포함되되 동일할 수 없습니다. 직접 sibling
interior는 겹치지 않아야 하며 edge-touch는 허용합니다. Internal owner의 cited text는 direct child 영역과 겹치지 않아야 하므로 넓은 parent bbox가 child OCR을
자기 근거로 가져갈 수 없습니다. 각 node의 candidate-authorized OCR/vector reading-order text는 exact label,
explicit value가 있으면 label 뒤의 exact fixed-decimal value까지 증명해야 합니다. Evidence ID 또는 normalized
text+bbox의 교차 owner 재사용, 한 owner 안의 duplicate evidence reference, same-bbox contradiction, invalid
hierarchy와 bounded work 소진은 unavailable, label/value 불일치는 association mismatch입니다. Aggregate
reference/text/character/token/spatial-comparison budget은 20,000/50,000/1,000,000/100,000/100,000입니다.
`numeric_consistency`는 전역 multiset 진단값을 유지할 수 있지만 local 결과와 global numeric occurrence가
모두 exact일 때만 native와 same-slot Flowchart를 게시할 수 있으며 semantic repair도 같은 typed plan과
scoped evidence로 재검증됩니다.
Direct Treemap은 owner plan이 없어 review-only입니다. Source bbox는 이 gate와 review provenance에만 남고
generated Scene에는 계속 복사하지 않습니다. Native의 `native_total_text`는 child value에서 파생한 renderer
output이지 explicit source value가 아니므로 owner citation에 포함하지 않습니다. Source OCR/vector가 internal
total을 별도 숫자로 관측하면 현재 global occurrence gate는 review로 닫습니다.

Explicit metadata는 serializer input의 존재가 아니라 실제 emitted terminal을 기준으로 검증합니다. Native
Treemap은 visible explicit `title`과 non-derived resolved accessibility title/description을, Flowchart fallback은
실제로 방출하는 resolved accessibility title/description만 요구합니다. `acc_*` override가 legacy text를 가려
terminal에 나오지 않으면 그 legacy field는 면제되고, deterministic derived text와 experimental notice도 source
proof를 요구하지 않습니다. 단, notice-only explicit description override는 structural description을 지우므로
unavailable입니다. 동일한 native visible/accessibility title은 한 title owner로 합치지만 title과
description 역할은 text가 같아도 분리합니다.

각 역할은 모든 node bbox 밖의 candidate-authorized spatial OCR/vector exact text 또는 reconstruction 초기의
approved exact `user_edit`로만 증명합니다. Node-owned evidence/observation, same-bbox ambiguity, node overlap,
cross-owner reuse, engine-emitted edit, invalid geometry와 shared bounded work exhaustion은 unavailable입니다.
선택된 OCR/vector proof에 숫자가 있으면 그 occurrence만 global Treemap data reference에서 제외하고 나머지
node/value 숫자는 exact해야 합니다. Runtime fallback과 semantic repair는 새 terminal/IR/scoped evidence로 이
검사를 다시 실행합니다.

Treemap은 semantic 원문을 typed IR에 남기고, source scanner에 걸리는 token은 emitted source에만
zero-width separator를 넣어 비활성화합니다. Scene/OCR은 separator를 제거한 terminal-visible
text를 씁니다. Node quote는 `″`로 표시하고, Flowchart label의 ASCII angle bracket/backslash는
`＜`/`＞`/`∖`, hash는 `＃`, native title의 angle bracket은 `＜`/`＞`로 표시합니다. Entity-like
literal, `#`, URL/directive-like token은 나머지 visible text를 버리지 않습니다. URL/directive-like token과
entity-like `&...;`는 emitted source에서만 비활성화하고, native의 `#`도 source-only separator로
나눅니다. Native grammar가 그대로 보존하는 literal은 임의 glyph로 바꾸지 않습니다.
Visible compatibility glyph을 사용한 native는 candidate warning을, Flowchart는 fallback reason/warning을
남기며 Scene/OCR이 그 terminal-visible text를 공유합니다. CR/LF와 NBSP를 포함한 Unicode whitespace
run은 한 ASCII space로 고정하고, resolved `accTitle`/`accDescr`의 visible 치환도 같은 warning 계약에
포함합니다. Native와 fallback source는 각각 50,000자·5,000줄 preflight를 통과해야 runtime으로 갑니다.

Venn serializer, generated Scene, semantic OCR은 `plan_venn_records()`의 같은 bounded plan을 소비합니다.
Plan은 set의 source/portable emitted ID, set과 충돌하지 않는 explicit 또는 deterministic
`intersection_N[_suffix]` Scene ID, canonical membership 순서, exact fixed-decimal value token,
terminal별 source/canvas label과 record-local evidence를 고정합니다. 지수 표기는 방출하지 않습니다.
Set/intersection object 재사용, unknown/repeated member, duplicate canonical intersection, 관측 set/하위
intersection보다 큰 intersection, area·membership resource 초과는 serializer 전에 거부합니다. Malformed
evidence list는 해당 record의 전체 evidence tuple만 비우고 code·topology·다른 provenance는 유지합니다.

Native `venn-beta`는 모든 set/intersection size가 관측되고 positive normal binary64로 exact round-trip되며,
Python `int` 입력의 safe-integer range와 `largest set / smallest positive area <= 200` visibility gate를 만족할 때만 선택합니다.
Intersection이 member set 또는 더 작은 explicit intersection과 같은 exact-containment, zero·subnormal·
overflow·precision-loss value는 renderer timeout이나 invisible area를 피하도록 exact Flowchart로 낮춥니다.
3개 이상 set의 union은 그 union에 포함된 모든 pairwise intersection이 explicit해야 하며 누락 pair나
higher-order area를 암묵적으로 합성하지 않습니다.

Native terminal의 Scene은 set circle과 shape 없는 intersection area, label/marker 없는 logical membership,
`unknown` direction을 사용합니다. Canvas OCR은 visible native title과 실제 set/intersection label만 세며
value는 area geometry input이라 text credit을 받지 않습니다. Flowchart terminal은 set circle,
intersection round, exact ` (value: x)` node suffix, `intersects` relation label, end-arrow, `LR` direction을
사용합니다. Native-only title은 fallback canvas에 복사하지 않고 resolved accessibility text는 SVG metadata로만
남습니다. 두 terminal 모두 generated bbox를 zero로 두며 set/intersection evidence를 element에,
intersection evidence를 각 membership relation에도 연결합니다.

Native runtime rejection은 새 후보 budget 없이 같은 candidate slot에서 이 Flowchart를 한 번 재직렬화하고
strict source scan, parse/render/SVG/terminal-type gate를 다시 적용합니다. Flowchart는 500 membership edge를
넘으면 code와 Scene을 모두 unavailable로 닫지만, 이 한도는 valid native Venn을 제한하지 않습니다. 500-edge
경계는 성능 보장이 아니므로 runtime timeout은 그대로 적용합니다. 두 terminal은 각각 50,000자·5,000줄
source preflight를 통과합니다. Semantic 원문은 typed IR에 보존하고 source-only security separator와
terminal-visible compatibility glyph을 분리하며, visible quote/angle/backslash/hash/semicolon 치환은 candidate
warning 또는 fallback reason에 공개하고 Scene/OCR에도 같은 canvas text를 사용합니다.

Direct serializer의 Sankey `links`, Radar `axes`, Treemap/Venn `name` 호환 입력은 canonical key가 없을 때의
기존 해석을 유지하지만 structured prompt에는 광고하지 않습니다. Sankey native grammar의 접근성 제한은
typed IR warning으로 남습니다. Radar는 shared plan의 native radial/fallback tabular Scene과 record-local
provenance를 sidecar에 기록합니다. Treemap은 누락·중복·잘못된 attribution ID를 reserved-safe slot으로
격리하고, Venn은 set/intersection
전체 namespace에서 collision-safe emitted/Scene ID를 계획합니다. Sankey·Radar·Treemap·Venn의 Scene
attribution과 별개로 네 유형 모두 독립 source OCR/vector numeric evidence gate를 통과해야 자동 게시할 수
있습니다.
