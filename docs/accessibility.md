# 접근성 생성

성공한 reconstruction에는 가능한 경우 Mermaid `accTitle`과 `accDescr`를 넣습니다. 명시적
`acc_title`/`acc_description`을 우선하고, 없으면 requested semantic type과 관측된 typed IR label에서
결정적으로 생성합니다. 명시적 title/description은 그 다음 fallback입니다.

파생 description은 최대 다섯 개의 label을 나열합니다. 명시적 directed graph에 root와 terminal이
각각 하나일 때만 시작·종료를 덧붙입니다. Chart의 증가·감소, 관계 의미, 누락 숫자를 추측하지
않습니다. Experimental 후보에는 review가 필요하다는 문구를 한 번만 추가합니다. 생성된 값은 code뿐
아니라 candidate의 `typed_ir`에도 저장되어 directive를 지원하지 않는 문법에서도 review metadata로
남습니다.

## Semantic type과 emitted grammar

내용 파생은 요청 유형을 사용합니다. 예를 들어 C4→Architecture fallback도 제목은 “C4 model
reconstruction”입니다. 실제 directive 삽입 가능 여부는 emitted Mermaid grammar를 사용합니다.

Pinned Mermaid 11.16에서 다음 native grammar에는 directive를 넣지 않습니다.

| Grammar | 이유 |
| --- | --- |
| Mindmap | directive를 추가 root로 해석해 parse 실패 |
| Block | `accTitle`/`accDescr` parse 거부 |
| Sankey | directive를 CSV flow row로 해석 |
| Venn | `title`만 지원하고 accessibility directive parse 거부 |
| Ishikawa | directive를 cause text로 렌더하고 SVG `<title>/<desc>`를 만들지 않음 |
| Timeline/Journey | directive를 받아들이지만 SVG 접근성 element를 만들지 않음 |
| Kanban | directive를 받아들이지만 SVG 접근성 element를 만들지 않음 |

이 유형은 limitation warning과 enriched typed IR을 남깁니다. Portable flowchart fallback이 선택되면
fallback grammar가 directive를 지원하므로 동일한 resolved text를 정상 출력합니다.

## Direct Mermaid

Raw/direct 후보에는 예측 type만 보고 source를 수정하지 않습니다. 원본 후보를 먼저 security scan,
parse, render한 뒤 canonical runtime type이 지원 grammar임을 확인하고 누락 directive를 삽입합니다.
보강된 source는 다시 전체 validation을 통과하고 runtime type이 유지될 때만 채택합니다. 실패 또는 type
drift가 있으면 원본 valid 후보를 유지하고 warning을 남깁니다.

접근성 metadata 안의 숫자는 chart data가 아니므로 `numeric_consistency` multiset에서는 제외합니다.
OCR recall은 원 label coverage를 계속 평가합니다.
