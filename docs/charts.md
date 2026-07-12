# 차트 serializer와 숫자 안전성

차트 typed IR은 OCR/VLM이 읽지 못한 값을 보간하지 않습니다. 숫자는 bool이나 숫자 문자열을 허용하지
않고 explicit finite `int`/`float`/`Decimal`만 받습니다. NaN/Infinity, unknown endpoint, series 길이
불일치, 잘못된 축 범위는 `SerializationError`입니다.

| type | native 조건 | fallback |
| --- | --- | --- |
| Pie | 고유 label, non-negative slice, positive total | 없음; 값 누락은 실패 |
| XY | category/value 길이 일치 또는 explicit uniform numeric x grid, y bounds | 없음; 비균일 x는 왜곡 대신 실패 |
| Quadrant | 두 축 low/high label, 모든 point의 explicit `[0,1]` 좌표 | 없음 |
| Sankey | positive weighted DAG, 모든 node 참여, native-safe 고유 label | exact weight label을 가진 flowchart |
| Radar | 3개 이상 dimension, 동일 series 길이, 일관 bounds, non-negative domain | edge 없는 tabular flowchart |
| Treemap | hierarchy leaf마다 explicit positive value | internal-node value나 native runtime 실패 시 value-label hierarchy |
| Venn | explicit set/intersection과 모든 관측 size | size 누락 시 숫자를 합성하지 않는 set/intersection graph |

모든 native/fallback 대표 fixture는 Mermaid 11.16 strict `CandidateValidator`의 parse/render/SVG 검사를
통과합니다. Sankey grammar는 title/accTitle/accDescr를 표현하지 못하므로 해당 text를 typed IR과 warning에
남깁니다. Treemap/Venn의 experimental native grammar도 runtime type을 sidecar에 기록합니다.

pipeline의 numeric consistency는 source와 generated 숫자 multiset F1입니다. source OCR/vector에 없는
추가 숫자는 precision을 낮춥니다. Gantt/Pie/XY/Quadrant/Sankey/Radar/Treemap/Packet은 source numeric
evidence가 하나도 없으면 syntax/render가 성공해도 `U` 등급 review 대상으로 남습니다. Venn은 크기 없이도
set 구조 fallback을 정확히 표현할 수 있으므로 이 자동 numeric gate의 필수 type에는 포함하지 않습니다.
