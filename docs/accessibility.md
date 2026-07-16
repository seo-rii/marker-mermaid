# 접근성 생성

성공한 reconstruction에는 가능한 경우 Mermaid `accTitle`과 `accDescr`를 넣습니다. 명시적
`acc_title`/`acc_description`을 우선하고, 없으면 requested semantic type과 관측된 typed IR label에서
결정적으로 생성합니다. 명시적 title/description은 그 다음 fallback입니다.

파생 description은 최대 다섯 개의 label을 나열합니다. 명시적 directed graph에 root와 terminal이
각각 하나일 때만 시작·종료를 덧붙입니다. Chart의 증가·감소, 관계 의미, 누락 숫자를 추측하지
않습니다. Experimental 후보에는 review가 필요하다는 문구를 한 번만 추가합니다. 일반 후보의 생성된 값은
code와 enriched `typed_ir`에 함께 저장되어 directive를 지원하지 않는 문법에서도 review metadata로 남습니다.
State·Gantt·ER·Sequence·Mindmap·Timeline처럼 terminal별 source/canvas plan을 가진 유형은 예외입니다. 이 유형은 stale derived text를
막기 위해 candidate `typed_ir`에 validated raw metadata snapshot을 유지하고, initial serialization과 accepted
repair마다 현재 semantic record에서 접근성 값을 다시 계산합니다.

## Semantic type과 emitted grammar

내용 파생은 요청 유형을 사용합니다. 예를 들어 C4→Architecture fallback도 제목은 “C4 model
reconstruction”입니다. 실제 directive 삽입 가능 여부는 emitted Mermaid grammar를 사용합니다.

Pinned Mermaid 11.16에서 다음 native grammar에는 directive를 넣지 않습니다.

| Grammar | 이유 |
| --- | --- |
| Mindmap | directive를 추가 root로 해석해 parse 실패 |
| Block | `accTitle`/`accDescr` parse 거부 |
| Sankey | directive를 CSV flow row로 해석 |
| Venn | canvas `title`만 지원하고 `accTitle`/`accDescr` parse 거부 |
| Ishikawa | directive를 cause text로 렌더하고 SVG `<title>/<desc>`를 만들지 않음 |
| Timeline/Journey | directive를 받아들이지만 SVG 접근성 element를 만들지 않음 |
| Kanban | directive를 받아들이지만 SVG 접근성 element를 만들지 않음 |

이 유형은 limitation warning을 남깁니다. 일반 unsupported grammar는 enriched typed IR에도 resolved text를
유지하지만, stale derived text를 막는 Mindmap/Timeline terminal은 예외로 candidate typed IR에 validated raw
snapshot만 저장하고 serialization/review 시 현재 record plan에서 값을 다시 계산합니다. Portable flowchart
fallback이 선택되면 fallback grammar가 directive를 지원하므로 동일한 resolved text를 정상 출력합니다.

Native Venn의 explicit `title`은 canvas content이므로 Scene/OCR에도 포함합니다. 반면 resolved
`accTitle`/`accDescr`는 native source에 넣지 않고 enriched typed IR과 limitation warning에만 남깁니다.
Venn이 same-slot Flowchart fallback으로 내려가면 resolved text를 SVG 접근성 metadata로 방출하지만 canvas OCR
label로 세지 않으며 native-only title도 복사하지 않습니다. Grammar-unsafe visible text를 compatibility glyph로
바꾼 경우 native candidate warning 또는 fallback reason에 공개합니다.

Native ER은 pinned Mermaid 11.16의 `accTitle`/`accDescr`를 SVG `<title>`/`<desc>` metadata로 방출합니다.
Record plan과 분리된 accessibility plan은 explicit `acc_title`/`acc_description`, 그 다음
`title`/`description`을 우선하고, 없으면 현재 semantic entity label에서 기본 문구를 만듭니다. 네 raw metadata
필드는 enrichment 전에 exact built-in string, bounds, UTF-8와 Unicode category를 검사하며 exact empty는
omitted로 처리합니다. Entity/attribute/relationship의 canvas text와 달리 접근성 metadata는 semantic OCR
content로 세지 않습니다. Numeric entity-like text처럼 directive canvas가 원문을 보존하지 못하는 경우에는
visible compatibility glyph와 warning을 사용하고 semantic 원문은 validated raw typed/review IR에 유지합니다.
Accepted repair는 raw snapshot에서 accessibility plan을 다시 만들어 derived description과 compatibility warning을
현재 entity plan에 맞춥니다.

Native Sequence도 pinned Mermaid 11.16의 `accTitle`/`accDescr`를 SVG `<title>`/`<desc>` metadata로
방출합니다. Resolution 순서는 `acc_title > title`, `acc_description > description`이고, 명시값이 없으면
현재 participant plan의 semantic label에서 결정적으로 파생합니다. 네 raw 필드는 enrichment 전에 exact
built-in string, raw/normalized bound, UTF-8와 Unicode category를 검사하고 exact `""`은 omitted으로
처리합니다. Initial candidate와 accepted repair에는 파생 `acc_*`가 아니라 validated raw snapshot을
저장하므로 participant 수정 뒤 description과 compatibility warning을 현재 plan에서 다시 계산합니다.

Participant/message의 `#`와 `;`는 native Sequence escape를 통해 canvas 원문을 보존하지만 접근성의 literal
`<`/`>`는 Mermaid가 double-escape하므로 `〈`/`〉`로 표시하고 조건부 warning을 남깁니다. Source-only
separator는 scanner/lexer 동작만 끄며 semantic 원문은 typed/review IR에 유지합니다. 이 metadata는
participant/message Scene/OCR content가 아니므로 구조 label recall에는 포함하지 않습니다.

Native Mindmap은 `accTitle`/`accDescr`를 node와 같은 추가 root로 해석하므로 directive를 source에 넣지
않습니다. 대신 resolver는 raw `acc_title > title`, `acc_description > description`을 우선하고, 명시값이
없으면 현재 preorder node plan의 semantic label에서 값을 결정적으로 파생합니다. 네 raw
필드는 enrichment 전에 exact built-in string, raw/normalized bound, UTF-8와 Unicode category를 검사하고 exact
`""`은 omitted으로 처리합니다. Initial candidate와 accepted repair는 파생 `acc_*`가 아닌 validated raw
snapshot만 저장하므로 hierarchy label 수정 뒤 description과 조건부 compatibility warning을 다시 계산할 수
있습니다. 파생 `acc_*` 자체는 candidate typed IR에 persist하지 않습니다. 이 값은 Mindmap canvas OCR content가
아니며 limitation warning으로 source/SVG 미방출 사실을 공개합니다.

Native Timeline은 `accTitle`/`accDescr`를 parse하지만 pinned Mermaid 11.16 SVG에 `<title>`/`<desc>`를
만들지 않으므로 directive를 source에 넣지 않습니다. Resolver는 `acc_title > title`,
`acc_description > description` 결과를 현재 raw snapshot에서 매번 계산하고 limitation warning을
기록합니다. 네 raw metadata 필드는 generic enrichment 전에 exact built-in string, raw/normalized bound,
UTF-8/Unicode category와 exact-empty-as-omitted gate를 통과합니다. Initial candidate와 accepted repair는 파생
`acc_*`가 아닌 validated raw snapshot을 저장해 event 수정 때 현재 semantic period/event plan에서 설명을 다시
만들며 파생 field 자체는 persist하지 않습니다. 이 값은 Timeline canvas OCR label이 아닙니다.

Native Pie는 pinned Mermaid 11.16에서 `accTitle`/`accDescr`를 지원하므로 resolved text를 SVG
`<title>`/`<desc>` metadata로 방출합니다. 별도의 explicit `title`은 Pie canvas content이므로 native semantic
OCR에 포함하지만 접근성 directive는 content label로 세지 않습니다. Native 조건을 벗어나거나 runtime
validation이 실패해 같은-slot exact-value Flowchart가 선택되면 resolved title/description은 Flowchart 접근성
metadata로 유지되고 description에는 proportional sector가 아니라 exact-value fallback이라는 설명을
덧붙입니다. Native-only canvas title은 fallback cell이나 OCR에 복사하지 않습니다.

Pie slice label의 source-only separator는 SVG DOM에 zero-width로 남아도 canvas glyph를 바꾸지 않으며,
semantic OCR은 separator를 제외한 visible label을 사용합니다. Native title의
quote/backslash/angle/hash/semicolon과 Flowchart cell의
quote/backslash/angle/hash가 visible compatibility glyph로 바뀌면 warning으로 공개하고, resolved 접근성 text와
semantic 원문은 enriched typed IR/review metadata에 보존합니다.

Pie의 explicit `title`/`acc_title`과 `description`/`acc_description` 네 필드는 모두 candidate-authorized 독립
OCR/vector 관측과 정확히 일치해야 자동 게시할 수 있습니다. 출력 resolution은 `acc_title > title`,
`acc_description > description` 순서지만, 가려진 explicit text도 보수적으로 검증합니다. 해당 관측은 slice가
소유한 ID·normalized text+bbox를 재사용하거나 slice bbox와 겹칠 수 없습니다. Review 결과처럼
reconstruction 초기 입력으로 전달된 `user_edit`의 exact text도 허용하지만 engine이 새로 방출한 `user_edit`는
승인 근거가 아닙니다. 구조에서 결정적으로 파생한 기본 접근성 문구와 `experimental` notice는 별도 source
관측을 요구하지 않습니다. Quote와 numeric entity-like text는 source-only separator를 거쳐
`<title>`/`<desc>`에서 원문 그대로 유지됩니다.

Native XY Chart도 pinned Mermaid 11.16의 `accTitle`/`accDescr`를 사용해 SVG accessibility
metadata를 만듭니다. 별도 `title`은 canvas에 보이므로 semantic OCR에 포함하지만 axis
bound, series value, 자동 tick과 accessibility metadata는 visible content text로 세지 않습니다. Native의
binary64/grid/visibility 조건을 만족하지 못하거나 runtime validation이 실패하면 같은 candidate
slot의 exact-value Flowchart로 낮춥니다. 이 terminal은 resolved accessibility metadata를 유지하고
description에 proportional plot이 아닌 exact-value fallback임을 덧붙이며, explicit canvas title은 별도
rectangle으로 표시합니다.

XY의 explicit `title`/`acc_title`과 `description`/`acc_description`도 자동 게시 전에 독립된
candidate-authorized OCR/vector exact text 또는 reconstruction 초기의 exact `user_edit` evidence가 필요합니다.
Axis/series/point record가 소유한 observation·bbox와 겹치거나 그 evidence ID를 재사용할 수 없으며,
engine이 새로 만든 `user_edit`는 승인 근거가 되지 않습니다. 구조에서 결정적으로 파생한
기본 title/description과 experimental notice만 별도 source observation 없이 허용합니다. Native/fallback
text의 quote·backslash·angle·hash 치환은 compatibility warning으로 공개하고 semantic 원문은
enriched typed IR·review metadata에 보존합니다.

Native Quadrant도 `accTitle`/`accDescr`를 SVG accessibility metadata로 방출합니다. 별도의 explicit
`title`은 canvas content이고 두 axis endpoint, supplied quadrant label, point label과 함께 semantic OCR에
포함합니다. 좌표 숫자는 point geometry이며 native canvas text가 아니고 accessibility directive도 content
label로 세지 않습니다. Explicit description이 없으면 파생 `accDescr`는 source 순서의 point label을 최대
다섯 개까지 포함하되 좌표의 추세나 사분면 소속을 추측하지 않습니다. Binary64/pixel/text visibility gate를
통과하지 못하거나 native runtime validation이
실패하면 같은 candidate slot의 disconnected exact-value Flowchart를 사용합니다. 이 terminal은 resolved
accessibility metadata를 유지하고 title·axis·slot·`label · x X, y Y` cell을 실제 canvas text로 셉니다.

Explicit `title`/`description`/`acc_title`/`acc_description`은 accessibility enrichment 전에 non-empty bounded
text인지 검사합니다. 따라서 빈 directive가 다음 axis directive를 title/description으로 삼키는 Mermaid 문법
오류를 만들 수 없습니다. 자동 게시에는 axis/point 관측과 분리된 exact OCR/vector 또는 reconstruction 초기의
exact `user_edit`가 필요하며, supplied slot label도 해당 source 사분면의 독립 근거를 가져야 합니다. Engine이
새로 만든 `user_edit`, bbox가 없어 slot 위치를 증명하지 못하는 edit나 axis/point evidence의 재사용은 승인
근거가 아닙니다. Native/fallback visible
compatibility glyph는 warning으로 공개하고 semantic 원문은 enriched typed IR·review metadata에 보존합니다.
현재 evidence model에는 title과 description을 구분하는 immutable target role이 없으므로 독립 관측은 exact
content existence만 증명합니다. Best-effort 정책은 role 교환을 자동으로 맞다고 주장하지 않고 limitation
warning을 남기며, strict validated 정책은 role-bound provenance가 도입될 때까지 review를 요구합니다.

Native Radar는 pinned Mermaid 11.16에서 `accTitle`/`accDescr`를 지원하므로 두 directive를 SVG
`<title>`/`<desc>` metadata로 방출합니다. 별도의 explicit `title`만 radar canvas에 보이며 semantic OCR에는
이 visible title, axis label, `showLegend=true`일 때의 series legend만 들어갑니다. Value, `min`/`max`,
`ticks`, `graticule`과 접근성 metadata는 geometry 또는 hidden option이므로 content OCR text가 아닙니다.
Same-slot Flowchart fallback도 resolved accessibility text를 metadata로 보존하지만 native-only canvas title은
복사하지 않으며, OCR은 series subgraph label과 `dimension: exact-value` cell만 셉니다. Native/fallback
visible text가 Mermaid 문법을 피하기 위해 호환 glyph로 바뀌면 각각 candidate warning에 공개하고 원문은 typed
IR과 review metadata에 유지합니다.

## Direct Mermaid

Raw/direct 후보에는 예측 type만 보고 source를 수정하지 않습니다. 원본 후보를 먼저 security scan,
parse, render한 뒤 canonical runtime type이 지원 grammar임을 확인하고 누락 directive를 삽입합니다.
보강된 source는 다시 전체 validation을 통과하고 runtime type이 유지될 때만 채택합니다. 실패 또는 type
drift가 있으면 원본 valid 후보를 유지하고 warning을 남깁니다.

접근성 metadata 안의 숫자는 chart data가 아닙니다. 따라서 detected grammar가 해당 directive를 지원할 때만
`accTitle: ...`, 한 줄 `accDescr: ...`, block `accDescr { ... }`의 본문 전체를 `numeric_consistency`
generated multiset에서 제외합니다. Native `title ...`와 colon `title: ...`도 grammar가 지원하는 metadata
경계에서만 제외하며 chart label과 좌표/value 안의 실제 숫자는 유지합니다. Sankey처럼 이 directive를
지원하지 않는 grammar의 metadata-like CSV label은 실제 data이므로 제거하지 않습니다. OCR recall은 원 label
coverage를 계속 평가합니다.
