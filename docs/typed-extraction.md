# Typed extraction 계약

Structured VLM은 `diagram_type`만 맞춘 임의 JSON을 내보내지 않습니다. 활성화된 Mermaid 유형마다
root 필드와 container 종류를 고정한 `TypedIRContract`를 prompt로 받고, 응답은 Pydantic 모델 생성
시점에 같은 registry로 다시 검사됩니다. Phase 1 유형, stable Core UML 유형(State, Class, ER),
native Phase 2의 Requirement·Block, C4·Deployment·Component·Use-case fallback, Phase 3 chart인
Pie·XY·Quadrant·Sankey·Radar·Treemap·Venn과 planning 유형인 Journey·Kanban·GitGraph는 record 내부의
알려진 필드와 recursive container도 전용 Pydantic model로 검사합니다. Packet·Ishikawa·TreeView도
bit-range record와 effect/category/cause 또는 root/children 계층을 같은 경계에서 후검증합니다.
Wardley·Cynefin의 positioned component/link·domain/item/transition record도 strict nested
contract 대상입니다. Event Modeling의 lane/frame/relation과 ZenUML의
participant/message fallback record도 같은 nested 경계를 통과합니다.
Organization의 재귀 hierarchy와 Data Lineage의 dataset/process/relation record도
canonical prompt와 응답 후 검증을 공유합니다.
Railroad도 rule과 discriminated recursive expression AST 전체를 같은 경계에서 검사합니다.
serializer의 세부 의미 검사는 그 다음 단계에서 수행합니다.

이 경계는 두 문제를 분리합니다.

- extraction contract는 `sequence` 후보에 `nodes`가 들어오는 식의 유형 간 root 혼동을 빠르게 거부합니다.
- serializer는 endpoint, cardinality, 숫자 근거, 날짜, 허용 문법처럼 유형 내부 의미를 검증합니다.

registry는 `ALL_TYPES`와 정확히 같은 key 집합이어야 하며 누락 또는 여분이 있으면 import 단계에서
실패합니다. Prompt에는 현재 `enabled_types`만 들어가므로 비활성 유형의 schema가 token budget을
소비하지 않습니다. 공통 선택 필드는 `title`, `description`, `acc_title`, `acc_description`,
`direction`이며 semantic node/relation record에는 prior에서 얻은 `evidence_ids`를 요구합니다.
알려진 모든 record의 `evidence_ids`는 prompt와 local nested schema에서 같은 record별 최대 256개
상한을 사용합니다.

## 중첩 계약 적용 범위

다음 유형은 root container 검사 뒤 strict nested model을 통과해야 합니다.

| 유형 | 검사하는 record 구조 |
| --- | --- |
| Flowchart / Generic Network | node, edge, group과 member/evidence list |
| Swimlane / BPMN | lane, lane 안의 node, top-level edge |
| Sequence | participant(`id`/`label`/evidence), message(`id`/`source`/`target`/`label`/closed `style`/evidence); terminal plan 후검증 |
| Mindmap | 재귀 root/children hierarchy |
| Timeline | event `id`, `time`/`period`, single `label`/ordered `events[]`, bbox/evidence; terminal plan 후검증 |
| Gantt | section과 task ID/status/start/end/duration field; terminal plan이 semantic/source/canvas text와 schedule 의미를 후검증 |
| Architecture | service, group, edge/port field |
| State | state/kind와 transition endpoint/label |
| Class | class/member와 relation/cardinality field |
| ER | entity/attribute/key와 relationship/cardinality field |
| Requirement | requirement/element/relation record와 닫힌 type·risk·verify·relation token |
| Block | block/edge record, shape token, `columns` scalar 형식 |
| C4 fallback | element/boundary/relation record, level·kind token, Architecture port side |
| Deployment / Component fallback | service-like primary/secondary record, group, canonical relation과 Architecture port side |
| Use-case fallback | actor/use-case/relation record와 portable Flowchart projection |
| Pie | slice label/value와 `show_data` boolean |
| XY | categorical 또는 numeric axis, line/bar series, numeric point |
| Quadrant | low/high axis, quadrant label, normalized point |
| Sankey | node와 explicit weighted flow |
| Radar | dimension, ordered numeric series, native option scalar |
| Treemap | 재귀 root/children hierarchy와 explicit value |
| Venn | set, intersection membership와 explicit value |
| Journey | section, scored task와 actor list |
| Kanban | column/card와 명시적 column reference |
| GitGraph | ordered commit/branch/merge operation과 닫힌 token |
| Packet | explicit integer bit range와 field label |
| Ishikawa | effect leaf와 재귀 category/cause hierarchy |
| TreeView | 재귀 root/children hierarchy |
| Wardley | positioned component와 explicit link |
| Cynefin | official domain, evidence-bearing item, domain transition |
| Event Modeling fallback | lane, lane 안의 frame, relation endpoint/label |
| ZenUML fallback | participant object·message; legacy string participant는 입력 호환만 지원 |
| Organization fallback | 재귀 root/children reporting hierarchy |
| Data Lineage fallback | dataset, process, relation endpoint/label |
| Railroad | rule과 terminal/nonterminal/special/sequence/choice/optional/repetition expression AST |

### Sequence terminal record 계약

Sequence root는 `participants: list`와 `messages: list`를 요구합니다. Participant는 legacy string 또는
`{id, label, bbox, evidence_ids}` object이고, message는
`{id, source, target, label, style, bbox, evidence_ids}` object입니다. `style`은
`solid|dotted|open|dotted_open|cross`만 coercion 없이 허용합니다. Extra metadata는 typed/review IR에
보존할 수 있지만 participant `text`, raw role/shape/direction과 message arrow hint는 방출 구조나 OCR label로
승격하지 않습니다.

`plan_sequence_records()`는 source identity, source-order `mmx_sequence_participant_N` emitted identity,
ordered `generated-relation-N` Scene slot, semantic/source/Mermaid 11.16 canvas text, endpoint, line/marker 의미를
한 번 고정합니다. Raw message ID는 Mermaid source identity가 아니라 typed/review metadata입니다.
Participant declaration, 모든 message endpoint와 generated Scene element가 같은 emitted mapping을 쓰고,
participant/message evidence는 각각 해당 element/relation에만 유지합니다. String participant는 자체 object
evidence를 제공할 수 없는 legacy 입력입니다.

Duplicate participant, malformed record, unknown/null endpoint, unknown style, resource/source budget 초과 중
하나라도 있으면 message를 생략하거나 partial Sequence로 축소하지 않고 전체 plan을 fail closed합니다.
Missing/`None`/exact-empty participant label은 source ID, message label은 `[unreadable]`로 고정하지만,
whitespace-only/non-string/control/format/surrogate/overlong label은 거부합니다. 문자별 `#35;`/`#59;` source
escape는 literal `#`/`;`와 entity-like text를 exact canvas로 보존하고 source-only separator는 active token만
비활성화합니다. Generated Scene은 `LR`이고 style별 실제 Mermaid marker 의미를 사용합니다.

Top-level `title`/`description`/`acc_title`/`acc_description`은 generic enrichment 전에 raw gate를 통과합니다.
Absent/`None`과 exact-empty omitted만 허용하고 나머지는 exact bounded nonblank UTF-8 text여야 합니다.
Pipeline은 derived `acc_*` 대신 validated raw snapshot을 initial/repair typed IR에 저장해 accepted repair 뒤
현재 participant plan에서 description과 angle compatibility warning을 다시 만듭니다.

### Timeline terminal record 계약

Timeline root는 non-empty `events: list`를 요구합니다. 각 record는
`{id, time, period, label, events, bbox, evidence_ids}` object입니다. `time`/`period`는 period alias이고,
`label`은 single event alias, `events[]`는 ordered multi-event 표현입니다. 두 period alias가 모두 있으면
normalized text가 같아야 하고, `label`과 non-empty `events[]`를 함께 주면 label이 첫 event와 같아야 합니다.
Exact-empty alias는 omitted이며 label이 모두 없으면 `[unreadable]` 한 slot을 유지합니다. Whitespace-only,
non-string, control/format/surrogate, overlong nested value와 malformed list는 coercion하거나 생략하지 않습니다.

`plan_timeline_records()`는 raw/provenance source ID, source-order `timeline_event_N` Scene ID, title/period/event의
semantic/source/Mermaid 11.16 canvas text와 source budget을 한 번 고정합니다. Duplicate source ID, alias conflict,
visible-label/record/source budget 초과가 하나라도 있으면 partial Timeline 대신 전체 plan을 fail closed합니다.
Generated Scene은 record마다 `role=event`, period canvas text, `reading_direction=timeline`과 그 record의
bbox/evidence만 사용합니다. Title, period, 모든 ordered event canvas label은 OCR projection에 들어가지만 raw
ID/role/shape/direction/hidden text, source sentinel/numeric entity와 accessibility metadata는 들어가지 않습니다.

Timeline lexer가 period의 `title`/`section`/comment/delimiter를 grammar로 소비하지 못하도록 각 terminal source에
generated zero-width sentinel을 붙이고 normalized text의 모든 ASCII code point를 numeric entity로 encode합니다.
Mermaid 11.16은 이를 quote/backslash/colon/hash/semicolon/entity-like spelling/whitespace를 포함한 exact canvas
text로 decode합니다. Expanded source는 50,000 UTF-16 unit/5,000줄에서 preflight합니다. Top-level
`title`/`description`/`acc_title`/`acc_description`은 enrichment 전에 exact raw gate를 통과하고 candidate에는
validated raw snapshot이 남습니다. Timeline SVG가 접근성 directive를 materialize하지 않으므로 resolved text는
typed/review metadata와 limitation warning으로만 제공됩니다.

### ER terminal record 계약

ER의 provider prompt와 nested response model은 다음 canonical record를 공유합니다. Root의 `entities`는
필수 list이고 `relationships`는 선택 list입니다.

| Record | Prompt에 공개하고 형을 검사하는 field |
| --- | --- |
| `entities[]` | `id`, `label`, `bbox`, `evidence_ids`, `attributes` |
| `entities[].attributes[]` | `type`, `name`, `keys`, `comment`, `bbox`, `evidence_ids` |
| `relationships[]` | `id`, `source`, `target`, `source_cardinality`, `target_cardinality`, `identifying`, `label`, `bbox`, `evidence_ids` |

`keys`는 `PK|FK|UK`의 list, cardinality는
`one|only_one|zero_or_one|one_or_more|zero_or_more`, `identifying`은 boolean으로 coercion 없이 검사합니다.
Partial reconstruction 호환을 위해 nested model의 개별 scalar는 optional일 수 있지만 serializer plan은 실제
방출 전에 entity ID, attribute type/name/evidence, relationship endpoint/cardinality/identifying/label/evidence를
요구합니다. Unknown endpoint나 누락 cardinality를 보완하거나 partial nodes-only ER로 축소하지 않습니다.

Nested extraction 뒤 `plan_er_records()`가 source identity, collision-safe emitted identity, relation Scene slot,
semantic/source/Mermaid 11.16 canvas text를 한 번 고정합니다. Relationship role은 공백 유무와 관계없이 quoted
terminal 하나로 방출해 trailing word가 entity로 해석되지 않게 합니다. Entity alias, attribute
type/name/comment와 role은 각 grammar 자리에 맞는 compatibility glyph와 source-only neutralization을 적용하며
semantic 원문은 provider 결과와 typed/review IR에 남깁니다. `erDiagram`·style/control keyword·cardinality
keyword·`__proto__` 또는 `iconify` substring과 충돌하는 source ID는 `mmx_er_id_N[_suffix]`로 mapping하고,
serializer relationship endpoint와 generated Scene이 같은 emitted ID를 소비합니다. Entity/relationship
evidence는 해당 Scene element/relation에 유지하고 attribute evidence는 실제 canvas field를 semantic OCR에
추가할 때 사용합니다.

Top-level `title`/`description`/`acc_title`/`acc_description`은 공통 root model을 통과한 뒤에도 ER 전용 raw
gate에서 enrichment 전에 다시 검사합니다. `None`/absent와 exact-empty omitted 외에는 exact built-in string,
raw/normalized bound, non-empty normalized text, valid UTF-8과 허용 Unicode category를 요구합니다. 별도
accessibility plan은 explicit metadata를 우선하고 없을 때 현재 entity semantic label로 default를
파생합니다. Pipeline은 initial/repair typed IR에 derived `acc_*`가 아닌 validated raw snapshot을 보존하므로
accepted structural repair 뒤 stale description이 남지 않고, visible compatibility warning도 새 plan에 맞춰
추가되거나 제거됩니다.

### Organization·Data Lineage fallback record 계약

Organization은 `root: object`를, Data Lineage는 `datasets: list`와
`relations: list`를 필수 root로 사용합니다. Data Lineage의 `processes`는 선택 root이며
누락하면 빈 list로 검증합니다. Provider prompt에는 다음 canonical record만
공개합니다.

| Type | Record | Prompt에 공개하고 형을 검사하는 field |
| --- | --- | --- |
| Organization | `root` / `children[]` | `id`, `label`, `bbox`, `evidence_ids`, `children` |
| Data Lineage | `datasets[]` | `id`, `label`, `bbox`, `evidence_ids` |
| Data Lineage | `processes[]` | `id`, `label`, `bbox`, `evidence_ids` |
| Data Lineage | `relations[]` | `source`, `target`, `label`, `bbox`, `evidence_ids` |

Organization의 기존 `name`은 TreeView sidecar/fixture 호환을 위해 응답 후 검증에서는
받지만 provider prompt에는 노출하지 않습니다. `role`, `shape`, `style`,
`bidirectional`, raw relation ID 같은 미등록 metadata는 원본 IR에 보존될 수
있어도 fallback 구조로 승격하지 않습니다. 공통 root의 `direction`은
Organization에서는 무시하고 Data Lineage에서는 닫힌 방향 값으로 검증해 실제
Flowchart와 Scene에 사용합니다. 기존 partial/direct IR 호환을 위해 Organization의
누락 ID는 preorder `node_N`으로 보완하고 Data Lineage의 누락 label은 검증된
source ID를 그대로 사용합니다. Organization label/name과 Data Lineage ID는 필수이며,
ID 충돌은 두 planner가, relation endpoint·self-loop·중복은 Data Lineage planner가
판정합니다. Organization
relation은 explicit record가 아니라 검증된 `children`에서만 파생합니다. source 코드
예산은 두 공유 planner/serializer 경계에서 판정합니다.
Data Lineage edge label의 `|`, `;`, `()`, `[]`, `{}`, `@`는 Mermaid 11.16의 unquoted
edge-label grammar과 충돌하므로 실제 SVG에 보이는 compatibility glyph로 치환하고
warning·Scene·OCR에 같은 손실을 기록합니다.

### Railroad recursive AST 계약

Railroad는 `rules: list`를 필수 root로 사용하며 각 rule은 `name`, `definition`, `bbox`,
`evidence_ids`를 canonical field로 갖습니다. `definition`은 `type` discriminator로 다음 exact
object 중 하나를 재귀적으로 선택합니다.

| `type` | canonical payload |
| --- | --- |
| `terminal` | `value: string` |
| `nonterminal` | `name: string` |
| `special` | `text: string` |
| `sequence` | `elements: expression[]` |
| `choice` | `alternatives: expression[]` |
| `optional` / `one_or_more` / `zero_or_more` | `element: expression` |

Provider prompt에는 이 lowercase closed token과 variant별 payload만 광고합니다. Expression 대신
scalar/list를 넣거나 선택된 variant의 알려진 payload field에 잘못된 container/scalar 형을 넣으면 응답 후
strict nested validation에서 거부합니다. 다른 variant의 field는 unknown metadata로 원본에 보존됩니다.
Partial reconstruction을 위해 rule `name`/`definition`과 expression payload 자체는 model 경계에서
선택이거나 null일 수 있고 serializer가 실제 requiredness를 판정합니다. 각 expression에도 bbox/evidence를
둘 수 있으며 discriminator와 variant-local known scalar/container의 형은 coercion하지 않습니다.

Serializer plan 단계에서는 non-empty `rules`, 각 rule의 `name`/`definition`, non-empty expression
container가 필수입니다. 이어서 rule name uniqueness, emitted-ID normalization, native namespace collision
avoidance, scanner/preprocessor source-active 또는 native grammar-reserved rule-name mapping, 모든
nonterminal reference 해결, 최대 깊이 20, rule/expression 각각 500개 한도와 50,000자·5,000줄 source
budget은 serializer와 generated Scene이 공유하는 bounded plan이 계속 판정합니다. Mapping으로 visible
rule name이 달라지면
warning을 남깁니다. 따라서 extraction model은 문법 AST의 형을 확정하고,
reference·identity·resource 의미 검사는 한 번 만든 plan에서 serializer와 attribution에 동일하게
적용됩니다.

Rule/nonterminal name은 whitespace normalization 뒤 ASCII Mermaid identifier
`[A-Za-z_][A-Za-z0-9_-]{0,127}`이어야 합니다. Terminal/special/title/accessibility 등 text도 whitespace를
정규화하고 field당 500자로 제한합니다. 이 serializer-visible normalized text와 별개로 raw field는 원본
typed IR/sidecar에 유지됩니다.

Pinned Mermaid 11.16에 넣는 canonical visible text는 ASCII `<`/`>`를 `〈`/`〉`로, 모든 ASCII `#`를
`＃`로, entity-like `&` prefix를 `＆`로 바꿉니다. 이는 전역 `encodeEntities`가 bare `#word;`와 `#35;`도
변형하는 동작까지 포함합니다. NFKC에서 quote/backslash 문법으로 되돌아오는 호환 문자는 각각
`″`/`∖`로 바꿉니다. 원 semantic field는 typed IR/sidecar에 유지하고 shared plan은 이 compatibility text를
serializer·Scene·OCR에 동일하게 제공하며 치환을 compatibility warning으로 공개합니다. Active token을
끊는 zero-width separator는 emitted source에만 존재하며 `style...:#...;`/`classDef...:#...;`로 해석될 수
있는 preprocessor substring도 source에서 분리합니다. 반환 전 원 source와 NFKC-normalized source를 모두
strict scanner로 검사합니다.

Source identifier로 안전하지 않은 이름뿐 아니라 native grammar의 case-folded expression-word namespace
(`terminal`, `nonterminal`, `special`, `sequence`, `choice`, `optional`, `oneOrMore`, `zeroOrMore`),
`railroad-beta`, 대소문자를 접은 뒤 lowercase `title*`인 이름도 collision-safe
`rrmapped_N[_suffix]`로 바꿉니다. Scanner 또는 Mermaid preprocessor에서 source-active인 이름,
즉 `style`/`classDef` substring을 포함하는 이름도 같은 mapping 대상입니다. 원 이름은 typed IR과
nonterminal visible label에 남습니다.

### Event Modeling·ZenUML fallback record 계약

Event Modeling은 `lanes: list`를, ZenUML은 `participants: list`와 `messages: list`를
필수 root로 사용합니다. Provider prompt에는 다음 canonical record만 공개합니다.

| Type | Record | Prompt에 공개하고 형을 검사하는 field |
| --- | --- | --- |
| Event Modeling | `lanes[]` | `id`, `label`, `bbox`, `evidence_ids`, `frames` |
| Event Modeling | `lanes[].frames[]` | `id`, `type`, `label`, `time`, `bbox`, `evidence_ids` |
| Event Modeling | `relations[]` | `source`, `target`, `label`, `bbox`, `evidence_ids` |
| ZenUML | `participants[]` | `id`, `label`, `bbox`, `evidence_ids` |
| ZenUML | `messages[]` | `source`, `target`, `label`, `bbox`, `evidence_ids` |

Event Modeling frame `type`은 serializer가 수용하는 `command`, `event`, `readmodel`,
`processor`, `ui`, `unknown`만 허용합니다. 대소문자는 구분하지 않고 검사하지만
입력 문자열은 재작성하지 않습니다. `swimlanes`·`nodes`, frame `name`·`timestamp`,
`cmd`·`evt` 같은 alias는 canonical prompt에 넣지 않습니다.

ZenUML의 기존 string participant는 기존 sidecar/fixture 호환을 위해 응답 후 검증에서
받지만, bbox/evidence를 연결할 수 없으므로 provider에는 object record만 요청합니다.
Message의 raw `id`·`style`과 participant의 `text` 같은 미등록 metadata는 원본 IR에
보존되어도 fallback node/relation 구조로 승격하지 않습니다. 두 유형 모두 field를
partial로 검증하며 non-empty label, ID 충돌과 endpoint 해결은 공유 serializer plan이
판정합니다. 50,000자·5,000줄 source budget은 코드 생성 후 serializer 반환 경계에서
최종 판정합니다.

### Requirement·Block record 계약

Provider prompt와 응답 후 중첩 검증이 공유하는 canonical record는 다음과 같습니다. Root에서
Requirement는 `requirements: list`를, Block은 `blocks: list`를 필수로 요구합니다. Requirement의
`elements`·`relations`와 Block의 `edges`·`columns`는 선택 root field입니다.

| Record | Prompt에 공개하고 형을 검사하는 field |
| --- | --- |
| `requirements[]` | `id`, `requirement_id`, `text`, `label`, `type`, `risk`, `verify_method`, `bbox`, `evidence_ids` |
| `elements[]` | `id`, `type`, `label`, `docref`, `bbox`, `evidence_ids` |
| `relations[]` | `id`, `source`, `target`, `type`, `bbox`, `evidence_ids` |
| `blocks[]` | `id`, `label`, `text`, `shape`, `bbox`, `evidence_ids` |
| `edges[]` | `id`, `source`, `target`, `label`, `style`, `bidirectional`, `bbox`, `evidence_ids` |

닫힌 token 집합은 serializer가 이미 받아들이는 값과 같습니다.

- Requirement `type`: `requirement`, `functional`, `functional_requirement`, `interface`,
  `interface_requirement`, `performance`, `performance_requirement`, `physical`,
  `physical_requirement`, `design_constraint`
- Requirement `risk`: `low`, `medium`, `high`
- Requirement `verify_method`: `analysis`, `demonstration`, `inspection`, `test`
- Requirement relation `type`: `contains`, `copies`, `derives`, `satisfies`, `verifies`,
  `refines`, `traces`
- Block `shape`: `rectangle`, `round`, `stadium`, `circle`, `diamond`, `hexagon`, `cylinder`,
  `subroutine`

이 token들은 serializer의 lowercase 해석과 맞추어 대소문자를 구분하지 않고 검증하며,
입력 문자열의 casing은 바꾸지 않습니다. Legacy Requirement 필드 `verifymethod`도
`verify_method`와 같은 token 집합으로 응답 후 검증하지만 canonical prompt에는 추가하지
않습니다. `relations[].label`도 string compatibility metadata로 형을 검사하지만 prompt와
Requirement Mermaid output에는 포함하지 않습니다. Requirement element의 `type`·`label`, Block edge의
`style`과 같은 자유 text field는 scalar 형만 여기서 검사하고 최종 표현 가능성은
serializer가 판정합니다.

Block `columns` prompt는 `auto|integer`를 요구합니다. Nested model은 이 경계에서
string·integer·null이라는 scalar 형을 먼저 검사하고, serializer가 누락을 `auto`로 처리하며
명시값이 `auto` 또는 양의 정수로 해석 가능한지 최종 판정합니다. 즉 extraction 계약의
구조 검사와 serializer의 의미 검사를 혼동하지 않습니다.

### C4 fallback record 계약

C4 root는 `elements: list`가 필수이고 `boundaries`·`relations`·`level`은 선택입니다. Prompt는 다음
네 record를 이 순서로 광고합니다.

| Record | Prompt에 공개하고 형을 검사하는 field |
| --- | --- |
| `level` | `context`, `container`, `component` |
| `elements[]` | `id`, `label`, `name`, `kind`, `boundary`, `description`, `technology`, `bbox`, `evidence_ids` |
| `boundaries[]` | `id`, `label`, `type`, `bbox`, `evidence_ids` |
| `relations[]` | `id`, `source`, `target`, `label`, `technology`, `bidirectional`, `source_side`, `target_side`, `bbox`, `evidence_ids` |

Element `kind`의 canonical token은 `person`, `external_person`, `system`, `external_system`,
`database`, `external_database`, `queue`, `external_queue`, `container`, `container_database`,
`container_queue`, `component`, `component_database`, `component_queue`입니다. `level`과 `kind`는
serializer의 lowercase 해석과 같이 대소문자를 구분하지 않고 검사하되 원본 문자열을 바꾸지 않습니다.
Legacy element `type`도 `kind`와 같은 집합으로 응답 후 검사하고 serializer fallback을 위해 원본 IR에
보존하지만 canonical prompt에는 광고하지 않습니다. Relation의 `source_side`와 `target_side`는
Architecture fallback이 실제 소비하는 대문자 `L`, `R`, `T`, `B`만 허용하며 `bidirectional`은 strict
boolean입니다.

자동 게시 경로가 실제 소비하는 값은 element ID·`label`/`name`·kind 기반 icon·boundary membership,
boundary ID·label, relation endpoint·port side·`bidirectional`입니다. Architecture output은 icon과 port
side를 사용하고, nested Flowchart retry는 같은 ID·label·membership·endpoint·bidirectional topology만
보존합니다. Element bbox/evidence, boundary bbox와 relation evidence도 attribution에 유지됩니다. Boundary
`type`은 의도적으로 닫힌 C4 boundary token으로
제한하지 않고 string 형만 검사합니다. 자동 fallback은 boundary notation 자체를 표시하지 않으므로,
진단용 native C4가 모르는 boundary type 때문에 안전한 Architecture/Flowchart fallback까지 거부하던 호환성
축소를 피하기 위함입니다. Element `description`·`technology`, relation `label`·`technology`·bbox와 exact
boundary notation은 typed IR/review metadata에는 보존되지만 자동 fallback의 node/edge label이나 attribution
geometry로 승격되지 않습니다. `serialize_c4_native`가 이 metadata를 표현할 수 있어도 해당 함수는 trusted
diagnostic 전용이며 자동 publication 또는 품질 평가 경로가 아닙니다.

Nested model은 record/container/scalar와 닫힌 token만 검사합니다. 빈 elements list, ID 정규화와 collision,
boundary membership/reference, relation endpoint, resource cap 같은 의미 조건은 자동 serializer와 generated
Scene이 공유하는 bounded C4-to-Architecture plan이 계속 판정합니다. 각 record field와 `evidence_ids`는
partial/legacy 후보를 위해 선택이고, `bbox`·evidence의 strict 형은 공통 record 계약을 따릅니다. 등록하지
않은 metadata는 `extra="allow"`로 남고 검증 model이 입력을 대체하지 않으므로 casing, legacy `type`, extra
field를 포함한 원본 dict가 serializer·repair·canonical hash·sidecar로 전달됩니다.

### Deployment·Component Architecture fallback 계약

Deployment는 `nodes: list`, Component는 `components: list` root가 필수입니다. Deployment의 canonical
선택 root는 `artifacts`·`groups`·`links`, Component는 `interfaces`·`groups`·`dependencies`입니다.
두 nested model 모두 legacy `edges`를 compatibility field로 검사하지만 root contract와 활성 type의
canonical prompt에는 광고하지 않고 다음 record만 공개합니다.

| Type | Prompt record | 공개 field |
| --- | --- | --- |
| Deployment | `nodes[]`, `artifacts[]` | `id`, `label`, `name`, `icon`, `group`, `bbox`, `evidence_ids` |
| Deployment | `groups[]` | `id`, `label`, `icon`, `bbox`, `evidence_ids` |
| Deployment | `links[]` | `id`, `source`, `target`, `label`, `bidirectional`, `source_side`, `target_side`, `bbox`, `evidence_ids` |
| Component | `components[]`, `interfaces[]` | `id`, `label`, `name`, `icon`, `group`, `bbox`, `evidence_ids` |
| Component | `groups[]` | `id`, `label`, `icon`, `bbox`, `evidence_ids` |
| Component | `dependencies[]` | `id`, `source`, `target`, `label`, `bidirectional`, `source_side`, `target_side`, `bbox`, `evidence_ids` |

Node와 artifact 또는 component와 interface는 각 목록 내부 순서를 유지하면서 primary record 뒤에
secondary record를 붙인 하나의 Architecture service 목록으로 평탄화됩니다. 각 record 자체는
collision-safe service ID와 `label` → `name` → source ID 순서의 표시
label을 얻지만, artifact containment·stereotype 또는 provided/required interface notation은 canonical
field가 아니며 원본 extra metadata에만 남습니다. Link/dependency의 `label`, raw relation ID와 bbox도
typed IR/review metadata이며 자동 Mermaid edge에는 표시되지 않습니다. Source/target, strict boolean
`bidirectional`, 대문자 `L/R/T/B` port side만 Architecture topology가 소비하고 relation evidence는 generated
Scene attribution에 유지됩니다.

Service-like `icon`은 string 형을 검사하지만 닫힌 token으로 거부하지 않습니다. Serializer가
case-insensitive로 `cloud`, `database`, `disk`, `internet`, `server`를 사용하고 그 밖의 값은 `server`로
낮춥니다. Group icon도 string metadata로 보존하며 누락 시 Architecture 기본값을 사용합니다. Group ID·label과
service `group`은 공용 Architecture structure plan에서 실제 membership으로 검사·방출됩니다.
Architecture output은 service/group icon과 relation port side를 사용하지만 runtime이 이를 거부해 nested
Flowchart로 재시도하면 같은 service/group ID·label·membership과 무라벨 endpoint/bidirectional topology만
남고 icon·port side는 typed IR에 보존됩니다.

Canonical relation collection이 root에 존재하면 비어 있어도 우선합니다. 따라서 Deployment `links`가
있으면 legacy `edges`를 합치거나 되살리지 않고, Component `dependencies`가 있으면 `edges`를 사용하지
않습니다. Canonical collection이 아예 없을 때만 `edges`를 compatibility alias로 읽습니다. Nested model은
legacy alias도 같은 strict relation 형으로 검사하지만 provider prompt에는 canonical collection만
요구합니다. 검증 결과는 원본 dict에 default list를 삽입하지 않으므로 이 key-presence 우선순위가 바뀌지
않습니다.

각 record field와 evidence는 partial/legacy reconstruction을 위해 선택이고 등록하지 않은 metadata는
`extra="allow"`로 보존됩니다. Known scalar/container, strict bbox/evidence, port와 boolean 형만 extraction
경계에서 확인하며, 결합된 service 목록의 non-empty 조건, ID/group collision, group reference, endpoint,
resource cap은 record ID planner와 공용 Architecture structure plan이 최종 판정합니다. Serializer와
generated Scene은 이 planner의 emitted identity/topology를 따르지만 별도의 Deployment/Component native
notation을 만들지는 않습니다.

### Use-case Flowchart fallback 계약

Use-case root는 `actors: list`와 `use_cases: list`가 모두 필수이고 `relations`가 선택입니다. Canonical
prompt는 다음 record만 공개합니다.

| Prompt record | 공개 field |
| --- | --- |
| `actors[]` | `id`, `label`, `name`, `bbox`, `evidence_ids` |
| `use_cases[]` | `id`, `label`, `name`, `bbox`, `evidence_ids` |
| `relations[]` | `id`, `source`, `target`, `type`, `label`, `bbox`, `evidence_ids` |

Actor와 use-case record는 공용 bounded plan에서 하나의 collision-safe namespace를 얻습니다. 표시 label은
`label` → `name` → source ID 순서이며, actor는 portable stadium proxy, use case는 별개의 round node로
방출됩니다. 이것은 Mermaid 11.16 Flowchart 표현이지 UML actor glyph나 native Use-case notation이 아닙니다.
입력 `groups`와 system boundary metadata는 원본 typed IR/extra metadata에 남더라도 serializer와 generated
Scene에서 명시적으로 억제됩니다.

Relation `type`과 `label`은 닫힌 UML enum이 아닌 open string입니다. `type`이 비어 있지 않으면 이를 edge
label로 사용하고, 없을 때만 `label`을 사용하며 둘 다 없으면 unlabeled edge를 만듭니다. Raw relation ID,
`bidirectional`, arrow hint, style과 semantic metadata는 자동 Flowchart에 반영하지 않습니다. 모든 relation은
공용 plan의 정확한 source/target을 갖는 일반 단방향 connector이고 generated Scene도 같은 deterministic
relation 순서·label·endpoint를 사용합니다. Node bbox는 generated Scene의 source 위치로 유지되지만 Mermaid
layout을 지시하지 않고, relation bbox는 typed IR/review metadata에만 남습니다. `evidence_ids`는 visible
node/relation의 generated Scene attribution에 유지됩니다.

Nested contract는 actor/use-case/relation이 object이고 위 known scalar, strict bbox와 string evidence list의
형을 갖는지만 검사합니다. 각 record field와 evidence는 partial/legacy reconstruction을 위해 선택이고
미등록 field는 `extra="allow"`로 원본 dict에 보존됩니다. 두 root list의 non-empty 조건, actor와 use-case
source ID 분리, normalization 및 `usecase_` prefix 뒤의 2차 collision, relation endpoint, node/relation cap은
`plan_usecase_fallback`과 serializer가 최종 판정합니다. 기본 방향은 `LR`이고 허용되지 않은 값은 portable
Flowchart와 generated Scene에서 `TB`로 정규화됩니다.

### Pie·XY·Quadrant terminal 계약

세 core chart는 다음 root와 record를 provider prompt에 정확히 공개하고 응답 후 같은 strict nested
model로 검사합니다.

| Type | 필수 root | 선택 root | Prompt record |
| --- | --- | --- | --- |
| Pie | `slices: list` | `show_data` | `show_data: boolean`; `slices[]: {label:string,value:number,bbox:number[4],evidence_ids:string[]}` |
| XY | `x_axis: object`, `y_axis: object`, `series: list` | 없음 | `x_axis`의 `label`·`categories`·`min`·`max`; `y_axis`의 `label`·`min`·`max`; `series[]`의 `kind:line\|bar`·`values`·`points`; `points[]`의 `x`·`y`; 각 record의 bbox/evidence |
| Quadrant | `x_axis: object`, `y_axis: object`, `points: list` | `quadrants` | 축의 `low`·`high`; `quadrants: string[4]\|{quadrant-1:string,quadrant-2:string,quadrant-3:string,quadrant-4:string}`; point의 `label`·`x`·`y`; 각 record의 bbox/evidence |

Structured extraction의 `number`는 strict finite JSON `int` 또는 `float`입니다. Boolean, 숫자 문자열,
NaN과 Infinity는 거부합니다. 직접 serializer API가 내부적으로 `Decimal`도 받을 수 있다는 사실은 provider
응답 계약을 넓히지 않습니다. XY `kind`는 `line`/`bar`를 대소문자 구분 없이 검사하지만 입력 casing은
바꾸지 않습니다. Root container를 제외한 chart record field와 evidence는 partial candidate 호환을 위해
model에서 선택입니다.

Nested contract는 형만 확정합니다. Pie의 non-empty·고유 label·non-negative value·positive total, XY의
축 mode와 bounds·모든 y의 범위 포함·series 선택 및 길이, Quadrant의 non-empty 고유 label과
`[0,1]` 좌표 및 같은 quadrant slot의 alias 충돌 같은 completeness/표현 가능성은 serializer가 계속
fail closed로 판정합니다. XY와 Quadrant의 valid하지만 native-lossy한 binary64·renderer geometry·visibility
조건은 completeness 실패와 구분하여 exact-value Flowchart로 낮춥니다.

Pie는 contract를 통과한 raw record를 `PiePlan`에서 다시 bounded snapshot으로 만들고 serializer, generated
Scene, semantic OCR이 함께 소비합니다. 최대 12개 slice, zero-or-normal binary64 round-trip, JavaScript
left-to-right finite positive total, positive slice별 1% visibility와 finite centroid, `show_data=true`일 때 exact
JavaScript `String(value)` 표시를 모두 만족해야 native `pie`입니다. Zero slice는 legend-only로 유지됩니다.
Native 조건 밖의 valid IR 또는 native runtime rejection은 최대 256개의 edge 없는 exact-value
`flowchart TB`로 같은 candidate slot에서 재검증됩니다. 따라서 extraction completeness 오류는 계속 실패하지만
renderer 표현 손실 때문에 exact source 값을 버리지는 않습니다. MMX-001의 missing/unreadable value용
structured table + description fallback은 아직 후속 작업입니다.

Pie slice의 bbox와 `evidence_ids`는 generated `pie_slice_N` element의 record-local provenance로 연결됩니다.
Native terminal은 positive slice의 normalized centroid/sector와 zero slice의 zero bbox를 사용하고, Flowchart는
zero-geometry exact-value rectangle을 사용합니다. 두 terminal 모두 relation/group을 만들지 않습니다. Slice
label/value 자동 검증은 각 non-overlapping slice bbox 내부의 candidate-authorized, slice-cited OCR/vector
observation이 punctuation-preserving 전체 label·허용 separator·exact value record를 이루고 전체
source/generated numeric occurrence도 exact하도록 요구합니다. Label suffix/value/slice가 누락되거나 evidence가
shared/ambiguous/invalid/over-budget이면 candidate를 review에 둡니다. Typed value 또는 evidence ID의 존재만으로
source gate를 충족하지 않습니다. Explicit title/accessibility text도 독립 spatial exact evidence 또는
reconstruction 초기 입력의 exact `user_edit` provenance가 필요하며, engine-emitted `user_edit`는 스스로 승인
근거가 될 수 없습니다. 결정적으로 파생한 기본 accessibility text만 예외입니다.

Pie source는 native slice quote/backslash를 canvas에서 보존하면서 scanner/entity-active token에 source-only
separator를 사용합니다. Native title과 Flowchart cell에 필요한 visible compatibility glyph는 warning으로
공개하고 semantic 원문은 typed IR/review metadata에 보존합니다. 두 terminal은 Mermaid JavaScript
`text.length`와 같은 50,000 UTF-16 code-unit 및 5,000 line preflight를 공유합니다.

XY contract를 통과한 raw record는 `XYPlan`에서 axis·series·point source identity, fixed-decimal x/y,
record-local evidence, terminal별 text와 normalized native geometry로 다시 snapshot됩니다. Native는 zero-or-normal
binary64 exact round-trip, positive normal finite axis span, bounded renderer x-loop의 exact count/endpoint/progress,
보이는 2-point 이상 line·positive-height bar, 10-series palette cap을 모두 요구합니다. Non-uniform
explicit x, last-point drop/stalled loop, 겹치는 duplicate line/multiple bar, y-minimum bar는 원본 값을 버리지 않고
최대 256 point의 disconnected `flowchart TB`로 낮춥니다. Categorical fallback은 각 value cell에
category를 같이 넣고 explicit point fallback은 exact x/y를 넣으며 추정 edge를 만들지 않습니다.
Native runtime rejection도 같은 candidate slot의 전체 Flowchart 재검증으로 처리하고 두 terminal은
50,000 UTF-16 code-unit·5,000 line preflight와 compatibility warning을 공유합니다.

Native generated Scene은 normalized axis/category anchor, text 없는 data point/bar, marker-less adjacent line relation을
반영하고 OCR은 visible title·axis·category만 세어 hidden value를 canvas text로 가정하지 않습니다.
Flowchart Scene/OCR은 title·axis·category·exact data cell을 emitted 순서 그대로 반영하고 relation/group을
만들지 않습니다. 자동 게시는 axis/series/explicit point 각 record의 bbox 안에서 candidate-authorized
OCR/vector observation이 전체 label/category/value/x-y 결합을 증명하고 전역 numeric occurrence도 일치할
때만 허용합니다. Record/value/x swap, 공유 observation, invalid bbox, missing evidence는 review입니다.
Explicit title/accessibility text는 data-owned bbox와 겹치지 않는 independent exact OCR/vector 근거 또는
reconstruction 초기 exact `user_edit`를 요구하며 engine-emitted edit는 스스로 승인 근거가 되지 않습니다.

Quadrant contract를 통과한 raw record는 `QuadrantPlan`에서 두 axis, supplied slot과 point source identity,
exact fixed-decimal coordinate, terminal별 text와 normalized geometry로 snapshot됩니다. Point는 최대 256개이며
axis/point object reuse를 거부합니다. Native는 zero-or-normal binary64 round-trip뿐 아니라 pinned Mermaid
11.16의 500×500 canvas에서 point·label·slot·axis·title이 finite하고 분리되어 보이는지를 100,000회 이하의
비교로 확인합니다. Duplicate/near point, float collapse, subnormal과 clipping/occlusion 위험은 exact
`flowchart TB`로 낮춥니다. Fallback은 optional title, 두 axis, supplied slot과 모든 `label · x X, y Y`
cell만 만들고 edge나 위치를 추정하지 않습니다. Native runtime rejection도 같은 candidate slot에서 이
Flowchart를 전체 재검증하며 두 terminal은 50,000 UTF-16 code-unit·5,000 line limit을 공유합니다.

Native generated Scene은 visible axis endpoint 네 개, `(x, 1-y)` normalized point circle과 네 quadrant
region group을 표현하되 axis line·group membership·connector는 만들지 않습니다. Fallback Scene은 emitted
cell을 zero geometry로 그대로 반영합니다. Axis와 point의 bbox/`evidence_ids`는 record-local provenance와
label/coordinate 결합 gate에 사용하지만, schema에 독립 evidence가 없는 slot label은 point/axis evidence를
상속하지 않습니다. X axis는 horizontal·아래쪽, y axis는 vertical·왼쪽인 source bbox의 상대 geometry도
검증해 전체 record 교환을 막습니다. 자동 게시하려면 supplied slot마다 해당 source quadrant의 독립 exact OCR/vector 또는
유효한 source-quadrant bbox를 가진 초기 exact `user_edit` 근거가 있어야 하고, explicit
title/accessibility text도 data bbox와 분리된 근거가 필요합니다. Direct Quadrant, record/observation reuse,
swap, invalid geometry와 공유 100,000회 association budget 초과는
review-only입니다. 공통 accessibility root와 미등록 metadata는 `extra="allow"`로 원본 dict에 보존되지만 그
숫자나 evidence ID만으로 source numeric gate를 충족하지 않습니다. Slot 위치는 detected plot이 아니라 전체
crop midpoint로 보수적으로 판정하므로 off-center plot은 review가 필요할 수 있습니다. Explicit metadata
관측은 target-role field가 없는 현재 evidence schema에서 content existence만 증명하므로 best-effort에는
limitation warning을 남기고 strict validated에는 자동 게시 권한을 주지 않습니다.

### Sankey·Radar·Treemap·Venn chart 계약

나머지 Phase 3 chart도 canonical root와 nested record를 provider prompt에 공개하고 같은 model로 응답을
검사합니다.

| Type | 필수 root | 선택 root | Prompt record |
| --- | --- | --- | --- |
| Sankey | `nodes: list`, `flows: list` | 없음 | node의 `id`·`label`; flow의 `source`·`target`·`value`; 각 record의 bbox/evidence |
| Radar | `dimensions: list`, `series: list` | `min`, `max`, `ticks`, `show_legend`, `graticule` | dimension의 `id`·`label`; series의 `id`·`label`·ordered `values`; 각 record의 bbox/evidence |
| Treemap | `root: object` | 없음 | 재귀 node의 `id`·`label`·`value`·`children`과 bbox/evidence |
| Venn | `sets: list`, `intersections: list` | 없음 | set의 `id`·`label`·`value`; intersection의 `id`·`sets`·`label`·`value`; 각 record의 bbox/evidence |

Structured extraction은 Sankey의 canonical `flows`와 Radar의 canonical `dimensions`만 광고합니다. Direct
serializer는 canonical key가 없을 때 legacy `links`와 `axes`를 읽지만, 이 alias는 structured contract의
필수 root를 대신하지 못하고 prompt에도 노출되지 않습니다. Treemap `name`과 Venn set/intersection
`name`도 `label` compatibility metadata로 형을 검사해
원본 dict에 보존하되 canonical prompt에는 광고하지 않습니다. 검증 model은 이 alias를 canonical key로
복사하거나 누락 collection을 default list로 삽입하지 않으므로 기존 key-presence 우선순위가 바뀌지 않습니다.

Chart number는 core chart와 같은 strict finite JSON `int`/`float`입니다. Sankey flow weight, Radar series와
bounds, Treemap value, Venn set/intersection value에 boolean·숫자 문자열·NaN·Infinity를 넣으면 candidate
경계에서 거부합니다. Radar `ticks`는 strict integer, `show_legend`는 strict boolean이고 `graticule`은
serializer가 소비하는 exact `circle|polygon` token입니다. 최소 record 수, 누락 semantic field, ID·label
고유성, endpoint/member reference, series 길이와 bounds, 양수/비음수 조건, hierarchy cycle/depth/resource
budget과 Radar의 `ticks <= 100` render resource cap은 serializer가 계속 fail closed로 판정합니다. 특히 음수
Radar domain, non-positive/cyclic Sankey,
internal value가 있는 Treemap, 일부 size가 없는 Venn은 구조 오류가 아니라 문서화된 Flowchart fallback을
선택할 수 있으므로 extraction model에서 금지하지 않습니다.

Radar의 serializer-owned 의미 검사는 dimension 3~256개, aligned series, 전체 Scene/point 예산,
native 12-series, fallback 256-point, 50,000 UTF-16 code-unit·5,000줄 source 경계를 함께 적용합니다. Native value와 explicit
bound는 zero 또는 normal binary64 exact round-trip, positive finite effective span과 finite renderer radius를
만족해야 하며 음수·subnormal·overflow·precision loss·zero/non-finite span은 exact-value Flowchart를 선택합니다.
Radar grammar 예약어를 포함한 dimension/series ID는 native axis/curve와 fallback group/cell 전체 namespace에서
reserved-safe collision suffix를 배정합니다. Native runtime rejection도 같은 candidate slot의 bounded fallback만
허용합니다.

Sankey·Radar·Treemap·Venn record의 ID와 evidence는 generated Scene attribution에 사용됩니다. Radar native
Scene은 source bbox 대신 normalized radial axis/data-point와 point-derived series envelope, closed curve
relation을, fallback은 zero-geometry `TB` visible-title node, 조건부 label의 series group, value cell과 빈
relation list를 사용합니다. Dimension/series evidence는 point에서 bounded
deduplicated union으로 결합하고 series evidence는 native curve relation에도 연결합니다.
Native point는 independently emitted node가 아니라 curve에서 파생되므로 generated-node provenance 분모는
axis/series만 사용합니다. Flowchart value cell은 dimension과 series를 결합한 projection이므로 각 cell에 source
evidence를 독점시키지 않고, 모든 dimension/series record를 별도의 owner-local gate에서 검증합니다. Dimension
label과 그 공간 순서, series label과 ordered value sequence가 candidate-authorized bbox-contained OCR/vector
observation에 정확히 결합되고 전역 숫자 occurrence도 일치해야 `numeric_consistency=1.0`입니다. Cross-owner
evidence/normalized-observation 재사용, 같은 bbox의 uncited contradiction, overlapping/invalid geometry, missing
typed plan 또는 bounded reference/text/token/spatial work 초과는 native·Flowchart·repair 모두 review로 닫습니다.
Visible title과 non-derived explicit accessibility metadata는 data record가 소유하지 않은 candidate-authorized
spatial OCR/vector exact observation 또는 approved initial user edit로 별도 attribution되어야 합니다.
Sankey 자동 게시는 각 planned flow의 exact `value_text`가 source image 안에서 서로 양의 면적으로 겹치지 않는
flow bbox에 완전히 포함된 candidate-authorized OCR/vector observation으로 증명되고 전역 numeric
occurrence도 exact할 때만 허용합니다. Evidence ID나 normalized text+bbox의 cross-flow 재사용, 같은 bbox의
상충 관측, weight swap,
invalid geometry와 bounded association work 초과는 native·same-slot Flowchart·semantic repair를 모두
review로 닫습니다. Direct 또는 typed flow plan이 없는 Sankey도 flow-local owner binding을 만들 수 없어
review-only입니다.

Sankey의 네 raw explicit metadata field(`title`, `description`, `acc_title`, `acc_description`)는 accessibility
enrichment 전에 별도로 검증합니다. Pipeline은 provider가 반환한 typed candidate의 raw snapshot에서, public
typed serializer는 caller가 넘긴 raw IR에서 같은 검사를 수행합니다. Non-`None` 값은 exact built-in `str`만
허용합니다. Whitespace 정규화 전에 raw 길이를 `MAX_TEXT_CHARS`로 제한하고, 호환용 exact `""` 외의 문자열은
정규화 후에도 non-empty·bounded·valid UTF-8이어야 하며 정규화된 text에 Unicode category
`Cc`/`Cf`/`Zl`/`Zp` 문자가 없어야 합니다. String subclass·숫자·container뿐 아니라 huge-whitespace를 포함한
overlong raw/normalized text, whitespace-only, ZWSP/control-only, lone-surrogate 입력도 provider별 Mermaid
serialization이나 runtime validation 전에 fail closed입니다. JSON `null`은 absent로 취급합니다. Pie/XY의
기존 input compatibility를 따라 exact `""`은 허용하지만 omitted로 resolve하며, 따라서 빈 explicit metadata가
아니라 deterministic accessibility text가 파생됩니다.

Sankey의 explicit accessibility metadata는 terminal별로 판정합니다. Native Sankey는 title/description을
방출하지 않으므로 이 귀속 검사를 요구하지 않습니다. Same-slot Flowchart fallback은 resolved title과
description을 SVG metadata로 방출하며 content OCR label로 세지 않습니다. `acc_title`이 `title`을,
`acc_description`이 `description`을 output에서 shadow하면 숨겨진 legacy text는 면제합니다. 실제 방출되는
non-derived resolved title과 description 두 역할은 서로 독립적으로, 어떤 node/flow data record도 소유하지 않고
그 record bbox와 겹치지 않는 candidate-authorized spatial OCR/vector exact observation 또는 reconstruction 초기
입력의 approved exact `user_edit`로 증명되어야 합니다. Derived default와 experimental notice는 예외입니다.
Node/flow record evidence ID 또는 normalized text+bbox 재사용, same-bbox ambiguity, metadata bbox와 node/flow
record bbox overlap, 필요한 data-record bbox의 missing/invalid geometry, bounded reference/text/token/spatial work
소진과 engine-emitted `user_edit` self-authorization은 review이며 semantic repair는 새 typed IR/scoped evidence로
같은 terminal gate를 다시 실행합니다.

Treemap은 누락·중복·잘못된 source ID를 reserved-safe preorder slot으로 격리합니다. Venn은 canonical unique
set ID를 요구한 뒤 set portable ID를 먼저 예약하고, intersection explicit ID가 정규화 충돌하면
deterministic `intersection_N[_suffix]` Scene slot을 배정합니다. Set/intersection의 source bbox는
typed IR/review provenance에 남지만 terminal generated Scene에는 복사하지 않고 zero bbox를 사용합니다.
Radar를 포함해 malformed evidence list는 해당 record에서만 원자적으로 비워 부분 provenance를 만들지
않습니다. 어느
경우에도 typed value나 그 record의 evidence reference가 독립 source OCR/vector numeric gate를 대신하지
않습니다. 현재 Marker response envelope는 계속 generic `TypedIRCandidate.ir: dict`이며 recursive Treemap
model도 provider에 diagram-type discriminated recursive schema를 직접 노출한다는 뜻은 아닙니다.

Treemap의 게시 gate는 이 extraction record를 단순 provenance 목록으로만 보지 않습니다. 모든 node의 bbox가
finite·positive·source image 내부인지, child가 parent에 완전히 포함되되 동일하지 않고 direct sibling interior가 겹치지 않되 edge-touch는 허용되는지 먼저
검사합니다. 각 node가 직접 인용한 candidate-authorized OCR/vector는 owner bbox 안에 있어야 하며 internal
owner의 text는 direct child 영역 밖에 있어야 합니다. Reading-order observation은 exact label과, 존재하는 경우
explicit fixed-decimal value를 함께 증명해야 합니다. Cross-owner evidence/observation reuse, same-bbox ambiguity,
한 owner 안의 duplicate evidence reference, missing bbox와 bounded association work 초과는 전체 결합을
unavailable로 두므로 source-wide OCR이나 typed
value만으로 자동 게시 권한을 만들 수 없습니다. Native runtime fallback과 semantic repair도 같은 record
결합을 다시 계산하며 generated Scene의 bbox는 계속 zero입니다.

Treemap root의 `title`/`description`/`acc_title`/`acc_description`은 extraction field가 존재한다는 사실만으로
게시 권한을 얻지 않습니다. Accessibility enrichment 뒤에도 네 metadata field를 모두 제거한 deterministic
structural baseline과 실제 resolved output을 비교해 non-derived text만 찾습니다. Native는 visible title과 accessibility title/description,
Flowchart는 실제 accessibility title/description만 평가하며 override에 가려진 legacy field, derived default와
pipeline-added experimental notice suffix는 면제합니다. Notice-only explicit description override는 structural
description을 지우므로 fail closed입니다. 같은 visible/accessibility title text는 한 title proof로 합치되 title과
description 역할은 분리합니다.

각 effective metadata owner는 node record와 분리된 candidate-authorized spatial OCR/vector exact observation
또는 reconstruction 초기 approved exact `user_edit`를 요구합니다. Node-owned/overlapping/ambiguous/reused
observation, engine-created edit와 shared budget 소진은 review입니다. 선택한 OCR/vector proof의 numeric token은
Treemap data reference에서 occurrence 단위로 제외해 독립적으로 관측된 metadata 숫자가 value hallucination으로
오인되지 않게 하며 fallback과 repair에서도 같은 provenance 경계를 유지합니다.

이 owner 계산보다 먼저 네 raw explicit metadata field를 검사합니다. Provider typed candidate의 raw snapshot은
pipeline enrichment 전에, public typed/runtime fallback과 typed direct `serialize_treemap()` input은 각 public
entry point에서 검증합니다. `None`/absent와 exact-empty omitted 호환 외에는 exact built-in `str`, raw/normalized
`MAX_TEXT_CHARS`, normalized non-empty, valid UTF-8와 raw `Cc`/`Cf`/`Zl`/`Zp` 부재를 요구합니다. 이 순서로
newline/tab laundering, overlong whitespace, zero-width format, string subclass, container/number와 lone surrogate를
accessibility derivation 및 Mermaid runtime보다 먼저 격리합니다. Semantic repair도 같은 검사를 재실행하고
exact-empty field를 제거한 canonical snapshot을 직렬화·평가·저장에 공통 사용합니다. Raw Direct Mermaid는
typed metadata field가 없으므로 security·parse·render와 typed-plan 부재 시 review-only gate로 제한합니다.

Venn native 선택은 extraction model보다 좁습니다. 모든 area가 관측된 positive normal binary64-safe
fixed-decimal이고, 최대 set/최소 positive area 비가 `200:1` 이하이며, exact-containment가 없고, 3개 이상
set의 union마다 모든 pairwise intersection이 explicit해야 합니다. Zero·unsafe·누락 value 또는 누락 pair는
exact-value Flowchart로 낮추되 값을 합성하지 않습니다. 관측 containment를 초과한 값은 invalid IR로
거부합니다. Portable membership은 500 edge까지만 허용하고 native area notation에는 그 Flowchart 전용
상한을 적용하지 않으며, 두 terminal source는 50,000자·5,000줄 예산을 공유합니다.

Typed value와 record의 evidence ID가 있다는 사실만으로 자동 게시 권한을 만들 수는 없습니다. 모든 planned
set/intersection의 finite positive in-image source bbox는 candidate-authorized cited contour와 exact 일치해야
하고, 그 안의 별도 cited OCR/vector text로
실제 label과 explicit fixed-decimal value를 full-record 단위로 증명해야 합니다. Label 또는 value가 optional이면
존재하는 field만 요구하고 합성하지 않지만 둘 다 없는 intersection은 textual owner proof가 없어 review입니다.
Evidence ID와 normalized text+bbox observation은 record 간 injective하고 same-bbox contradiction, missing
authority/evidence, invalid geometry와 20,000 reference·50,000 text·1,000,000 character·100,000 OCR token·
100,000 shared spatial work 초과를 fail closed로 처리합니다. Set/intersection source bbox끼리의 overlap은 Venn
의미이므로 허용하지만 intersection은 declared member set 모두에 inclusively contained되고 undeclared set에는
완전히 contained되지 않아야 합니다. Higher-order intersection은 모든 explicit strict-subset intersection 안에
있어야 하며 equal containment는 허용합니다. 100,000 shared spatial work에는 모든 set scan,
intersection-pair scan, contour 비교와 text containment가 포함됩니다. 이 membership geometry와 cited
observation의 owner-local containment, local record binding, 전역 numeric occurrence exactness를 native,
same-slot Flowchart와 repair에서 함께 재평가하고 runtime fallback repair는 같은 terminal로 canonical
재직렬화합니다. Direct/untyped Venn은 review-only입니다.

Venn metadata owner도 terminal-effective output에서 계산합니다. Native는 explicit canvas title만 요구하고
unsupported accessibility/description field는 면제합니다. Flowchart fallback은 실제 resolved accessibility
title/description 중 structure-only baseline과 다른 non-derived 역할만 요구하며 effective `acc_*`가 shadow한
legacy field는 세지 않습니다. Deterministic default와 pipeline-added experimental suffix는 면제하지만
experimental notice-only explicit description override는 fail closed입니다. `strict`에서 명시한 같은 문구는
pipeline suffix가 아니므로 일반 source text처럼 증명합니다. 같은 fallback title/description text도 역할이 다르므로
별도 owner입니다.

각 owner는 모든 Venn data bbox 밖의 candidate-authorized spatial OCR/vector exact observation 또는 초기 입력의
approved exact `user_edit`를 요구합니다. Data contour/text와 metadata 사이 ID·normalized text+bbox 재사용,
same-bbox ambiguity, area overlap, engine-created edit와 combined 20,000 reference·50,000 text·1,000,000
character·100,000 token·100,000 spatial work 소진은 review입니다. 선택한 OCR/vector proof의 numeric
occurrence만 data reference에서 제거하고 `user_edit` 숫자는 제거하지 않으며 native/intrinsic/runtime fallback과
repair에서 같은 scoped evidence로 재평가합니다. 동일 exact observation의 OCR/vector와 승인 edit가 함께 있으면
source channel을 우선해 evidence ID 이름이 numeric score를 바꾸지 않게 합니다.

이 owner 계산에 앞서 네 raw explicit metadata field를 검증합니다. Provider typed candidate의 raw snapshot은
pipeline enrichment 전에, public typed/runtime fallback과 direct Venn serializer input은 각 public entry point에서
검사합니다. Non-`None`은 exact built-in `str`이어야 하고 raw length를 먼저 제한하며 raw `Cc`/`Cf`/`Zl`/`Zp`를
허용하지 않습니다. Exact `""`은 omitted compatibility로 허용하지만 다른 문자열은 whitespace normalization
후에도 non-empty·bounded·valid UTF-8이어야 합니다. 이 순서로 newline/tab laundering, overlong whitespace,
zero-width format, string subclass, container/number와 lone surrogate를 accessibility derivation 및 Mermaid
runtime보다 먼저 격리합니다. Semantic repair의 typed IR도 같은 검사를 재실행하고 exact-empty field를 제거한
canonical snapshot을 직렬화·평가·저장에 공통 사용합니다.

여기서 direct Venn serializer는 typed `serialize_venn()` API를 가리킵니다. Raw Direct Mermaid 후보는 typed
metadata field를 갖지 않으므로 이 검사의 대상이 아니며 security·parse·render와 typed-plan 부재 시
review-only gate로 제한합니다.

### Journey·Kanban·GitGraph 계획 다이어그램 계약

세 계획 유형도 canonical record를 provider prompt에 공개하고 같은 strict nested model로 응답을
후검증합니다.

| Type | 필수 root | 선택 root | Prompt record |
| --- | --- | --- | --- |
| Journey | `sections: list` | 없음 | section의 `title`과 nested task; task의 `id`·`label`·strict integer `score`·`actors`; 각 record의 bbox/evidence |
| Kanban | `columns: list`, `cards: list` | 없음 | column의 `id`·`label`; card의 `id`·`label`·`column_id`; 각 record의 bbox/evidence |
| GitGraph | `initial_branch: string`, `operations: list` | 없음 | exact `initial_branch: main`; `direction: LR\|TB\|BT`; commit/branch/merge operation의 ID·branch/reference·tag·commit type·order와 bbox/evidence |

Journey section `label`과 task `text`, Kanban column `title`과 card `text`, GitGraph branch의 legacy `id` 및
commit `style`은 기존 serializer 호환 field로 strict 형을 검사하고 원본 IR에 보존하지만 canonical prompt에는
광고하지 않습니다. GitGraph의 operation type, direction, `NORMAL|REVERSE|HIGHLIGHT` commit type은
대소문자를 구분하지 않고 닫힌 집합으로 검증하되 원본 casing을 다시 쓰지 않습니다. `order`와 Journey
`score`는 boolean을 허용하지 않는 strict integer이고 bbox는 네 finite number, evidence reference는 string
list여야 합니다.

Nested model은 record/container와 알려진 scalar 형만 확정합니다. Journey의 non-empty section/task,
1~5 score와 고유 actor, Kanban의 non-empty ID·정규화 충돌·`column_id` reference, GitGraph의 정확한 `main`
초기 branch·순서가 있는 branch-head replay·commit/merge ID 고유성·merge 가능성은 공용 planning planner와
serializer가 계속 fail closed로 판정합니다. 세 유형의 2,000-record cap은 구조 탐색의 절대 상한이고, 모든
native/fallback 결과는 생성 뒤 validator와 같은 50,000자·5,000줄 source budget을 별도로 확인합니다.
긴 label이나 많은 actor 때문에 source budget이 먼저 소진되는 것은 정상적인 fail-closed 결과입니다.

Journey는 Timeline fallback의 section/task Scene을 만들고 score/actor를 실제 event text와 OCR projection에
보존합니다. Score는 typed IR에 있다는 이유만으로 신뢰하지 않고 독립 source OCR/vector 숫자와 일치해야
자동 게시할 수 있습니다. Kanban은 공용 column/card plan의 정규화된 emitted ID와 containment를 native,
Flowchart runtime fallback 및 generated Scene에 동일하게 사용합니다. GitGraph도 공용 branch-head plan으로
commit/merge node, parent relation, branch membership과 provenance를 만들며 native runtime 거부 시 같은
candidate slot에서 Flowchart로 재검증합니다. GitGraph label의 quote/backslash와 일반 문장부호는 pinned
Mermaid 11.16의 실제 SVG text를 기준으로 보존하고, renderer가 원문 angle bracket을 보존하지 못하는 경우만
`‹`/`›` compatibility glyph와 warning을 사용합니다.

Journey Timeline item은 colon을 문법 delimiter로 사용하고 entity-like spelling을 잘라낼 수 있으므로
section/task/actor의 `:`와 entity prefix를 각각 `∶`, `＆`/`＃` compatibility glyph로 표시합니다. Kanban
native markdown label의 quote/backtick은 `″`/`ˋ`, 두 planning Flowchart fallback의 quote/backslash는
`″`/`∖`로 표시합니다. 치환은 warning에 기록되고 원문은 typed IR/provenance sidecar에서 바뀌지 않습니다.

Canonical field와 compatibility alias가 동시에 있으면 정규화한 의미가 같아야 하며 충돌하면 응답을
거부합니다. GitGraph `initial_branch`는 root/prompt에서 필수이고 exact `main`을 요청합니다. Commit ID는
source 문자열뿐 아니라 grammar-specific encoding 뒤 표시 namespace까지 고유해야 하며, Kanban emitted ID는
strict Flowchart 예약어와 겹치지 않는 `kanban_` namespace를 native/Scene/fallback에서 공유합니다.
GitGraph generic operation record는 prompt 편의를 위해 모든 known field를 열거하지만, serializer는
commit/branch/merge별 허용 field 집합을 닫아 irrelevant known field가 자동 결과에서 조용히 사라지지 않게
합니다.

### Packet·Ishikawa·TreeView 특수 다이어그램 계약

세 특수 유형은 canonical record만 provider prompt에 공개하고 같은 strict nested model로
응답을 후검증합니다.

| Type | 필수 root | Prompt record |
| --- | --- | --- |
| Packet | `fields: list` | field의 `id`·`start`·`end`·`label`과 bbox/evidence |
| Ishikawa | `effect: object`, `categories: list` | child가 없는 effect와 재귀 category/cause `children` |
| TreeView | `root: object` | 재귀 root/children hierarchy |

Packet `name`과 hierarchy node `name`은 `label` compatibility alias로 string 형을 검사하고
원본 IR에 보존하지만 canonical prompt에는 광고하지 않습니다. `label`과 `name`이 둘 다
있으면 whitespace 정규화 후 같아야 하며, 다른 값은 증거 우선순위로 하나를 버리지 않고
fail closed합니다. Ishikawa `effect`는 leaf contract이므로 `children`을 넣어 category를
덮어쓰는 입력도 거부합니다.

Nested contract는 `start`/`end`를 boolean이 아닌 strict integer로, `children`을 object list로,
`bbox`를 네 finite number로, `evidence_ids`를 string list로 확정합니다. 빈 목록,
bit range 역전·overlap·gap, ID 형식·충돌, 순환·같은 dict object 재사용, TreeView
자식 누락, depth/node/source budget은 serializer-owned planner가 추가로 판정합니다.

Native serializer, portable Flowchart fallback과 generated Scene이 공유 planner의 같은 source
record·label·identity·parent를 소비합니다. Native 문법은 명시적 node ID 대신 검증된
label/range/depth를 사용하고, ID를 표현하는 fallback과 Scene은 각각 `packet_field_`,
`ishikawa_node_`, `treeview_node_` 예약어 안전 namespace의 emitted ID를 사용합니다.
따라서 missing ID를 각자 다른 순서로 생성하거나 충돌 node를 조용히 제거하지 않습니다.
Packet Scene은 field를 `LR` 순서의 독립 element로 표시하고 입력에
없는 relation을 만들지 않습니다. Ishikawa/TreeView Scene은 공유 parent 계획으로
containment relation을 만들고 원 record의 bbox/evidence를 유지합니다. Planner가 거부하면
Scene adapter도 부분 attribution을 만들지 않고 `unavailable`로 보냅니다.

### Wardley·Cynefin experimental native/fallback 계약

Wardley는 `components: list`를 필수, `links: list`를 선택 root로 사용합니다. Component
prompt는 `id`·`label`·`x`·`y`·`anchor`·bbox/evidence, link는 `source`·`target`·`label`·
bbox/evidence만 공개합니다. `x`/`y`는 boolean이 아닌 finite JSON integer/float,
`anchor`는 strict boolean입니다. 좌표 누락·`[0,1]` 범위, safe ID, display label 충돌,
endpoint·self/duplicate link와 component/link각 500개는 공유 Wardley plan이 추가로
fail closed 판정합니다. Serializer는 생성 source에 50,000자·5,000줄 preflight를 별도로
적용합니다. `name`·`nodes`·`relations`와 같은 비공식 alias는 canonical root를
대체하지 않습니다.
공유 plan은 native label token과 별도로 입력 순서 기반 `wardley_component_N` fallback ID,
`wardley_link_N` relation ID, Flowchart-visible compatibility label과 resolved fallback endpoint를
고정합니다. Native runtime 거부 시 이 값만 사용해 `flowchart LR`의 rectangle와 무방향 link를 만들고,
좌표·visibility/evolution 축·anchor 의미 손실을 warning으로 남깁니다. Source ID, 좌표, anchor와 원문은
typed IR/sidecar에 그대로 보존됩니다.

Cynefin은 `domains: list`를 필수, `transitions: list`를 선택 root로 사용합니다. Domain
`name`은 `complex|complicated|clear|chaotic|confusion`만 허용하며 extraction 경계는
serializer와 같이 whitespace 정규화·case-insensitive로 토큰을 검사하되 원문 casing을
바꾸지 않습니다. Canonical prompt는 item을 `{label,bbox,evidence_ids}` object로 요청해
각 표시 항목이 provenance를 가질 수 있게 합니다. 기존 scalar string item은 입력 호환을
위해 후검증에서는 허용하지만 prompt에 광고하지 않고, record evidence를 생성하지도
않습니다. Empty domain/item, unknown·duplicate domain, transition endpoint/self/duplicate과
item/transition각 500개는 serializer-owned plan이 판정하고, 생성 source budget은 serializer
preflight가 별도로 판정합니다.

Wardley IR의 `x`/`y`는 화면의 수평/수직 좌표입니다. Mermaid 11.16 Wardley 문법이
`[visibility, evolution]`을 요구하므로 serializer는 `[y, x]`를 방출하고 generated Scene은
`(x, 1-y)`를 사용합니다. `->` link는 해당 runtime에서 화살촉 없는 선이므로 Scene에서도
무방향 relation입니다.

Cynefin 11.16 renderer는 입력과 무관하게 다섯 domain과 practice/response template text를
자동 출력합니다. Generated Scene/OCR은 이 고정 element를 무근거 runtime template로 명시하고,
`confusion` item이 네 개 이상이면 처음 세 개와 renderer가 표시하는 `+N more`만 투영합니다.
숨겨진 item 원문은 typed IR/sidecar에 남습니다. 고정 template에 source provenance를 붙일
계약이 없으므로 native Cynefin 후보는 점수와 무관하게 review를 요구합니다.

Native runtime rejection은 extraction 계약을 완화하지 않습니다. 같은 검증된 plan으로 `flowchart LR`를
같은 candidate slot에 한 번 만들며, 입력에 있는 domain만 subgraph로, 모든 explicit item은 축약 없는 node로,
explicit transition만 domain subgraph 사이의 directed edge로 방출합니다. 다섯 domain template,
practice/response/disorder text, `+N more`, membership connector를 fallback에 합성하지 않습니다. Fallback
Scene/OCR도 이 terminal visibility를 따릅니다. Domain은 같은 ID를 공유하는 conceptual element/group으로
표현하되 label은 OCR에서 한 번만 세고, domain/item/transition은 각 record-local evidence를 유지합니다.
Source 위치를 subgraph quadrant로 추측하지 않으므로 bbox는 모두 0, direction은 `LR`이며 layout/Cynefin 공간
의미 손실을 warning으로 공개합니다. 이 fallback은 provenance와 일반 hard/semantic gate를 통과하면 게시할
수 있지만 native review hold를 해제하지 않습니다.

두 nested model은 공통 strict bbox/evidence와 알려진 scalar/container만 확정하고 추가 metadata를
원본 IR에 보존합니다. 검증 model은 입력 dict를 대체하지 않으므로 direct serializer의
label fallback, key-presence, order가 변하지 않습니다.

알려진 scalar field에는 object/list를 넣을 수 없고, record와 child container의 종류도 고정합니다. `bbox`는
정확히 네 개의 finite number, `evidence_ids`와 membership은 string list여야 합니다. `extra="allow"`를
사용하므로 style, geometry, plugin 또는 향후 Mermaid field 같은 미등록 metadata는 삭제하지 않습니다.
검증 결과 model은 원본 IR을 대체하지 않습니다. 기존 dict를 그대로 serializer, repair, canonical hash,
sidecar에 전달하므로 coercion이나 field stripping이 일어나지 않습니다.

Prompt의 record 목록은 serializer-visible 구조와 provenance/evaluation에서 canonical하게 소비하는 field만
광고합니다. Nested model은 보존할 style/compatibility metadata의 알려진 type도 추가로 검사할 수 있으므로
Pydantic field 집합과 prompt 문자열을 기계적으로 동일하게 만들지는 않습니다. 예를 들어 Architecture
`name`과 relation `label`, Sequence participant `text`, Flow edge의 raw arrow hint처럼 compatibility
metadata의 type도 검사합니다. Architecture `name`은
`label`의 serializer-visible alias지만 relation `label`, Sequence participant `text`, raw arrow hint는 원
IR/sidecar에 남아도 node/relation label 또는 구조 방향으로 평가하지 않습니다. 접근성 description에 보존된
metadata도 OCR 구조 점수에서는 제외합니다.

이 경계는 구조를 확인할 뿐 의미를 추측하지 않습니다. 빈 후보, 읽을 수 없는 label, 누락 label처럼 부분
복원 placeholder로 처리할 수 있는 입력은 허용합니다. non-empty 조건, ID uniqueness, endpoint/group reference,
Gantt 날짜와 Mermaid 표현 가능성은 serializer 및 evaluation gate가 계속 판정합니다. Architecture port는
nested contract에서 `L/R/T/B`만 허용합니다.
State kind, Class member visibility/kind/classifier 및 relation type, ER attribute key와 relationship
cardinality, Requirement·Block 및 C4의 위 token과 Deployment/Component port처럼 serializer가 닫힌
집합으로 해석하는 값도
같은 집합으로 제한합니다. Root list 이외의 record field는 partial reconstruction을 위해 선택이며,
필드 존재, non-empty, 표시 text, ID 중복, endpoint 참조 같은 의미 조건은 계속 serializer가
판정합니다.

Gantt record plan은 top-level `title`/`description`/`acc_title`/`acc_description`/`date_format`을 generic
accessibility enrichment 전에 다시 검사합니다. `None`/absent와 exact-empty omitted을 구분하고, 나머지는 exact
built-in string, raw/normalized `MAX_TEXT_CHARS`, valid UTF-8과 control/format/surrogate/line-separator 부재를
요구합니다. Initial candidate와 semantic repair도 같은 raw snapshot을 사용하므로 malformed metadata나
coercion hook이 derived `acc_*`로 바뀌지 않습니다. 별도 accessibility plan은 record plan의 semantic label로
description을 만들고 자신의 source/canvas encoding을 적용하며 task compatibility canvas를 복사하지 않습니다.
Explicit `description`/`acc_description`이 있으면 계속 authoritative하고, 둘 다 없을 때만 accepted repair의
현재 section/task 구조에서 description을 다시 파생합니다. Explicit title/acc-title도 우선합니다.

Plan은 task가 없는 section을 생략하고 renderable task가 하나도 없으면 거부합니다. Missing/exact-empty section
title과 task label은 각각 `Tasks`, section-local `Task N`으로 고정합니다. Status는 중복 없는
`active`/`crit`/`done`/`milestone`만 받고 `active`와 `done`의 동시 지정은 거부합니다. 각 task에는 `end` 또는
`duration` 중 정확히 하나가 있어야 합니다.
Task ID는 ASCII Gantt identifier 문법으로 표현 가능하고 전체 diagram에서 고유해야 합니다. Mermaid runtime
tag `active`/`done`/`crit`/`milestone`/`vert`, `__proto__`, 대소문자 무관 `iconify` substring은 ID로 거부합니다.
Start/end/duration/date format의 `,`/`#`/`;`도 거부합니다. `date_format`은 지원하는 numeric Day.js token subset을
strict parsing format으로 변환하고 calendar-valid start/end를 요구합니다. `h`/`hh` 12-hour token은 `A`/`a`
meridiem과 반드시 함께 있어야 합니다. Mermaid 11.16에서 end date가 zero-width가 되는 `Z`/`ZZ`와 `S`/`SS`는
거부하고 `SSS`만 허용합니다. Unit 불일치 seconds timestamp `X`도 거부하며 milliseconds `x`는 canonical
no-leading-zero decimal이고 ECMAScript Date 최대값 이하여야 합니다. Duration을 적용한 resolved `x` end도
prior-only `after` chain 전체에서 같은 최대값 이하여야 합니다. End는 start보다 뒤여야 하며 milestone
status만 equality를 허용합니다. Duration은 exact decimal+unit grammar 뒤 fractional `ms`/`d`/`w`/`M`/`y`를
Mermaid-rounded calendar 값으로 거부합니다. Fractional `h`/`m`/`s`는 정확한 양의 integer millisecond로
환산되고 bounded runtime magnitude 안에 있어야 하며 exact zero는 milestone에만 허용합니다. `after id...`는 기존 task ID 중
source order상 현재 task보다 먼저 나온 중복 없는 target만 허용해 forward/multi-target partial resolution과
cycle/depth 문제를 차단합니다. `after` start에는 duration만 허용하고 `until`은 relation attribution 전까지
fail-closed합니다.
검증된 `after` schedule도 현재 generated Scene에는 dependency `SceneRelation`을 만들지 않습니다.
`MMM`, timezone, 부분 날짜처럼 Mermaid 자체가 지원하더라도 이 numeric validation subset 밖인 format은 typed
자동 게시 coverage가 아니며 direct 후보 또는 review가 필요합니다.

Serializer를 통과한 뒤에도 실제 pinned-runtime scale에서 양수 task가 0폭으로 반올림될 수 있습니다. Final SVG
inspection은 runtime type `gantt`의 모든 `class~=task` rectangle에 finite positive width/height를 요구하며,
위반하면 typed/direct 구분 없이 render-invalid로 처리합니다. Journey나 Flowchart의 동명 custom class는 runtime
type gate 밖이므로 이 검사에 포함하지 않습니다.

Record plan의 title·section·task는 semantic/source/canvas text를 공유합니다. Task canvas의 모든 `:`/`%`는
`∶`/`％`, title/accessibility `<`는 `‹`로 표시하고 visible substitution이 실제로 있을 때만 warning을
추가합니다. Task directive-like `%`도 fullwidth가 되지만 title/section의 plain `%%`은 active opener가 아니면
literal로 남을 수 있습니다. Directive/comment/URL/callback/icon/config/Gantt control word/numeric entity/
task-leading ISO date 등 grammar/scanner-active token에는 visually inert zero-width separator를 넣습니다.
Normalized canvas와 Scene/OCR에서는 이를 제거하지만 raw SVG DOM text/title/description에는 남을 수 있습니다.
Generated Scene과 semantic OCR은 empty-section 제거 뒤의 title/section/task record canvas만 사용하고 hidden task
`text`, 내부 ID, schedule/accessibility metadata를 content label로 승격하지 않습니다. 별도 accessibility canvas는
SVG `<title>`/`<desc>` metadata projection으로 검사합니다.

State serializer의 후속 plan은 일반 node와 명시 transition label을 exact built-in string으로 다시
검사합니다. Exact-empty node label은 ID fallback, exact-empty transition label은 omitted 의미를 유지하지만,
그 밖의 text는 raw/normalized 길이·UTF-8·control/format/surrogate·normalized-empty gate를 통과해야 합니다.
Unicode whitespace는 한 칸으로 정규화하고 일반 node quote는 `″`, node/transition의 시작·끝·연속 run·
CommonMark escape backslash만 `∖`로 바꿉니다. Bounded linear scanner가 실제 active Markdown delimiter와
entity-like literal을 renderer-safe visible Unicode canvas glyph로 고정하고 일반 punctuation·안전한 중간
backslash는 유지합니다. Bare email/`www` autolink는 source-only separator로 비활성화해 visible text를
보존합니다. Resolved accessibility text의 quote/backslash/Markdown/named entity는
원문 glyph를 유지하되 numeric entity와 `<`는 `＆＃…`/`‹`로 고정합니다. Hidden pseudo-state label은 접근성
derivation에서 제외하고 ID만 사용합니다. Prompt/typed IR은 semantic 원문을 보존하고 generated Scene·
semantic OCR은 node/transition plan의 terminal-visible text를 사용하며, State grammar/scanner-active token의
source-only separator는 projection에서 제외합니다. Accessibility text는 별도의 SVG `<title>`/`<desc>`
metadata projection을 사용합니다.

State raw `title`/`description`/`acc_title`/`acc_description`은 nested label plan과 함께 generic accessibility
enrichment보다 먼저 검증됩니다. Absent/`None`과 exact-empty omitted 의미만 허용하며, 나머지는 exact
built-in string, raw/normalized bound, non-empty normalized text, UTF-8 및 Unicode category gate를 통과해야
합니다. Public/direct serialization과 initial/repair가 이 raw gate와 canonical accessibility plan을 공유하고,
pipeline의 initial/accepted-repair typed IR에는 validated raw snapshot을 저장합니다. 따라서 사용자 coercion
hook이나 malformed `acc_*`가 derived metadata로 세탁되지 않고, label repair 뒤 접근성 description도 현재
구조에서 다시 생성됩니다.

State record의 source ID가 Mermaid lexer/security 예약 토큰이거나 normalized ID에 strict `iconify` substring이
있으면 plan은 typed record와 evidence의 source identity를 바꾸지 않고 위험 token이 없는 collision-free
`mmx_state_id_…` emitted alias를 배정합니다. 전체 normalized source namespace를 먼저 예약해 prefix 충돌을
피하고, declaration·transition·generated Scene endpoint가 동일 mapping을 사용합니다.

`evidence_ids`도 prompt에서는 필수지만 legacy/partial candidate 호환을 위해 model에서는 선택 사항이며,
생략·`null`·빈 목록은 계속 허용합니다. 값이 있으면 strict string list이면서 record별 256개 이하여야
하고, 초과 후보는 serializer에 도달하기 전 nested post-validation에서 격리됩니다. 실제 자동 게시
여부는 provenance gate가 결정합니다.

평가 Scene은 serializer-visible fallback을 그대로 사용합니다. label이 없는 Flowchart/Generic Network,
Swimlane/BPMN, Mindmap node는 내부 ID가 아니라 `[unreadable]`로 기록합니다. Sequence participant는 source ID와
분리된 source-order `mmx_sequence_participant_N`을 declaration, message endpoint와 Scene에 공유하고 같은 logical
ID 중복을 거부합니다. Planner는 모든 message container/record, closed style, Scene relation 예산과 endpoint를
검사하며 하나라도 unresolved이면 전체 plan을 fail closed합니다. 통과한 모든 message를 원래 순서대로
serializer와 Scene에 전달하고 raw message ID와 무관한 `generated-relation-N` slot을 부여합니다. Scene/OCR은
plan의 canvas label과 style별 line/marker 의미, record-local evidence만 사용하므로 Mermaid에서 합쳐진 actor나
생략된 message를 별도 구조로 세거나 raw metadata에 OCR/provenance credit을 주지 않습니다.

Event Modeling·ZenUML adapter도 requested type은 각각 `eventmodeling`·`zenuml`로
유지하면서 실제 Flowchart·Sequence fallback plan의 identity와 topology를 Scene으로
옮깁니다. 작성자가 넣은 role/shape/style/direction/bidirectional·raw relation ID를
복사하지 않고, frame/participant element와 relation/message record 자체의 evidence만
연결합니다. `SceneGroup`에는 evidence field가 없으므로 lane record evidence를 group에
부여했다고 가장하지 않습니다.
Fallback이 source 좌표를 재현하지 않으므로 generated element/group bbox는 0으로 두어
layout 일치를 위조하지 않습니다. 두 Scene은 `LR`이고 relation/message는 end-arrow만 가지며,
화면에 보이는 compatibility label을 OCR projection과 한 번만 공유합니다.

Organization·Data Lineage adapter도 독립적으로 raw IR을 풀지 않고 serializer와
공유하는 frozen plan을 사용합니다. Organization node의 logical/fallback identity는
`treeview_node_*`,
Data Lineage node는 `data_lineage_dataset_*` 또는 `data_lineage_process_*`로
namespace를 나누고, plan이 확정한 relation은 각각 `organization_relation_N`·
`data_lineage_relation_N` Scene/provenance slot을 얻습니다. Lineage dataset/process의
실제 fallback shape는 cylinder/rectangle이고 relation은 `data_flow`입니다.
Organization child evidence는 child element·containment relation에, lineage relation evidence는
해당 `data_flow` relation에만 연결합니다.

두 fallback이 source 좌표와 group/style을 재현하지 않으므로 generated bbox는 0이고
group은 만들지 않습니다. raw bbox/role/shape/style/bidirectional과 relation ID는
Scene을 변경하지 않고 Organization의 raw direction도 무시합니다. OCR projection은
compatibility 치환 후 실제 화면에
보이는 node/relation label을 각 record당 한 번만 사용합니다. Data Lineage
direction은 `TB`, `BT`, `LR`, `RL`만 허용하고 기본은 `LR`입니다.

Railroad adapter도 raw AST를 다시 해석하지 않고 native serializer의 frozen plan을 사용합니다.
Rule은 `railroad_rule_*` logical ID와 실제 SVG text인 `native_name =` label을, expression은 preorder
`railroad_expression_N` ID를 사용합니다. Terminal/nonterminal label은 canonical compatibility text이고
special은 `? text ?`이며, ASCII angle·모든 ASCII `#`·entity-like `&` prefix·NFKC quote/backslash hazard는
위 glyph 계약을 그대로 반영합니다. Sequence/choice/optional/repetition operator에는 표시 text를
만들지 않습니다. Rule→definition과 parent operator→child만 marker 없는 containment relation으로
투영하고, nonterminal reference를 입력에 없는 native connector로 만들지 않습니다. Native layout이
source bbox를 재현하지 않으므로 Scene은 `LR`, zero geometry, 빈 group을 사용합니다. Rule evidence는
rule element에, expression evidence는 해당 element와 그 expression으로 들어오는 containment relation에만
연결하며 OCR은 화면에 실제 보이는 rule/leaf label을 한 번씩만 셉니다. Direct Scene 경계는
`evidence_ids`가 null/생략 또는 string list일 때만 받으며 다른 scalar·object·원소 형은 fail closed합니다.
Normalized safe rule name은 `native_name == source_name`이지만 scanner/preprocessor source-active name과
exact expression word, `railroad-beta`, case-folded lowercase `title*` prefix는 collision-safe
`rrmapped_N[_suffix]` native identifier로 mapping되고 warning을 남깁니다. Logical ID는 계속 source 기반
`railroad_rule_*`이며 원 source field는 typed IR에, normalized source name은 nonterminal 표시 text에
보존됩니다. Raw
ID/label/role/shape/style, source bbox와 다른 extra metadata는 sidecar IR에는 남지만 Scene/OCR 구조로
승격하지 않습니다.

현재 Marker `response_schema`의 외부 envelope는 여전히 `TypedIRCandidate.ir: dict`입니다. 따라서 이 단계는
모든 Phase 2 type, Phase 3 chart(Pie·XY·Quadrant·Sankey·Radar·Treemap·Venn),
Journey·Kanban·GitGraph와 Packet·Ishikawa·TreeView·Wardley·Cynefin·Event Modeling·ZenUML·
Organization·Data Lineage·Railroad의
prompt와 응답 후 검증을 중첩 구조까지
확장하지만 provider에 모든 Mermaid 유형을 하나의 discriminated JSON Schema로 직접 노출하거나 generic
envelope reserve를 늘리지는 않습니다. 모든 등록 유형의 root 아래에는 strict nested contract가 있지만,
envelope-level discriminated schema는 후속 작업이므로 `ARCH-001`은 여전히 부분 완화
상태입니다.

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

VLM/fixture 입력은 신뢰하지 않습니다. typed IR은 hook-free iterative walker가 exact built-in
`dict`/`list`/`tuple`/`str`/number/boolean/null만 읽어 detached snapshot으로 바꿉니다. 깊이 64, 전체 item
100,000개, 문자열 필드 50,000자뿐 아니라 모든 key와 반복 alias occurrence를 포함한 누적 UTF-8 text
1,000,000 bytes, compact escaped JSON 4,000,000 bytes를 동시에 제한합니다. 한 observation의 모든 typed
candidate JSON 합계도 8,000,000 bytes를 넘을 수 없습니다. Tuple은 list로 정규화하고 cycle, container
subclass, non-finite number와 JavaScript safe integer 범위 밖 숫자는 직렬화 전에 거부합니다.
Candidate envelope는 `diagram_type`, `ir`, `confidence` 세 공개 field만 허용하며 field count를 확인한 뒤에만
exact-string field name을 검사하고 bounded copy합니다. Validation error는 원본 input 표현을 포함하지
않습니다. Fusion도 모든 observation에서 선택한 unique candidate에 64개/8,000,000 bytes 전역 상한을
다시 적용하고 deterministic bounded prefix를 유지합니다.
알려진 semantic record의 evidence reference는 Scene model과 같은 공용 상수로 256개까지 허용됩니다.
정확한 경계값은 generated Scene과 게시 후보에 그대로 보존하고, 생성 뒤 257개 이상으로 변조된 IR은
canonical key, fusion, pipeline 및 sidecar 소비 경계의 재검증에서 후보 단위로 거부합니다.
Observation candidate, evidence, warning 수와 Scene IR element/relation/group, polygon/polyline, ID, bbox도
별도 상한과 finite-number 검사를 거칩니다. `NaN`/무한 좌표와 범위를 벗어난 confidence는 sidecar에
도달하기 전에 거부됩니다. JSON sidecar는 `allow_nan=false`로 직렬화합니다.

Semantic record의 `evidence_ids` 상한과 별도로, retained `VisualEvidence.source_block_ids`는 collection
전체에서 logical occurrence 20,000개와 Python 문자열 길이 8,000,000자를 공유합니다. 중복 reference도
각각 계산하며 `VisualEvidence`의 `id`·`kind`·`text`·`font_weight`·source-block ID 전체에 대한 기존
8,000,000-character cap도 독립적으로 적용합니다. Exact boundary는 통과하고 `+1`이면 initial/custom-engine
collection, reconstruction-global 신규 ID batch 또는 fusion input/output 전체를 원자적으로 격리합니다.
Snapshot은 exact public field와 nested exact list를 built-in access로 고정해 detached model을 만들며 live
`model_dump`나 subclass hook을 사용하지 않습니다. Final result와 publication/Markdown, sidecar/output
sink도 같은 snapshot을 재사용합니다. JSON/dict ingress는 record별 canonical validation과 누적 snapshot을
교대로 수행해 over-budget prefix 전체를 폐기합니다. Marker OCR producer와 Review provenance
read/replacement/structured-add 및 standalone Structured VLM prior-evidence ingress도 이 경계를 공유합니다.
Structured VLM은 prompt item selection보다 먼저 whole-input snapshot을 만들며 provider에는 detached
record만 전달합니다. 이 runtime 계약은 provider response schema, public config, sidecar manifest를
변경하지 않습니다. Evaluation prediction JSON ingress도 `VisualEvidence` model 생성 전에 같은
raw-record snapshot을 사용하되, `mmx-eval-prediction-0.1`의 100,000-record/64 MiB artifact 계약을
보존하기 위해 item/full-character limit만 evaluation 값으로 명시합니다. Source-block occurrence와
source-block character limit은 runtime과 동일합니다.

Canonical candidate key는 이 bounded snapshot의 SHA-256 digest를 사용하므로 multi-megabyte IR을 key로
보유하지 않습니다. set, bytes 같은 비결정적·비 JSON 값도 dedup이나 private mapping lookup에 들어가기
전에 거부합니다.
Flowchart/Generic Network record의 ID·label·endpoint 같은 알려진 scalar field에는 object/list를 넣을 수
없습니다. 또한 repair나 plugin이 모델 생성 뒤 mutable IR를 바꿀 수 있으므로 canonical key 계산과 fusion
입력 경계에서 현재 payload를 다시 snapshot 및 Pydantic/typed-contract validation합니다. Accessibility
enrichment와 semantic repair의 current/proposal IR도 같은 경계를 다시 통과합니다. 변조된 후보 하나는
fusion warning과 함께 제외되며 다른 후보나 문서를 실패시키지 않습니다.
이 재검증은 fusion 후보에만 한정되지 않습니다. Pipeline은 initial evidence와 각 engine 응답을 받은
직후 Scene IR, typed/direct candidate, evidence를 현재 payload로 각각 다시 모델 검증하고, invalid component만
`CandidateFailure`로 격리한 sanitized observation을 이후 original/fusion 경로 모두에서 사용합니다.
Sidecar는 selected/alternative candidate의 live IR을 안전한 snapshot으로 교체한 shallow copy를 만든 뒤에만
model dump, JSON encoding, 전체 result deep-copy를 수행합니다. 따라서 validation 뒤 mutation된 original
observation이 fusion fallback이나 serialization sink를 우회해 게시되지 않습니다.

## 평가 Scene adapter

Typed IR serializer가 만든 결과를 provenance 및 구조 점수에 사용할 때는 `candidate_scene.py`가 실제
방출 구조를 `DiagramSceneIR`로 바꿉니다. Flow/UML/architecture/chart 외에도 sequence/ZenUML,
mindmap/treemap/tree/organization, timeline/journey/Kanban, event modeling, Ishikawa, Wardley/Cynefin,
data lineage, Venn adapter가 있습니다. Organization과 Data Lineage adapter는 serializer와
공유하는 bounded plan에서 logical/fallback emitted identity와 실제 label·topology를
읽으므로 raw record를 다시
해석하지 않습니다. Adapter가 없는 유형은 구조를 추측하지 않고 metric을
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
