# 차트 serializer와 숫자 안전성

차트 typed IR은 OCR/VLM이 읽지 못한 값을 보간하지 않습니다. Structured VLM 경계의 숫자는 bool이나
숫자 문자열을 허용하지 않는 strict finite JSON `int`/`float`이며 잘못된 값은 candidate validation에서
거부됩니다. Pie·XY·Quadrant·Sankey·Radar·Treemap의 직접 serializer API는 `Decimal`도
받지만 provider 응답 계약에는 포함하지 않습니다. Venn 직접 API는 기존 `int`/`float`
계약을 유지합니다. 각 API는
NaN/Infinity, unknown endpoint, series 길이 불일치, 잘못된 축 범위를 `SerializationError`로 거부합니다.

| type | native 조건 | fallback |
| --- | --- | --- |
| Pie | 고유 label, non-negative slice, positive total, 12개 이하 slice, zero-or-normal binary64와 1% visibility/`showData` 표시 동등성 | 최대 256개 slice의 edge 없는 exact-value Flowchart |
| XY | category/value 길이 일치, bounded exact numeric grid, visible line/bar, zero-or-normal binary64 axis/value, 최대 10 series | 최대 256 point의 edge 없는 title/axis/category/exact-value Flowchart |
| Quadrant | 두 축 low/high label, 최대 256개 point의 exact `[0,1]` 좌표, zero-or-normal binary64와 pinned 500×500 canvas의 point/text 비충돌·비클리핑 | title/axis/quadrant/`label · x X, y Y` exact cell만 가진 edge 없는 Flowchart |
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
Mermaid 표현 가능성은 serializer가 판정하며, 실패하면 후보 단위로 끝납니다. Pie·XY·Quadrant는 native
renderer가 source 값·구조를 손실 없이 표시할 수 없거나 native runtime validation이 실패하면 같은 candidate
slot에서 exact-value Flowchart를 재검증합니다.

각 record의 bbox/evidence는 strict 검증 후 typed IR/review sidecar에 보존됩니다. 세 유형 모두 이 evidence를
generated Scene attribution과 record-local label/value 검증에 연결합니다. Quadrant slot label은 typed schema에
독립 evidence field가 없으므로 evidence를 축이나 point에서 합성·상속하지 않습니다. 공통 accessibility root와
미등록 extra metadata도 원본 dict에 보존되지만, 그 안의 숫자는 누락된 slice/axis/point 값을 채우는 chart data
evidence가 아닙니다.

### Pie terminal plan

Pie serializer·generated Scene·semantic OCR은 `plan_pie_records()`가 한 번 검증한 `PiePlan`을 공유합니다.
Plan은 source slice record, `pie_slice_N` Scene identity, exact fixed-decimal value, record-local evidence와
native/fallback별 source·canvas label을 고정합니다. Slice는 non-negative이고 전체 exact decimal 합계는
positive여야 합니다. Native `pie`는 최대 12개 slice만 허용하며, 각 값과 JavaScript의 왼쪽부터 더한 total이
zero-or-normal binary64로 exact round-trip되고 finite positive total과 finite centroid를 만들어야 합니다.
Mermaid 11.16이 1% 미만인 positive slice의 sector/percentage를 숨기므로 모든 positive slice가 1% 이상일 때만
native를 선택합니다. Zero slice는 legend만 보이고 sector·percentage가 없는 native record로 허용합니다.

Native canvas는 모든 legend와 positive visible slice의 반올림 percentage를 표시합니다. `show_data=true`이면
legend에 JavaScript `String(value)` 형식의 `[value]`도 붙으므로 그 문자열이 exact source decimal과 같을 때만
native를 허용합니다. 이 조건, 12-slice one-color-per-slice palette cap, binary64/geometry 조건 중 하나라도 맞지 않으면
최대 256개의 `label: exact-value` rectangle을 가진 disconnected `flowchart TB`를 사용합니다. Fallback은
sector 크기나 slice 간 edge를 만들지 않습니다. Native `CandidateValidator`가 parse/render/SVG/type gate에서
거부한 경우에도 새 후보를 소비하지 않고 같은 slot의 Flowchart를 한 번 전체 재검증합니다. 두 terminal 모두
Mermaid의 JavaScript `text.length`와 같은 50,000 UTF-16 code-unit 및 5,000 line preflight를 통과해야 합니다.

Native Scene은 slice마다 `sector` element 하나를 만들고 positive slice는 renderer percentage-label radius에
맞춘 normalized centroid, zero slice는 zero bbox를 사용합니다. Direction은 `radial`이며 relation/group은
없습니다. Element text는 실제 legend text이고 record-local evidence를 그대로 참조합니다. Native semantic
OCR은 visible title, 모든 legend, positive visible slice의 percentage를 세며 접근성 metadata는 세지 않습니다.
Flowchart Scene은 `TB` 방향의 zero-geometry rectangle cell만 만들고 relation/group을 추가하지 않으며,
OCR도 exact `label: value` cell만 셉니다. Native-only canvas title은 fallback으로 복사하지 않습니다.

Slice label의 quote와 backslash는 native source에서 escape하되 canvas에서는 보존하고, scanner/entity-active
directive·URL scheme·callback·CSS/icon token·`%%`·`//`·`<`·`&`·`#`·statement separator는 source에만
zero-width separator를 넣습니다. Native title은 Mermaid 11.16이 그대로
보존하지 못하는 quote/backslash/angle/hash/semicolon을 visible compatibility glyph로 바꿉니다. Flowchart
label도 quote/backslash/angle/hash를 visible glyph로 바꾸고 source-only separator를 적용합니다. Unicode
whitespace는 한 ASCII space로 고정합니다. Canvas-visible 치환은 warning으로 공개하고 semantic 원문은 typed
IR/review metadata에 유지합니다.

### XY terminal plan

XY serializer·generated Scene·semantic OCR은 `plan_xychart_records()`가 만든 하나의 bounded
`XYPlan`을 공유합니다. Plan은 axis·series·explicit point의 source record, deterministic Scene ID,
fixed-decimal x/y, record-local evidence, terminal별 source·canvas text를 고정합니다. Categorical mode는
각 value를 category text와 바인딩하고 numeric mode는 axis bound와 ordered value 또는 explicit x/y를 유지합니다.
Valid하지만 non-uniform한 explicit x는 실패하거나 균일하게 다시 만들지 않고 exact fallback cell에
원본 x/y를 남깁니다.

Native `xychart-beta`는 축 bound와 y, explicit x가 zero 또는 normal binary64로 exact round-trip되고
선언된 numeric 축 span이 positive normal finite일 때만 사용합니다. Numeric x-axis는 Mermaid 11.16의
`for (x = min; x <= max; x += step)` 동작을 입력 길이+1로 제한해 미리 실행하고, 매 step이
엄격히 증가하며 정확한 개수·시작·종료 좌표를 만드는지 확인합니다. 따라서 `[0,1]`에
10개 value를 놓았을 때 마지막 point가 사라지는 경우와 `2^53` 근처에서 float 덧셈이 진전하지
않는 무한 loop 위험을 runtime 전에 닫습니다.

추가로 line은 보이는 segment를 위해 두 point 이상이어야 하고, 동일 line 경로가 완전히
겹치거나 두 개 이상의 bar series가 같은 x/width를 공유해 가리는 경우, bar가 y-axis minimum에서
0 높이가 되는 경우는 fallback을 선택합니다. Pinned palette를 넘는 11번째 series도 fallback입니다.
Native 조건을 통과하지 못하면 최대 256 point의 disconnected `flowchart TB`를 사용하며,
visible title, 두 axis, category, category-bound value 또는 exact x/y cell을 추정 edge 없이 방출합니다.
Native CandidateValidator가 parse/render/SVG/type gate에서 거부해도 새 candidate를 소비하지 않고
같은 slot에서 이 Flowchart를 한 번 전체 재검증합니다. 두 terminal은 50,000 UTF-16 code-unit·5,000
line source preflight와 strict security scan을 공유합니다.

Native Scene은 normalized x/y axis, categorical tick anchor, hidden-text data point/bar를 만듭니다. Line은
인접 point 사이의 marker-less `series_line` association이고 bar는 y point에서 plot bottom까지의 bbox입니다.
Semantic OCR은 canvas에 실제로 보이는 title·axis label·category만 세고 hidden value와 accessibility
metadata는 제외합니다. Flowchart Scene은 source 순서와 동일한 zero-geometry title·axis·category·data
cell을 만들고 relation/group은 비웁니다. Quote·backslash·angle·hash compatibility glyph과
source-only scanner separator는 plan에서 terminal별로 고정하고 visible 치환을 warning으로 공개합니다.

### Quadrant terminal plan

Quadrant serializer·generated Scene·semantic OCR은 `plan_quadrant_records()`가 만든 bounded
`QuadrantPlan`을 공유합니다. Plan은 두 axis source record, supplied quadrant slot, point source record,
fixed-decimal x/y, deterministic Scene ID와 terminal별 source·canvas text를 한 번 고정합니다. Axis와 point의
malformed evidence는 해당 record에서만 비우며, slot은 schema에 없는 provenance를 만들지 않고 빈 evidence를
유지합니다. Point는 1개 이상 256개 이하이고 axis·point object를 서로 재사용할 수 없습니다.

Native `quadrantChart`는 모든 좌표가 `[0,1]` 안의 zero-or-normal binary64로 exact round-trip되고
`(x, 1-y)` canvas 위치가 finite일 때만 사용합니다. Pinned Mermaid 11.16의 500×500 canvas, title 유무에
따른 plot offset, point radius와 12/16px text 배치를 미리 계산해 서로 다른 source point가 같은 pixel에
접히거나 point/label/quadrant/axis/title이 겹치거나 잘리는 경우를 거부합니다. 비교는 candidate당 100,000회로
제한합니다. 따라서 duplicate coordinate, subnormal 차이, float collapse, 육안으로 분리되지 않는 근접 point와
긴 canvas text는 native에 보내지 않습니다.

Pinned Mermaid 11.16은 native Quadrant point의 HSL paint에 `NaN%` component를 생성합니다. SVG geometry와
label은 유한하고 consumer의 initial/inherited paint로 계속 표시될 수 있으므로 native를 강제 폐기하지는 않지만,
모든 native candidate에 paint compatibility warning을 남깁니다. Portable Flowchart fallback에는 이 renderer
전용 경고를 붙이지 않습니다.

Valid하지만 native-lossy한 입력은 disconnected `flowchart TB`로 낮춥니다. Fallback은 optional title,
`X axis: low to high`, `Y axis: low to high`, supplied slot의 named position, 그리고 각
`label · x exact-x, y exact-y` rectangle을 source 순서대로 만들며 edge나 quadrant geometry를 추정하지
않습니다. Native CandidateValidator가 security/parse/render/SVG/type gate에서 거부해도 새 candidate를
소비하지 않고 같은 slot의 Flowchart를 한 번 전체 재검증합니다. 두 terminal 모두 Mermaid JavaScript
`text.length`와 같은 50,000 UTF-16 code-unit·5,000줄 source preflight와 strict security scan을 통과해야
합니다. Point projection은 먼저 native/fallback line의 UTF-16 unit을 terminal별로 누적하고, 두 terminal이
모두 예산을 넘으면 source/canvas/fallback point 문자열을 복제하기 전에 중단합니다. 한 terminal만 넘으면
다른 terminal의 정상 출력을 보존합니다.

Native Scene은 visible axis endpoint 네 개와 normalized point circle을 만들고, `q1=upper-right`,
`q2=upper-left`, `q3=lower-left`, `q4=lower-right`의 네 `SceneGroup`을 둡니다. Axis line, quadrant membership,
point connector는 발명하지 않으므로 relation은 비우고 reading direction은 `unknown`입니다. Fallback Scene은
실제 emitted cell과 같은 순서의 zero-geometry rectangle만 가지며 relation/group은 비우고 `TB`를 사용합니다.
Semantic OCR도 native의 visible title·axis endpoint·supplied slot·point label 또는 fallback의 exact cell만
세고 point coordinate와 accessibility metadata를 native canvas text로 세지 않습니다.

자동 게시에는 global numeric completeness와 별도로 각 axis/point bbox 내부의 candidate-authorized
OCR/vector가 완전한 low/high 또는 label/x/y record를 증명해야 합니다. Record·observation·bbox 재사용,
axis/point swap, invalid geometry와 공유 100,000회 association budget 초과는 review로 내립니다. Axis owner는
horizontal·아래쪽 x bbox와 vertical·왼쪽 y bbox의 상대 geometry까지 맞아야 하므로 전체 axis record 교환도
승인되지 않습니다. Supplied slot
label은 source의 해당 사분면 안에 있는 독립 exact OCR/vector 관측 또는 reconstruction 초기의 exact
`user_edit` 중 유효한 source-quadrant bbox가 있는 것만 인정합니다. Explicit title/accessibility text도 data
record와 겹치지 않는 독립 근거가 필요합니다.
Direct Quadrant는 typed plan이 없어 review-only이며 engine이 새로 만든 `user_edit`는 자기 승인 근거가 될 수
없습니다. 현재 `VisualEvidence`에는 title/description semantic target이 없으므로 이 metadata 검사는 exact
content existence만 증명하고 두 role의 교환까지 판정하지 못합니다. `best_effort_validated`는 이 limitation을
경고하고 experimental 후보로 다루며 `strict_validated`는 review로 보냅니다. Visible compatibility 치환은
warning으로 공개하고 semantic 원문은 typed IR/review metadata에 보존합니다. Slot의 source quadrant는 아직
detected plot bbox가 아니라 전체 crop의 가로·세로 중점을 쓰는
보수적 heuristic이므로, inset 또는 off-center plot은 자동 승인하지 않고 review로 보낼 수 있습니다.

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

자동 게시에는 flow-local numeric attribution도 필요합니다. 각 flow는 source image 안에서 서로 양의 면적으로
겹치지 않는 bbox와 그 안에 완전히 포함된 candidate-authorized `ocr_token`/`vector_text`를 직접 인용해
plan의 exact `value_text`를 증명해야 하고, 전체 source/generated 숫자 occurrence도 정확히 일치해야 합니다.
Evidence ID나
normalized text+bbox를 다른 flow가 재사용하거나 같은 bbox의 상충 관측을 숨기는 경우, weight swap, 잘못된
geometry와 bounded association budget 초과는 부분 점수 없이 review입니다. Native와 same-slot Flowchart,
semantic repair는 같은 typed plan과 scoped evidence로 이 gate를 다시 계산하며 direct/untyped Sankey는
flow owner를 증명할 수 없어 review-only입니다.

Terminal별 귀속을 계산하기 전에 raw Sankey IR의 `title`, `description`, `acc_title`, `acc_description`을
accessibility enrichment와 분리해 검증합니다. Reconstruction pipeline 후보 경계와 public typed serializer가
같은 규칙을 적용하며, 값이 `None`이 아니면 subclass가 아닌 exact built-in `str`여야 합니다. 숫자·container·
custom string subclass는 거부됩니다. 정규화 작업 전에 raw 문자열 길이가 `MAX_TEXT_CHARS` 이하인지 먼저
검사하고, 호환용 exact `""`을 제외한 문자열은 whitespace 정규화 뒤에도 non-empty·bounded여야 합니다. 또한
UTF-8로 encoding할 수 있고 정규화된 text에 Unicode category `Cc`/`Cf`/`Zl`/`Zp` 문자가 없어야 합니다. 따라서
huge-whitespace를 포함한 overlong raw/normalized text, whitespace-only, ZWSP/control-only, lone-surrogate 입력도
provider별 Mermaid 직렬화나 runtime 호출 전에 실패합니다. JSON `null`은 필드 부재와 같고, 기존 Pie/XY
호환성을 유지하기 위해 exact `""`은 허용하되 명시적 빈 metadata로 방출하지 않고 omitted로 해석해
deterministic accessibility text를 파생합니다.

접근성 귀속은 terminal별로 다릅니다. Native Sankey는 title/description을 방출하지 않으므로 이 metadata
gate의 대상이 아닙니다. Same-slot Flowchart fallback은 resolved accessibility title과 description을 SVG
metadata로 방출하며 content OCR label로 세지 않습니다. 이때 `acc_title`이 `title`을, `acc_description`이
`description`을 output에서 shadow하면 방출되지 않는 legacy text는 면제합니다. 실제 방출되는 non-derived
resolved title과 description 두 역할은 서로 독립적으로, 어떤 node/flow data record도 소유하지 않고 그 record
bbox와 겹치지 않는 candidate-authorized spatial `ocr_token`/`vector_text` exact observation 또는 reconstruction
초기 입력에서 승인된 exact `user_edit`로 증명되어야 합니다. 구조에서 결정적으로 파생한 기본 문구와
experimental notice는 예외입니다. Node나 flow record가 인용한 evidence ID 또는 normalized text+bbox의 재사용,
same-bbox ambiguity, metadata bbox와 node/flow bbox의 overlap, 필요한 data-record bbox의 missing/invalid geometry,
공유 reference/text/token/spatial budget 초과, engine-emitted `user_edit`의 자기 승인은 review로 닫습니다.
근거로 실제 선택된 OCR/vector metadata의 numeric token만 flow-weight 전역 reference에서 제외하며, 귀속되지
않은 추가 숫자는 계속 mismatch입니다. Semantic repair도 새 typed IR과 scoped evidence로 이 terminal gate를
다시 계산합니다.

Radar serializer·Scene·semantic OCR은 `plan_radar_records()`의 같은 bounded plan을 공유합니다. Plan은
dimension/series source record, terminal 전체에서 충돌하지 않는 emitted ID, exact fixed-decimal value,
terminal별 source/canvas label, point별 dimension+series evidence를 한 번 고정합니다. Radar grammar 예약어와
Flowchart group/cell ID까지 하나의 namespace에서 collision-safe suffix로 분리합니다. Malformed evidence list는
해당 record에서만 원자적으로 비우고, point provenance의 bounded 합집합을 만들 수 없으면 그 point evidence
전체를 비웁니다. Dimension은 최대 256개, 전체 point와 Scene element는 공용 Scene budget을 지키며 native와
fallback source 모두 50,000 UTF-16 code-unit·5,000줄 preflight를 통과합니다.

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
`dimension: exact-value` cell이며 edge는 만들지 않습니다. Visible title은 isolated title node로 보존하고,
series subgraph의 visible label은 `showLegend=true`일 때만 방출합니다. Fallback Scene/OCR도 이
title·conditional group label·cell을 그대로 투영하며 bounds/ticks/graticule은 canvas content로 가장하지
않습니다. 256-point fallback을 만들 수 없는
valid native candidate가 runtime에서 거부되면 partial code/Scene 대신 unavailable입니다. Strict scanner용
source separator와 angle/hash, fallback quote/backslash 등의 visible compatibility glyph을 분리하고, visible
치환은 native/fallback warning에 공개합니다. CandidateValidator의 SVG inspection은 Mermaid가 render 성공을
보고해도 geometry attribute에 `NaN`/`Infinity`가 있으면 render-invalid로 닫습니다.

Native generated-node provenance gate는 실제로 직접 귀속할 수 있는 axis와 series를 평가하고, series에서
파생된 data point는 분모에서 제외합니다. Flowchart fallback의 point cell은 dimension과 series record를 함께
참조하므로 node 단위로 evidence를 독점시키지 않고 아래 Radar-local association으로 두 owner를 검증합니다.
어느 cell도 알려진 record evidence가 없으면 별도의 generated-node provenance gate도 통과하지 못합니다.

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

Treemap 자동 게시에는 공용 plan의 모든 node에 대한 record-local source 결합이 추가로 필요합니다.
각 node bbox는 source image 안의 양의 면적이어야 하고, child bbox는 parent에 완전히 포함되되 동일할 수 없으며 같은 parent의
직접 child끼리는 interior가 겹치지 않아야 하며 edge-touch는 허용합니다. Parent와 descendant의 중첩은 계층 자체이므로 허용하지만,
internal node가 인용한 text evidence는 직접 child 영역과 겹칠 수 없습니다. 각 node는 자신의 bbox 안에 완전히
들어오는 candidate-authorized `ocr_token`/`vector_text`를 직접 인용해 exact label을 증명하고, explicit value가
있으면 label 뒤의 fixed-decimal value까지 같은 reading-order record로 증명합니다. Typed value나 source-wide
`ocr_texts`만으로 이 소유권을 만들 수 없습니다.

Native renderer가 계산해 표시하는 internal `native_total_text`는 typed IR의 explicit source value가 아니라
결정적으로 파생한 output입니다. 현재 local owner record는 이 값을 source citation 대용으로 인정하지 않습니다.
Source OCR/vector가 internal total을 별도 숫자로 관측하면 전역 numeric occurrence에도 extra token으로 남으므로
보수적으로 review가 필요합니다. 이 동작은 작은-cell visibility 문제와 함께 향후 terminal-aware derived-total
평가를 도입하기 전까지 유지합니다.

Evidence ID와 normalized text+bbox observation은 node 사이에서 재사용할 수 없고 한 node 안의 duplicate
evidence reference도 허용하지 않습니다. 같은 bbox의 상충 text, equal/crossing parent-child bbox, sibling overlap,
missing/invalid geometry도 전체 결합을 unavailable/review로 둡니다. Aggregate reference/text/character/token/
spatial-comparison budget은 각각 20,000/50,000/1,000,000/100,000/100,000입니다. 결합된 label/value가 다르면
association mismatch로 aggregate가 unavailable이지만 `numeric_consistency`는 전역 multiset 진단값을 유지할 수
있습니다. 자동 게시에는 local 결합과 전역 numeric occurrence가 모두 exact여야 합니다. 이 gate는 native, same-slot
Flowchart, semantic repair에서 동일하게 다시 실행되고 typed plan이 없는 direct Treemap은 자동 게시하지
않습니다. Source bbox는 이 검증과 review provenance에만 쓰며 generated Scene의 zero geometry 계약은 바뀌지
않습니다.

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

pipeline의 일반 numeric consistency는 source와 generated 숫자 occurrence multiset F1입니다. Bounded evidence
안에서는 동일 normalized text+bbox를 한 관측으로 합치고, OCR context와 evidence 채널의 token Counter는
token별 최대 occurrence로 병합합니다. 따라서 위치가 다른 반복값은 보존하면서 채널 간 중복 보고는 다시
세지 않습니다. Source에 없는 숫자나 횟수 불일치는 precision/recall을 낮춥니다. Typed chart value나 그
record의 `evidence_ids`만으로 source 숫자 관측을 대체할 수 없습니다. Typed/Scene 후보는 semantic type으로
gate를 유지하고, direct 후보만 parse/render validation으로 확정한 emitted/runtime type을 사용합니다.
결과 type이 Gantt/Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn이면 source OCR/vector numeric evidence가
하나도 없을 때 syntax/render가 성공해도 `U` 등급 review 대상으로 남고, 일치도가 threshold보다 낮아도 자동
게시되지 않습니다.

Pie는 전역 숫자 multiset만으로 label/value swap을 검출할 수 없으므로 slice-local association과 전역
numeric completeness를 함께 요구합니다.
각 typed slice는 양의 면적이며 source image 안에 있는 서로 겹치지 않는 bbox와, candidate publication
authority에 포함된 `ocr_token`/`vector_text` evidence를 직접 인용해야 합니다. Evidence bbox 전체가 해당
slice bbox 안에 있고, bbox reading order로 합친 관측이 punctuation-preserving `label + 허용 separator + value`
record와 정확히 일치하며, 그 slice의 label에 포함된 숫자와 exact value의 numeric multiset도 같아야 합니다.
또한 전체 bounded source OCR/vector 숫자와 생성 data 숫자의 occurrence multiset이 정확히 일치할 때만
`numeric_consistency=1.0`입니다. Slice-local 소유권에만 candidate publication authority를 요구합니다.
Label은 결합됐지만 값이 바뀌거나 관계없는 source 숫자가 더 있으면 `0.0`과 review입니다. Source-wide
`ocr_texts`는 전역 completeness에는 들어가지만 slice 소유권을 만들 수 없습니다.
겹치는 slice, broad/shared evidence, 같은 evidence ID나 normalized text+bbox의 교차 slice 재사용, 같은 bbox의
상충 text, invalid geometry/authority 또는 association budget 소진은 전체 Pie binding을 unavailable/review로
둡니다. 이 검사는 native, exact-value Flowchart, semantic repair에 동일하게 다시 적용되고 direct Pie처럼
typed slice slot이 없는 후보도 자동 게시하지 않습니다. 누락된 uncited slice나 숫자도 전역 completeness에서
review로 닫힙니다. Pie generated slice element는 별도의 80% provenance
gate도 통과해야 하므로 숫자 결합만으로 unattributed slice가 게시되지는 않습니다.

Explicit Pie `title`/`acc_title`과 `description`/`acc_description`도 독립적인 candidate-authorized spatial
OCR/vector exact observation 또는 reconstruction 초기 입력으로 전달된 exact `user_edit` evidence가 없으면
review입니다. Engine이 새로 생성한 `user_edit`는 스스로 승인 근거가 될 수 없습니다. Slice-owned observation의
ID만 바꾸거나 slice bbox와 겹치는 관측을 재사용할 수 없습니다. 구조에서 결정적으로 파생한 접근성 기본
문구와 experimental notice는 이 gate의 대상이 아닙니다.

Packet도 이 전역 occurrence multiset의 예외입니다. Native Packet, Flowchart runtime fallback, semantic
repair proposal 모두 candidate-authorized field-local association을 다시 계산합니다. Field가 직접 인용한
OCR/vector evidence의 bbox 전체가 양의 면적의 field bbox 안에 있고 둘 다 실제 image bounds 안에 있을 때만
label과 bit range를 결합하며 source-wide `ocr_texts`는 binding에 사용할 수 없습니다. Exact label+range는
`1.0`, 결합된 잘못된 range나 추가 숫자는 `0.0`과 review입니다. Single-bit `start == end`는 endpoint 숫자
한 번을 요구합니다. 동일 normalized text+bbox의 OCR/vector 중복은 한 번만 세고 공간적으로 다른 반복은
유지합니다. 겹치는 field, broad/shared/같은 위치의 모호한 관측, 누락되거나 잘못된 authority·bbox·image
bounds, association budget 소진은 unavailable/review이며 전역 multiset이나 게시 threshold로 우회하지
않습니다.

Radar도 전역 숫자 multiset에 더해 dimension/series-local association을 요구합니다. 모든 dimension과 series
record는 source image 안의 양의 면적이며 서로 겹치지 않는 bbox를 가져야 하고, candidate publication authority의
`ocr_token`/`vector_text` evidence를 직접 인용해야 합니다. Dimension 관측은 exact label, series 관측은 exact
label과 원래 순서의 모든 fixed-decimal value를 한 record로 결합해야 합니다. Evidence bbox는 owner bbox 안에
완전히 포함되어야 하며 bbox reading order로 합친 text만 허용된 bounded 표기와 비교합니다. 같은 evidence ID나
normalized text+bbox를 여러 owner가 재사용하거나, 인용하지 않은 상충 text가 같은 bbox에 있거나, record가
겹치거나, geometry/reference/text/token/comparison budget을 확인할 수 없으면 전체 binding을
unavailable/review로 닫습니다. 결합된 label 또는 value 순서가 다르면 `0.0`, local binding과 전역 occurrence가
모두 exact일 때만 `1.0`입니다. 이 검사는 native, same-slot Flowchart, semantic repair에 공통이며 typed Radar
plan이 없는 direct candidate는 자동 게시하지 않습니다. Pie·XY·Quadrant·Sankey·Radar·Treemap과 Packet을
제외한 numeric type의 multiset 계산은 그대로입니다.

Radar의 visible `title`과 non-derived explicit `acc_title`/`description`/`acc_description`도 data record와
독립된 근거가 필요합니다. Candidate-authorized OCR/vector observation은 모든 dimension/series bbox 밖의 유효한
source 위치에 있어야 하고, reconstruction 초기 입력에서 승인된 exact `user_edit`도 사용할 수 있습니다. Record가
이미 소유한 evidence, 같은 text+bbox 재사용, engine이 새로 만든 user edit, 모호하거나 budget을 넘긴 비교는
metadata를 승인하지 않습니다. 구조에서 파생한 기본 접근성 문구와 experimental notice는 대상이 아닙니다.

Generated numeric projection은 Mermaid `%%` comment를 제외하고, detected grammar가 지원할 때만 native
`title ...`, colon `title: ...`, `accTitle: ...`, 한 줄 `accDescr: ...`, block `accDescr { ... }`를
metadata로 제외합니다. Sankey에서는 이 문자열로 시작하는 CSV label도 실제 data이므로 row와 weight 숫자를
그대로 셉니다. Quadrant의 `quadrant-1`~`quadrant-4` directive index는 문법 토큰이므로 제외하지만 directive
label이나 point 좌표 안의 실제 숫자는 유지합니다. Source collector는 title/accessibility 영역을 구분하지
않으므로 그 영역에서 관측된 숫자도 전역 completeness에 포함되어 보수적인 review를 유발할 수 있습니다.
Venn은 size가 없는 portable fallback을 만들 수 있어도 numeric mandatory type이므로 자동 게이트를
우회하지 않습니다.
