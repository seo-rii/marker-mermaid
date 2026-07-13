# 설정 레퍼런스

Marker JSON에서는 `MermaidDiagramProcessor_` prefix를 사용합니다. Python `MermaidConfig`에는
prefix 없는 이름을 직접 전달할 수 있습니다. prefix 설정이 concise 설정을 덮어씁니다.

## 모드 기본값

| 모드 | type 후보 | Mermaid 후보 | repair | direct Mermaid | style recovery |
| --- | ---: | ---: | ---: | --- | --- |
| `strict` | 1 | 1 | 1 | 꺼짐 | 꺼짐 |
| `extended` | 2 | 3 | 3 | 켜짐 | 켜짐 |
| `maximal` | 3 | 6 | 10 | 켜짐 | 켜짐 |

명시한 `candidate_count`, `type_candidate_count`, `max_repair_iterations`는 모드 기본값보다
우선합니다. 각각 12, 3, 10을 넘길 수 없습니다.
repair 횟수는 structured `RepairEngine` proposal 상한입니다. Marker 기본 processor와 fixture CLI는
evidence-backed Flowchart repair engine을 구성합니다. Label은 trusted Marker OCR 또는 exact built-in Vector
text가 source block/bbox와 일치할 때만 교정합니다. Marker processor에서는 built-in Geometry relation이
단독으로 지지하고 engine 간 방향 충돌이 없는 reversed edge와 무라벨 missing edge도 교정할 수 있습니다.
Fixture CLI는 JSON이 trust를 스스로 선언하지 못하도록 connector topology repair를 활성화하지 않으며 label
fixture도 trusted Marker/Vector provenance가 없으면 자동 교정하지 않습니다. Node/conditional/layout repair는
아직 기본 연결하지 않습니다.

## 게시 정책

| 정책 | parse/render 통과 후 동작 |
| --- | --- |
| `strict_validated` | aggregate와 semantic score가 모두 `review_below_score` 이상일 때만 게시 |
| `best_effort_validated` | aggregate와 semantic score가 모두 `publish_min_score` 이상인 A/B/C 게시 |
| `review_required` | Markdown에 넣지 않고 sidecar/review만 생성 |
| `sidecar_only` | Markdown에 넣지 않고 sidecar만 생성 |

`sidecar_only`에서 검증된 후보를 sidecar에 저장한 결과는 게시나 review 요청 없이 성공 상태로 기록합니다.

모든 정책에서 parse 또는 render 실패 결과는 게시할 수 없습니다. `trusted-local` 보안 profile은
`review_required` 또는 `sidecar_only`와만 조합할 수 있습니다.

## 주요 옵션

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `mode` | `extended` | 안전성/기능/budget preset |
| `publish_policy` | `best_effort_validated` | 자동 Markdown 게시 정책 |
| `enabled_types` | 전체 알려진 type | typed/direct 후보 allowlist |
| `publish_min_score` | `0.50` | best effort 최소 점수 |
| `review_below_score` | `0.70` | strict 최소 점수 및 review 경계 |
| `security_profile` | `strict` | Mermaid source allowlist |
| `compatibility_profile` | `portable-rich` | serializer style 호환성 목표 |
| `candidate_count` | 모드별 | source당 candidate 상한 |
| `type_candidate_count` | 모드별 | source당 type top-k |
| `max_repair_iterations` | 모드별 | 개선 후보 repair 상한 |
| `enable_fusion` | `true` | 여러 engine observation의 결정적 병합 |
| `enable_page_detector` | `true` | full-page coverage와 missed structural region proposal |
| `enable_style_recovery` | `true` | compatibility/security가 허용할 때 node/edge/trusted-vector-group style evidence 방출 |
| `runtime_dir` | cache 자동 탐색 | Node worker와 dependencies 위치 |
| `render_timeout_seconds` | `20` | candidate당 parse/render 제한 |
| `max_mermaid_chars` | `50000` | browser 전달 전 source 문자 상한 |
| `max_mermaid_lines` | `5000` | browser 전달 전 source line 상한 |
| `max_virtual_source_dimension` | `32768` | panel/merge canvas 한 변 상한 |
| `max_virtual_source_pixels` | `100000000` | panel/merge canvas pixel budget |
| `max_views` | `8` | VLM에 전달할 view 상한 |

`write_ir`, `write_svg`, `write_png`, `write_alternatives`, `write_provenance`는 각 sidecar
artifact 생성을 제어합니다. 선택된 `final.mmd`, `scores.json`, `review-history.json`, manifest는
bundle의 최소 감사 기록으로 항상 남습니다.

`include_original_image`와 `extract_images`는 타입 수준에서 `true`만 허용합니다. Marker 공통
`--disable_image_extraction`과 함께 사용할 수 없습니다.

Renderer의 `MermaidMarkdownRenderer_include_rendered_preview=true`는 validation runtime이 만든 PNG를
별도 `images/*--mermaid-preview.png`로 저장하고 원본 뒤에 삽입합니다. 기본값은 `false`이며 PNG가 없는
후보에 preview를 추정하거나 SVG를 임의 rasterize하지 않습니다.

## 구현 상태가 있는 옵션

edge map, Hough line, detected-arrow overlay, OCR/vector/contour overlay, grayscale,
adaptive threshold, color cluster, thumbnail, source-resolution tile,
GeometryEngine과 duck-typed VectorPrimitiveEngine이 구현되어 있습니다. vector engine은
`get_drawings()`, `get_text()`, `vector_primitives`, `vector_texts`를 노출하는 provider에서만 추출하며
Marker processor는 `marker` extra의 PyMuPDF로 실제 PDF page provider를 열어 source page→canvas mapping과
함께 전달합니다. provider를 열 수 없으면 block duck-typing으로 후퇴한 뒤 fail-closed empty observation을
반환합니다. page-level detector는 bounded edge/component
heuristic과 occupied-region exclusion을 사용하며 unanchored proposal은 PageGroup queue를 거쳐 sidecar로
보존하되 Markdown에는 자동 삽입하지 않습니다.
자세한 구분은 [스펙 대응표](spec-coverage.md)를 참고하세요.

State/Class/ER/Requirement/Block typed serializer와 C4/Deployment/Component/Use-case fallback은
`enabled_types` allowlist에 포함할 때 활성화됩니다. 요청 type과 실제 grammar가 다를 수 있으므로
[serializer 계약](serialization.md)의 emitted type과 fallback chain을 함께 확인해야 합니다.
Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn도 같은 allowlist와 계약을 사용합니다. 숫자 chart의 자동
게시에는 OCR 또는 vector numeric evidence와 최소 numeric consistency가 필요합니다. 구조 후보는 생성
node attribution을 계산할 수 없거나 80% 미만이면 자동 게시하지 않습니다.

`tile_size`는 64 이상이고 `tile_overlap`은 0 이상 `tile_size` 미만이어야 합니다. View slot은 큰
source의 tile 1~2개를 먼저 예약하고, 앞선 engine의 type top-k에 따라 유형별 priority를 적용합니다.
빈 OCR/arrow/contour/Hough overlay는 slot을 사용하지 않습니다.

기본 `strict` security profile에서는 `enable_style_recovery=true`여도 style statement를 만들지
않습니다. 실제 style code를 원하면 `portable-rich`/`style-rich` compatibility와 `style-only` 같은
비-strict security profile을 명시해야 하며 결과는 계속 parse/render/SVG hard gate를 거칩니다.
PDF label 굵기는 trusted vector span evidence의 ID가 충돌하지 않고 text/bbox가 generated Flowchart
node에 모호하지 않게 대응할 때만 상수 `font-weight:bold`로 복원됩니다.
