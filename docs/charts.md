# 차트 serializer와 숫자 안전성

차트 typed IR은 OCR/VLM이 읽지 못한 값을 보간하지 않습니다. Structured VLM 경계의 숫자는 bool이나
숫자 문자열을 허용하지 않는 strict finite JSON `int`/`float`이며 잘못된 값은 candidate validation에서
거부됩니다. 직접 serializer API는 `Decimal`도 받지만 provider 응답 계약에는 포함하지 않습니다. 이 API는
NaN/Infinity, unknown endpoint, series 길이 불일치, 잘못된 축 범위를 `SerializationError`로 거부합니다.

| type | native 조건 | fallback |
| --- | --- | --- |
| Pie | 고유 label, non-negative slice, positive total | 없음; 값 누락은 실패 |
| XY | category/value 길이 일치 또는 explicit uniform numeric x grid, y bounds | 없음; 비균일 x는 왜곡 대신 실패 |
| Quadrant | 두 축 low/high label, 모든 point의 explicit `[0,1]` 좌표 | 없음 |
| Sankey | positive weighted DAG, 모든 node 참여, native-safe 고유 label | exact weight label을 가진 flowchart |
| Radar | 3개 이상 dimension, 동일 series 길이, 일관 bounds, non-negative domain | edge 없는 tabular flowchart |
| Treemap | hierarchy leaf마다 explicit positive value | internal-node value나 native runtime 실패 시 value-label hierarchy |
| Venn | explicit set/intersection과 모든 관측 size | size 누락 시 숫자를 합성하지 않는 set/intersection graph |

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

모든 native/fallback 대표 fixture는 Mermaid 11.16 strict `CandidateValidator`의 parse/render/SVG 검사를
통과합니다. Sankey grammar는 title/accTitle/accDescr를 표현하지 못하므로 해당 text를 typed IR과 warning에
남깁니다. Treemap/Venn의 experimental native grammar도 runtime type을 sidecar에 기록합니다.

pipeline의 numeric consistency는 source와 generated 숫자 occurrence multiset F1입니다. Bounded evidence
안에서는 동일 normalized text+bbox를 한 관측으로 합치고, OCR context와 evidence 채널의 token Counter는
token별 최대 occurrence로 병합합니다. 따라서 위치가 다른 반복값은 보존하면서 채널 간 중복 보고는 다시
세지 않습니다. Source에 없는 숫자나 횟수 불일치는 precision/recall을 낮춥니다. Typed chart value나 그
record의 `evidence_ids`만으로 source 숫자 관측을 대체할 수 없습니다. Typed/Scene 후보는 semantic type으로
gate를 유지하고, direct 후보만 parse/render validation으로 확정한 emitted/runtime type을 사용합니다.
결과 type이 Gantt/Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn/Packet이면 source OCR/vector numeric evidence가
하나도 없을 때 syntax/render가 성공해도 `U` 등급 review 대상으로 남고, 일치도가 threshold보다 낮아도 자동
게시되지 않습니다.

Generated numeric projection은 Mermaid `%%` comment를 제외하고, detected grammar가 지원할 때만 native
`title ...`, colon `title: ...`, `accTitle: ...`, 한 줄 `accDescr: ...`, block `accDescr { ... }`를
metadata로 제외합니다. Sankey에서는 이 문자열로 시작하는 CSV label도 실제 data이므로 row와 weight 숫자를
그대로 셉니다. Quadrant의 `quadrant-1`~`quadrant-4` directive index는 문법 토큰이므로 제외하지만 directive
label이나 point 좌표 안의 실제 숫자는 유지합니다. 지원되는 metadata는 source numeric evidence로도 사용되지
않습니다. Venn은 size가 없는 portable fallback을 만들 수 있어도 numeric mandatory type이므로 자동 게이트를
우회하지 않습니다.
