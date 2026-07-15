# 차트 serializer와 숫자 안전성

차트 typed IR은 OCR/VLM이 읽지 못한 값을 보간하지 않습니다. Structured VLM 경계의 숫자는 bool이나
숫자 문자열을 허용하지 않는 strict finite JSON `int`/`float`이며 잘못된 값은 candidate validation에서
거부됩니다. Pie·XY·Quadrant·Sankey·Radar·Treemap의 직접 serializer API는 `Decimal`도
받지만 provider 응답 계약에는 포함하지 않습니다. Venn 직접 API는 기존 `int`/`float`
계약을 유지합니다. 각 API는
NaN/Infinity, unknown endpoint, series 길이 불일치, 잘못된 축 범위를 `SerializationError`로 거부합니다.

| type | native 조건 | fallback |
| --- | --- | --- |
| Pie | 고유 label, non-negative slice, positive total | 없음; 값 누락은 실패 |
| XY | category/value 길이 일치 또는 explicit uniform numeric x grid, y bounds | 없음; 비균일 x는 왜곡 대신 실패 |
| Quadrant | 두 축 low/high label, 모든 point의 explicit `[0,1]` 좌표 | 없음 |
| Sankey | positive weighted DAG, 모든 node 참여, native-safe 고유 label | exact weight label을 가진 flowchart |
| Radar | 3개 이상 dimension, 동일 series 길이, 일관 bounds, 12개 이하 series, non-negative zero-or-normal binary64 domain과 finite positive renderer span | 최대 256 point의 edge 없는 exact-value tabular flowchart |
| Treemap | hierarchy leaf마다 explicit positive value, internal value 없음, binary64/표시 합계 재현 가능 | internal-node value·unsafe numeric·native runtime 실패 시 value-label hierarchy |
| Venn | 모든 area가 positive·normal binary64-safe이고 최대 set/최소 area 비가 `200:1` 이하이며 higher-order union의 모든 pair가 explicit | zero·unsafe·누락·exact-containment·가시성 위험·누락 pair는 숫자를 합성하지 않는 set/intersection graph |

## Core chart structured extraction

Pie·XY·Quadrant는 root-only JSON이 아니라 provider prompt와 응답 후 검증이 공유하는 strict nested
contract를 사용합니다.

| type | nested contract | serializer가 판정하는 의미 조건 |
| --- | --- | --- |
| Pie | `slices[]`의 `label`·`value`·bbox/evidence, strict `show_data` boolean | non-empty slice, 고유 label, non-negative value, positive total |
| XY | `x_axis`/`y_axis`, `series[]`의 `kind: line\|bar`·`values`·`points`, point `x`/`y`, 각 record의 bbox/evidence | category와 numeric x mode 배타성, min < max, exactly-one values/points, category 길이 또는 exact uniform numeric grid, 모든 y가 선언된 y축 범위 안인지 검사; Mermaid 11.16에 strict-safe series label 문법이 없어 `label`/`name`은 거부 |
| Quadrant | 축 `low`/`high`, `quadrants: string[4]\|{quadrant-1:string,quadrant-2:string,quadrant-3:string,quadrant-4:string}`, point `label`·`x`·`y`와 bbox/evidence | non-empty·고유 point label, 좌표 `[0,1]`; quadrant list는 정확히 4개이고 object는 canonical `quadrant-1`~`quadrant-4` 또는 compatibility key `1`~`4`의 부분 집합을 허용하되 같은 slot의 alias 충돌은 거부 |

세 계약의 root container는 필수지만 개별 record field는 partial extraction을 위해 선택입니다. Completeness와
Mermaid 표현 가능성은 serializer가 판정하며, 실패하면 후보 단위로 끝납니다. 현재 core chart에는 table,
prose 또는 Flowchart fallback이 없고 각각 native `pie`, `xychart-beta`, `quadrantChart`만 방출합니다.

각 record의 bbox/evidence는 strict 검증 후 typed IR/review sidecar에 보존됩니다. 아직 이 세 type용 generated
Scene adapter가 없으므로 Scene attribution이나 구조 점수에는 연결되지 않습니다. 공통 accessibility root와
미등록 extra metadata도 원본 dict에 보존되지만, 그 안의 숫자는 누락된 slice/axis/point 값을 채우는 chart
data evidence가 아닙니다.

## Extended chart structured extraction

Sankey·Radar·Treemap·Venn도 provider prompt와 응답 후 검증이 공유하는 strict nested contract를 사용합니다.

| type | nested contract | serializer가 판정하는 의미 조건과 fallback |
| --- | --- | --- |
| Sankey | `nodes[]`의 `id`·`label`, `flows[]`의 exact endpoint·`value`, bbox/evidence | non-empty·ID/endpoint, 모든 node 참여, label 안전성, positive DAG를 판정; native 조건을 벗어난 valid graph는 exact-weight Flowchart |
| Radar | `dimensions[]`의 `id`·`label`, `series[]`의 ordered `values`, finite `min`/`max`, strict `ticks`/`show_legend`, `circle|polygon` graticule, bbox/evidence | 3개 이상 dimension, ID·series 길이·bounds·option 의미, `ticks <= 100`, native 12-series와 fallback 256-point cap을 판정; binary64/span/radius 비호환 또는 valid negative domain은 edge 없는 exact-value Flowchart |
| Treemap | 재귀 `root` node의 `id`·`label`·`value`·`children`과 bbox/evidence | root/internal/leaf, positive value, cycle·object reuse·depth·size를 판정; internal value·binary64/표시 합계 손실·native runtime 실패는 value-label hierarchy Flowchart |
| Venn | `sets[]`와 `intersections[]`의 ID·membership·label·optional finite value, bbox/evidence | non-negative value, set/member·canonical intersection uniqueness와 size containment를 판정; native는 positive normal binary64-safe area, `200:1` visibility gate, higher-order union의 모든 explicit pair를 요구하고 나머지는 exact-value Flowchart |

Nested model은 JSON 구조와 known scalar/container의 형만 검사합니다. 개별 semantic field는 partial/legacy
후보 격리를 위해 선택이며 completeness와 native/fallback 결정은 serializer가 맡습니다. Sankey `links`,
Radar `axes`, Treemap/Venn `name`은 direct compatibility metadata로 검증·보존할 수 있지만 canonical prompt에는
광고하지 않습니다. Alias를 canonical root로 복사하거나 누락 collection을 채우지 않으므로 serializer의
key-presence 우선순위도 그대로입니다.

Sankey·Radar·Treemap·Venn의 valid evidence는 generated Scene attribution에 연결됩니다.
Treemap source bbox는 typed IR/review provenance에만 남고 generated Scene에는 복사하지 않습니다.
Radar source bbox도 terminal layout으로 가장하지 않습니다. Native는 renderer에서 계산한 normalized
axis/data-point 위치를 사용하고 fallback은 zero geometry를 사용합니다. Radar fallback은 모든 dimension label과
series value를 보존하지만 bounds, ticks, legend, graticule과 Radar geometry를 Mermaid code에 표현하지 않습니다.
Treemap은 unique·bounded source ID를 유지하고, 누락·중복·잘못된 ID는 collision-safe
`treemap_node_N[_suffix]` attribution slot으로 격리합니다. Venn은 set의 portable emitted ID를 먼저
예약하고, intersection의 explicit ID가 정규화 충돌하면 deterministic `intersection_N[_suffix]` slot을
배정합니다. 따라서 set/intersection ID 충돌 때문에 Scene node를 버리지 않습니다. 모든 numeric type의
독립 source evidence gate도 그대로 적용됩니다.

Sankey serializer와 Scene/OCR adapter는 한 번 검증한 terminal plan을 공유합니다. Native `sankey-beta`는
source node ID와 label을 유지하지만 Mermaid 11.16 canvas에는 각 node마다 label과
`max(sum(incoming), sum(outgoing))`만 보입니다. 합계는 runtime의 binary float 합산과
`Math.round(value * 100) / 100` 결과를 안전하게 재현할 수 있을 때만 native를 선택합니다. Native Scene의
flow는 `data_flow`이지만 개별 weight label과 arrow marker가 없고, runtime이 고정하는 방향에 맞춰 `LR`입니다.
Semantic OCR projection은 node label과 이 표시 합계만 세며, relation의 exact value는 typed IR과 provenance에
남깁니다. JavaScript number로 변환할 때 0·무한대가 되거나 shortest decimal 자체가 달라지는 exact value와
안전한 cent 단위 표시 범위를 벗어난 합계는 native로 보내지 않습니다.

Flowchart terminal은 같은 plan의 collision-safe emitted node ID를 쓰고 각 exact decimal weight를 directed
edge label로 표시합니다. Scene도 그 endpoint, label, end-arrow와 `TB`/`BT`/`LR`/`RL`로 정규화한 requested
direction을 그대로 사용합니다. Node/flow record의 bbox와 evidence만 attribution에 연결하고 raw `text`,
role, shape, flow label/style/bidirectional/arrow hint 같은 미방출 metadata는 Scene으로 승격하지 않습니다.
Native runtime이 parse/render gate에서 거부되면 새 후보를 만들지 않고 같은 candidate slot에서 이
Flowchart를 한 번 재직렬화하고 전체 security/parse/render/SVG/type gate를 다시 통과시킵니다.

Radar serializer·Scene·semantic OCR은 `plan_radar_records()`의 같은 bounded plan을 공유합니다. Plan은
dimension/series source record, terminal 전체에서 충돌하지 않는 emitted ID, exact fixed-decimal value,
terminal별 source/canvas label, point별 dimension+series evidence를 한 번 고정합니다. Radar grammar 예약어와
Flowchart group/cell ID까지 하나의 namespace에서 collision-safe suffix로 분리합니다. Malformed evidence list는
해당 record에서만 원자적으로 비우고, point provenance의 bounded 합집합을 만들 수 없으면 그 point evidence
전체를 비웁니다. Dimension은 최대 256개, 전체 point와 Scene element는 공용 Scene budget을 지키며 native와
fallback source 모두 50,000자·5,000줄 preflight를 통과합니다.

Native `radar-beta`는 value와 explicit bound가 zero 또는 normal binary64로 원문과 round-trip되고,
effective minimum과 maximum의 binary64 span이 positive finite이며, pinned 300px renderer radius 계산이
finite일 때만 선택합니다.
음수 domain, subnormal/overflow/precision loss, zero/non-finite span은 exact fallback으로 내립니다. Mermaid
theme가 색을 안정적으로 제공하는 12 series까지만 native로 허용하며 13번째부터 fallback합니다. Native
Scene은 perimeter의 axis와 curve data point를 `[0,1]` normalized 좌표로 놓고 각 series의 마지막 point를
첫 point로 잇는 marker/label 없는 `series_curve` association을 사용합니다. Series element bbox는 그 curve
point들의 normalized envelope라서 logical series를 원점에 놓아 layout score를 왜곡하지 않습니다. 방향은 `radial`이고 series label은
`showLegend=true`일 때만 Scene/OCR text입니다. Native OCR은 visible title·axis·legend만 세며 value와
`min`/`max`·`ticks`·`graticule`, `accTitle`/`accDescr`는 geometry/metadata이므로 제외합니다.

Native를 쓸 수 없거나 CandidateValidator가 native를 거부하면 같은 candidate slot에서 최대 256 point의
`flowchart TB`를 한 번 재검증합니다. 각 series는 subgraph, 각 point는 zero-geometry rectangle
`dimension: exact-value` cell이며 edge는 만들지 않습니다. Fallback Scene/OCR도 이 group/cell만 투영하고
native title과 bounds/ticks/legend/graticule을 canvas content로 가장하지 않습니다. 256-point fallback을 만들 수
없는 valid native candidate가 runtime에서 거부되면 partial code/Scene 대신 unavailable입니다. Strict scanner용
source separator와 angle/hash, fallback quote/backslash 등의 visible compatibility glyph을 분리하고, visible
치환은 native/fallback warning에 공개합니다. CandidateValidator의 SVG inspection은 Mermaid가 render 성공을
보고해도 geometry attribute에 `NaN`/`Infinity`가 있으면 render-invalid로 닫습니다.

Native generated-node provenance gate는 실제로 직접 귀속할 수 있는 axis와 series를 평가하고, series에서
파생된 data point는 분모에서 제외합니다. Point value는 별도의 numeric consistency가 검증합니다. 반대로
Flowchart fallback의 point cell은 실제 Mermaid node이므로 injective provenance 분모에 남습니다. 여러 cell이
같은 dimension/series evidence만 반복 주장하면 자동 게시 권한으로 세지 않고 review로 보냅니다.

Treemap serializer·Scene·semantic OCR도 `plan_treemap_records()`의 같은 DFS preorder plan을
공유합니다. Plan은 source record, logical Scene ID, Flowchart에 실제 방출할 `N1..Nn`,
parent/child relation, terminal별 label과 value text를 한 번 고정합니다. Original image와 source bbox는
typed IR/review provenance에 그대로 남기지만 generated terminal Scene은 생성 SVG 배치를 원본
위치로 대체하지 않고 모두 zero bbox를 씁니다. Valid evidence ID는 element, child evidence는 해당
containment relation에도 연결됩니다. `evidence_ids`가 exact string list·256개·ID/Unicode 경계를
하나라도 어기면
record 전체 evidence tuple만 비우고 직렬화·계층·다른 record provenance는 유지합니다.

Native `treemap-beta`는 internal node를 section, leaf를 값을 가진 cell로 렌더합니다. Internal
표시 합계는 Mermaid 11.16의 d3-hierarchy처럼 child를 역순으로 binary64 `+=`한 값이고,
각 section/leaf의 canvas value는 d3 `format(",")`의 comma-grouped 12-digit 표시와 같아야 합니다.
Decimal token이 JavaScript number로 underflow/overflow하거나, safe integer를 넘거나, shortest decimal을
읽었을 때 원본과 다르거나, 이 표시 합계를 안전하게 재현할 수 없으면 native를
시도하지 않습니다. Native Scene은 section/leaf text와 logical containment만 가지며 실제
SVG에 connector path나 arrow marker가 없고, nested-area layout을 flow 방향으로 해석하지 않아
`reading_direction=unknown`입니다. Zero Scene geometry 때문에 native/fallback 모두 generated
layout similarity는 원본 bbox를 복사해 자기 자신을 증명하지 못합니다.

Explicit native `title` directive는 canvas에 보이는 title을 만듭니다. 별도의 `accTitle`/
`accDescr`는 SVG `<title>`/`<desc>` accessibility metadata이며 그 자체를 content OCR로 세지
않습니다. Native semantic projection은 visible title이 있으면 그 text, 각 section/leaf label,
d3 표시 합계를 사용합니다. 다만 실제 배치에서 너무 작은 native cell의 text는 renderer가
`display:none`으로 숨길 수 있으므로, 모든 leaf label이 눈에 보인다고 보장하지 않습니다.

Internal node에 explicit value가 있거나 native numeric contract를 만족하지 못하면 Flowchart
terminal은 DFS preorder `N1..Nn`, `flowchart TB`, rectangle node, parent→child end-arrow를 사용합니다.
각 node에 실제로 제공된 value만 exact fixed-decimal ` (value: x)` suffix로 표시하며 파생한
internal total을 만들지 않습니다. Raw direction과 native-only visible title은 fallback canvas에
복사하지 않고 title/description은 accessibility metadata로만 남습니다. Native runtime 거부도
새 후보 없이 같은 slot의 이 fallback을 한 번 재검증합니다. Flowchart는 500 relation까지만
가능하며, 이 제한을 넘는 valid native Treemap은 native로 남을 수 있지만 runtime fallback이
필요해지면 unavailable입니다.

Treemap text는 semantic 원문을 typed IR에 보존하고 terminal이 실제로 표시하는 호환 text를
Scene/OCR에 사용합니다. Scanner-active token은 emitted source에만 zero-width separator로
나누어 canvas에서는 제거하고, quote는 `″`로 표시합니다. Flowchart label은 추가로 ASCII
angle bracket/backslash/hash를 `＜`/`＞`/`∖`/`＃`로, native title은 angle bracket을 `＜`/`＞`로
표시합니다. URL/directive-like token과 entity-like `&...;`는 emitted source에서만 비활성화하고,
native의 `#`도 source-only separator로 나눕니다. Native grammar가 그대로 보존하는 literal은 임의
glyph로 바꾸지 않습니다. CR/LF와 NBSP를 포함한 Unicode whitespace run은 실제 canvas와 같이 한
ASCII space로 고정합니다. 눈에 보이는 호환 glyph을 사용한 node/title 또는 resolved
`accTitle`/`accDescr`가 있으면 native 결과는 candidate warning을, Flowchart는 fallback
reason/warning을 남깁니다. 두 terminal source는 runtime 전에 50,000자·5,000줄 예산을 통과해야 합니다.

Venn serializer·Scene·semantic OCR은 `plan_venn_records()`의 같은 bounded plan을 사용합니다. Plan은
set의 source/portable ID, collision-safe intersection Scene ID, canonical membership 순서, exact
fixed-decimal value token, terminal별 label과 record-local evidence를 한 번 고정합니다. 지수 표기는
방출하지 않습니다. Set/intersection object 재사용, unknown/repeated member, duplicate intersection,
containment 위반, area·membership resource 초과는 serialization 전에 거부합니다. Malformed evidence list는
해당 record의 전체 evidence tuple만 비우며 code·topology·다른 record provenance는 유지합니다.

Native `venn-beta`는 모든 set/intersection value가 positive normal binary64로 원문과 round-trip되고,
Python `int` 입력이 JavaScript safe 범위를 넘지 않으며, 최대 set과 최소 positive area의 비가 `200:1` 이하일 때만
선택합니다. Intersection이 member set 또는 더 작은 explicit intersection과 정확히 같은 크기인
exact-containment도 renderer budget 위험 때문에 fallback합니다. 3개 이상 set의 union은 그 union 안의
모든 pairwise intersection이 입력에 명시돼야 하며 누락 pair의 크기를 암묵적으로 합성하지 않습니다.
Zero·subnormal·overflow·precision-loss·누락 value도 모두 exact Flowchart를 선택합니다. 관측 containment를
초과하는 intersection은 fallback으로 감추지 않고 잘못된 IR로 거부합니다.

Native Scene은 set을 circle, intersection을 shape 없는 logical area로 두고, membership을 label/marker가 없는
`logical_membership` containment로 투영하며 `reading_direction=unknown`을 사용합니다. Native canvas OCR은
visible `title`과 실제 set/intersection label만 세고 area value는 화면 text가 아니라 geometry input이므로
세지 않습니다. Flowchart terminal은 set circle과 intersection round node에 관측 value를 exact
` (value: x)` suffix로 표시하고, 각 set→intersection relation을 `intersects` label·end-arrow로 방출하며
`LR`을 사용합니다. Native-only title은 fallback canvas에 복사하지 않고 resolved accessibility text는 SVG
metadata로만 남습니다. 두 terminal의 generated element bbox는 모두 zero이고, set/intersection evidence는
각 element에, intersection evidence는 모든 membership relation에도 연결됩니다.

Native runtime rejection은 새 후보를 만들지 않고 같은 candidate slot에서 Flowchart를 한 번 재검증합니다.
Flowchart terminal은 pinned worker의 500-edge limit을 넘으면 code와 Scene을 모두 unavailable로 닫지만,
501개 이상의 membership을 가진 valid native Venn까지 금지하지는 않습니다. 500은 성능 보장이 아니라
상한이므로 near-limit fallback도 runtime timeout과 일반 render budget을 계속 적용합니다. Native와 fallback
source는 각각 50,000자·5,000줄 preflight를 통과해야 합니다. Scanner-safe source separator와 visible
quote/angle/backslash/hash/semicolon compatibility glyph은 terminal Scene/OCR과 공유하고 warning에 공개합니다.

공용 Sankey plan은 Scene relation 상한을 serializer 이전에 적용하고 relation ID를 bounded unique slot으로
할당합니다. 비문자·초과 길이 ID는 deterministic `sankey_flow_N` slot을 사용하고 중복 ID는 suffix로
분리합니다. Record의 `evidence_ids`가 string list가 아니거나 개수·ID·Unicode 경계를 위반하면 Mermaid와
구조 자체는 유지하되 그 record의 provenance만 빈 목록으로 격리합니다. Native는 Scene relation 상한까지
평가할 수 있지만, Flowchart terminal은 pinned worker의 500-edge limit을 넘기 전에 serializer와 Scene이 함께
unavailable로 닫습니다.

모든 native/fallback 대표 fixture는 Mermaid 11.16 strict `CandidateValidator`의 parse/render/SVG 검사를
통과합니다. Sankey grammar는 title/accTitle/accDescr를 표현하지 못하므로 해당 text를 typed IR과 warning에
남깁니다. Flowchart fallback은 접근성 metadata를 SVG에 보존하지만 canvas OCR label로 세지 않습니다.
Native Venn은 visible `title`만 지원하고 `accTitle`/`accDescr`를 parse하지 못하므로 resolved accessibility
text를 typed IR과 limitation warning에 남깁니다. Treemap/Venn의 experimental native grammar도 runtime
type을 sidecar에 기록합니다.

pipeline의 numeric consistency는 source와 generated 숫자 occurrence multiset F1입니다. Bounded evidence
안에서는 동일 normalized text+bbox를 한 관측으로 합치고, OCR context와 evidence 채널의 token Counter는
token별 최대 occurrence로 병합합니다. 따라서 위치가 다른 반복값은 보존하면서 채널 간 중복 보고는 다시
세지 않습니다. Source에 없는 숫자나 횟수 불일치는 precision/recall을 낮춥니다. Typed chart value나 그
record의 `evidence_ids`만으로 source 숫자 관측을 대체할 수 없습니다. Typed/Scene 후보는 semantic type으로
gate를 유지하고, direct 후보만 parse/render validation으로 확정한 emitted/runtime type을 사용합니다.
결과 type이 Gantt/Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn이면 source OCR/vector numeric evidence가
하나도 없을 때 syntax/render가 성공해도 `U` 등급 review 대상으로 남고, 일치도가 threshold보다 낮아도 자동
게시되지 않습니다.

Packet은 이 전역 occurrence multiset의 예외입니다. Native Packet, Flowchart runtime fallback, semantic
repair proposal 모두 candidate-authorized field-local association을 다시 계산합니다. Field가 직접 인용한
OCR/vector evidence의 bbox 전체가 양의 면적의 field bbox 안에 있고 둘 다 실제 image bounds 안에 있을 때만
label과 bit range를 결합하며 source-wide `ocr_texts`는 binding에 사용할 수 없습니다. Exact label+range는
`1.0`, 결합된 잘못된 range나 추가 숫자는 `0.0`과 review입니다. Single-bit `start == end`는 endpoint 숫자
한 번을 요구합니다. 동일 normalized text+bbox의 OCR/vector 중복은 한 번만 세고 공간적으로 다른 반복은
유지합니다. 겹치는 field, broad/shared/같은 위치의 모호한 관측, 누락되거나 잘못된 authority·bbox·image
bounds, association budget 소진은 unavailable/review이며 전역 multiset이나 게시 threshold로 우회하지
않습니다. 다른 numeric type의 multiset 계산은 그대로입니다.

Generated numeric projection은 Mermaid `%%` comment를 제외하고, detected grammar가 지원할 때만 native
`title ...`, colon `title: ...`, `accTitle: ...`, 한 줄 `accDescr: ...`, block `accDescr { ... }`를
metadata로 제외합니다. Sankey에서는 이 문자열로 시작하는 CSV label도 실제 data이므로 row와 weight 숫자를
그대로 셉니다. Quadrant의 `quadrant-1`~`quadrant-4` directive index는 문법 토큰이므로 제외하지만 directive
label이나 point 좌표 안의 실제 숫자는 유지합니다. 지원되는 metadata는 source numeric evidence로도 사용되지
않습니다. Venn은 size가 없는 portable fallback을 만들 수 있어도 numeric mandatory type이므로 자동 게이트를
우회하지 않습니다.
