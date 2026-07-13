# Style recovery

Scene IR의 vector/CV style evidence를 Mermaid에 반영하는 현재 범위는 Flowchart 계열의 node fill,
border color, dashed/thick border, vector-backed bold label과 link stroke color/dashed/thick style입니다.
PDF vector open path의 stroke color는 `SceneRelation.line_color`에 보존되고, relation endpoint가 Mermaid
edge 순서에 정확히 대응할 때 같은 `linkStyle` 선언으로 결합됩니다. 색은 `#RGB(A)`, `#RRGGBB(AA)`
또는 작은 named-color allowlist만 허용합니다. `url()`, 함수형 color, 임의 CSS property는 출력하지
않습니다.

PyMuPDF text span의 정수 `flags`에서 bold bit `16`만 해석합니다. 한 node 안에 배정된 모든 text span이
bold일 때만 `SceneElement.font_weight="bold"`가 되고, normal/unknown span이 섞이면 emphasis를
생략합니다. 동일 text+bbox의 중복 span이 서로 다른 weight를 주장하면 label은 한 번만 유지하고 weight를
버립니다. `font_weight` 모델은 `normal | bold` enum이므로 font family, size, 임의 weight/CSS를 전달할
수 없습니다.

Style statement를 code에 넣으려면 다음 조건을 모두 충족해야 합니다.

- `enable_style_recovery=true`
- compatibility profile이 `portable-rich`, `style-rich`, `trusted-local` 중 하나
- security profile이 `strict`가 아님
- emitted grammar가 Flowchart이고 source element가 generated candidate node에 모호하지 않게 대응
- style 대상 edge를 포함한 모든 Mermaid edge line의 순서를 독립 line으로 정확히 mapping 가능

source→candidate node mapping은 content-consistent exact ID, registry에 존재하는 evidence overlap,
punctuation을 보존하는 unique normalized label 순서로 시도하며 다중 match, normalized ID collision과
target collision은 fail closed 처리합니다. evidence index는 source×candidate 전수 비교 없이 bounded
lookup으로 사용합니다. Direct Mermaid처럼 generated candidate Scene을 만들 수 없는 후보에는 source
style을 추측해 붙이지 않습니다.

bold 출력에는 실제 `VectorPrimitiveEngine`이 새로 등록한 bold `vector_text` evidence가 필요합니다.
VLM/fixture가 같은 kind를 자칭하거나 기존 evidence ID와 충돌하면 신뢰하지 않습니다. cited bold span의
중심은 source node bbox 안에 있어야 하며, source label·candidate label·위치순 span text가 모두
일치해야 합니다.

기본 설정은 `portable-rich + strict`이므로 style evidence는 Scene IR에 보존되지만 자동 Markdown code는
바뀌지 않습니다. `style-rich + style-only`처럼 명시적으로 허용하면 `style`/`linkStyle`을 append하고
pre-validation repair history에 `recover_style` event를 남깁니다. 이후에도 security scan,
`mermaid.parse()`, render, SVG inspection을 반드시 거칩니다. 적용된 node style의 source/emitted ID,
evidence ID, match method는 `recover_style` repair history에 저장됩니다.

외부 Markdown consumer는 Mermaid version/theme에 따라 색과 선을 다르게 보일 수 있어 compatibility
warning을 기록합니다. normal weight를 강제로 지정하는 style, group background, lane/series color는 아직
공통 evidence와 안전한 style attribution이 연결되지 않은 후속 범위입니다. Typed Flowchart의 explicit
flat/disjoint group과 Swimlane/BPMN lane은 이미 검증된 subgraph 및 generated `SceneGroup`으로 round-trip되어
style target 구조는 준비됐습니다. 후속 group/lane color는 이 target에 trusted vector contour evidence를
1:1로 연결해야 하며, VLM이 self-declare한 색을 직접 사용하지 않습니다.
