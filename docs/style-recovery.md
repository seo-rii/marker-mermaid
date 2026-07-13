# Style recovery

Scene IR의 trusted PDF vector style evidence를 Mermaid에 반영하는 현재 범위는 Flowchart 계열의 node fill,
border color, dashed/thick border, vector-backed bold label, link stroke color/dashed/thick style과 trusted
vector container 기반 flat group fill/stroke/dashed/thick style입니다.
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

source→candidate node mapping은 content-consistent exact ID, trusted registry에 존재하는 evidence overlap,
punctuation을 보존하는 unique normalized label 순서로 시도하며 다중 match, normalized ID collision과
target collision은 fail closed 처리합니다. evidence index는 source×candidate 전수 비교 없이 bounded
lookup으로 사용하며 둘 이상의 generated node가 같은 evidence를 참조한 bucket은 candidate를 순회하지 않고
즉시 ambiguous 처리합니다. Direct Mermaid처럼 generated candidate Scene을 만들 수 없는 후보에는 source
style을 추측해 붙이지 않습니다.

Node fill/border/dashed/thick style은 실제 `VectorPrimitiveEngine`이 현재 source block에서 새로 등록한
collision-free contour와 source element bbox가 IoU 0.8 이상일 때만 그 vector element의 값을 사용합니다.
Scene/VLM이 선언한 색을 authority로 사용하지 않습니다. 같은 contour ID를 여러 source element가 참조하거나
다른 engine이 ID를 재사용하면 모두 생략합니다. Edge color/dashed/thick style도 같은 방식으로 새로 등록한
vector line만 허용합니다. Source relation의 양 endpoint bbox와 vector relation의 endpoint bbox가 각각 IoU
0.8 이상이고 source/trusted vector/generated Scene/Mermaid operator의 arrow flag, relation evidence ownership,
source→Mermaid endpoint mapping이 모두 일치해야 합니다.
Parallel endpoint pair, reused line evidence, non-pixel Scene과 불완전한 Mermaid edge ordering은 fail closed됩니다.
적용된 node/source relation, Mermaid link index, evidence ID와 match method는 `recover_style` history에 기록됩니다.

bold 출력에는 실제 `VectorPrimitiveEngine`이 새로 등록한 bold `vector_text` evidence가 필요합니다.
VLM/fixture가 같은 kind를 자칭하거나 기존 evidence ID와 충돌하면 신뢰하지 않습니다. cited bold span의
중심은 source node bbox 안에 있어야 하며, source label·candidate label·위치순 span text가 모두
일치해야 합니다.

Group style은 typed Flowchart/Swimlane이 방출한 generated `SceneGroup`과 source group의 mapped member set이
정확히 1:1일 때만 검토합니다. 실제 `VectorPrimitiveEngine`이 새로 등록한 styled contour bbox가 source
group bbox와 IoU 0.8 이상이고, 모든 member 중심을 포함하며 group 밖의 독립 node 중심을 포함하지 않아야
합니다. Fusion이 같은 node를 vector/VLM 두 element로 남긴 경우에는 member bbox와 IoU 0.8 이상인 geometry
duplicate만 제외합니다. Evidence ID collision, 다중 container match, normalized subgraph declaration 부재,
non-pixel source group은 fail closed됩니다. Portable ID 정규화 충돌은 exact member mapping으로 간주하지 않고,
member 자체의 evidence ID이거나 member bbox와 IoU 0.8 이상인 contour도 outer container로 승격하지 않습니다.
비교량은 group/member/node/vector 수로 계산한 결정적 budget을 넘으면 style matching을 생략합니다. 적용된
source/emitted group ID, contour evidence ID와 match method는 `recover_style` history에 기록됩니다.
VLM/fixture가 self-declare한 contour, line 또는 색은 어떤 node/group/edge style에도 권한이 없습니다.

기본 설정은 `portable-rich + strict`이므로 style evidence는 Scene IR에 보존되지만 자동 Markdown code는
바뀌지 않습니다. `style-rich + style-only`처럼 명시적으로 허용하면 `style`/`linkStyle`을 append하고
pre-validation repair history에 `recover_style` event를 남깁니다. 이후에도 security scan,
`mermaid.parse()`, render, SVG inspection을 반드시 거칩니다. 적용된 node style의 source/emitted ID,
evidence ID, match method는 `recover_style` repair history에 저장됩니다.

외부 Markdown consumer는 Mermaid version/theme에 따라 색과 선을 다르게 보일 수 있어 compatibility
warning을 기록합니다. normal weight를 강제로 지정하는 style, raster-only group/lane color와 chart series
color는 아직 공통 evidence와 안전한 style attribution이 연결되지 않은 후속 범위입니다. Vector container가
있는 flat Flowchart group과 Swimlane/BPMN lane은 위의 동일한 trusted group 경로를 사용할 수 있습니다.
