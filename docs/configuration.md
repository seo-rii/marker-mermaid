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
기존 conditional edge의 label은 trusted OCR/vector text와 unique built-in Geometry connector가 각각 text와
방향·위치를 독립적으로 지지하고, source/typed edge가 같은 exact 방향으로 하나씩만 존재할 때 label-only로
교정합니다. Typed label이 비어 있거나 source label과 유사한 오타일 때만 허용하며 의미가 다른 기존 label은
덮어쓰지 않습니다.
Fixture CLI는 JSON이 trust를 스스로 선언하지 못하도록 connector topology repair를 활성화하지 않으며 label
fixture도 trusted Marker/Vector provenance가 없으면 자동 교정하지 않습니다. 누락 node, conditional topology,
endpoint·방향 변경, 새 branch와 Yes/No 의미 추론, parallel relation, layout repair는 아직 기본 연결하지
않습니다.

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
| `max_vlm_prompt_chars` | `100000` | provider-visible prompt와 Marker 1.10.2 response-schema reserve의 합산 상한 (`32768`~`1000000`) |
| `max_vlm_evidence_items` | `256` | prompt에 포함할 provenance evidence 상한 (`1`~`4096`) |
| `max_vlm_ocr_items` | `512` | prompt 후보로 검사하는 OCR text 상한 (`0`~`4096`) |
| `max_image_dimension` | `2048` | VLM original/overlay 한 변 상한 (`1`~`4096`) |
| `tile_size` | `1280` | source-resolution tile 한 변 (`64`~`4096`) |
| `max_virtual_source_dimension` | `32768` | panel/merge canvas 한 변 상한 |
| `max_virtual_source_pixels` | `100000000` | panel/merge canvas pixel budget |
| `max_views` | `8` | VLM에 전달할 view 상한 (`1`~`16`) |

`write_ir`, `write_svg`, `write_png`, `write_alternatives`, `write_provenance`는 각 sidecar
artifact 생성을 제어합니다. 선택된 `final.mmd`, `scores.json`, `review-history.json`, manifest는
bundle의 최소 감사 기록으로 항상 남습니다. 단, 선택 후보에 provenance-backed `node-id-map.json`이
있으면 dangling reference를 만들지 않도록 `write_provenance=false`여도 `provenance.json`을 함께
기록합니다. 자동 게시 bundle은 validation receipt를 독립적으로 검증할 수 있어야 하므로
`write_svg=false`보다 `final.svg` 보존이 우선합니다. `write_png=false`는 그대로 적용되며 이때 공개
generation receipt의 선택적 PNG digest는 validation-time audit 값으로 유지하고
`generation_artifact_presence.final.png=false`로 파일 부재를 명시합니다.

`include_original_image`와 `extract_images`는 타입 수준에서 `true`만 허용합니다. Marker 공통
`--disable_image_extraction`과 함께 사용할 수 없습니다.

Renderer의 `MermaidMarkdownRenderer_include_rendered_preview=true`는 validation runtime이 만든 PNG를
별도 `images/*--mermaid-preview.png`로 저장하고 원본 뒤에 삽입합니다. 기본값은 `false`이며 PNG가 없는
후보에 preview를 추정하거나 SVG를 임의 rasterize하지 않습니다. 현재 PNG bytes가 validation receipt의
digest와 다르면 Mermaid code는 게시하되 preview만 생략합니다.

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

Vector extraction 세부 예산은 현재 Marker JSON/환경 설정으로 노출되지 않습니다.
Custom integration은 `VectorPrimitiveEngine(max_primitives=..., max_texts=...,
max_text_chars=..., max_points=...)`로 hard validation 상한 안에서 조정할 수 있으며 새
`MermaidDiagramProcessor_*` key를 추측해 넘기면 안 됩니다. 생성자 기본값과 hard
validation은 다음과 같습니다.

| Vector engine 자원 | 생성자 기본값 | 확장 불가 상한 |
| --- | ---: | ---: |
| primitive/command raw work | 2,048 | 5,000 |
| vector text raw work | 5,000 | primitive+text 합 20,000 |
| vector text 문자 | 8,000,000 | 8,000,000 |
| vector source | 256 | 256 |
| polygon / polyline point | 256 / 512 | 256 / 512 |
| reconstruction 전체 보존 point | 100,000 | 100,000 |
| vector metadata token | 256자 | 256자 |
| approximate dedup 비교 | 250,000 | 250,000 |
| text ownership / endpoint 비교 | 1,000,000 / 1,000,000 | 동일 |
| observation warning | 256 | 256 |

예산은 source별 보존 output이 아니라 reconstruction-global raw work입니다. Malformed,
out-of-crop, deduplicated record와 빈 nested drawing container도 소모하며, count/문자 상한이
닫히면 뒤 source에서 그 dimension을 다시 열지 않습니다. Source/raw iterable은 최대 한 개의
lookahead만 사용하고 point 초과 geometry는 prefix로 자르지 않고 record 전체를 생략합니다.
Point가 없는 primitive는 전체 point budget 소진 뒤에도 record count 예산 안에서 처리됩니다.
비교 상한 뒤 label은 unassigned, connector는 unresolved로 보존하고 warning을 남깁니다.
Custom extractor output과 `VectorObservation.to_engine_observation()` 직접 입력도 같은 상한으로
다시 검사됩니다. 원시 작업량 계산과 fusion 경계는
[Vector extraction과 fusion](vector-fusion.md)에 정리합니다.

State/Class/ER/Requirement/Block typed serializer와 C4/Deployment/Component/Use-case fallback은
`enabled_types` allowlist에 포함할 때 활성화됩니다. 요청 type과 실제 grammar가 다를 수 있으므로
[serializer 계약](serialization.md)의 emitted type과 fallback chain을 함께 확인해야 합니다.
Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn도 같은 allowlist와 계약을 사용합니다. 숫자 chart의 자동
게시에는 OCR 또는 vector numeric evidence와 최소 numeric consistency가 필요합니다. 구조 후보는
`ocr_token`, `vector_text`, `contour`, `vlm_observation`, `user_edit`만 node 근거로 사용하고,
나머지 `source_crop`, `line_segment`, `arrowhead`는 node credit을 만들지 않습니다. 둘 이상의
generated node가 같은 eligible ID를 참조하면 그 ID를 모두에서 취소한 뒤 attribution을
계산합니다. 이 collision-free attribution을 계산할 수 없거나 80% 미만이면 자동 게시하지
않습니다. 이 규칙은 기존 설정과 스키마를 바꾸지 않습니다.

`tile_size`는 64 이상이고 `tile_overlap`은 0 이상 `tile_size` 미만이어야 합니다. View slot은 큰
source의 tile 1~2개를 먼저 예약하고, 앞선 engine의 type top-k에 따라 유형별 priority를 적용합니다.
빈 OCR/arrow/contour/Hough overlay는 slot을 사용하지 않습니다.

Structured VLM의 provider-visible prompt는 system instruction, 활성 type 계약, view/selection manifest,
prior evidence, OCR text와 Marker 1.10.2가 별도로 전달하는 canonical `EngineObservation` schema reserve를
합쳐 `max_vlm_prompt_chars` 안에 있어야 합니다. 고정 영역만으로 상한을 넘으면 provider를 호출하지
않습니다. 이 수치는 SDK 내부 wire encoding이나 임의 custom service가 덧붙이는 숨은 text까지 보장하지
않습니다.

Marker 1.10.2 stock Ollama service에는 `$defs`가 소실되지 않도록 bounded inline response schema를
자동으로 사용합니다. 다른 Marker service에는 원래 Pydantic schema class를 전달하며, 모든 응답은 같은
canonical `EngineObservation` 후검증을 거칩니다.

Evidence 선택은 user edit와 trusted connector를 먼저 보존하고, 남은 slot의 최소 25%를 arrowhead,
line, contour, vector text에 source 순서 round-robin으로 예약합니다. 남은 slot은 trusted label과 기존
전역 우선순위로 결정적으로 backfill하므로 다수 OCR이 뒤쪽 구조 근거를 모두 밀어내지 않습니다. 큰
record가 문자 예산에 맞지 않으면 JSON escape 길이를 allocation 없이 계산해 직렬화 전에 건너뛰고 다음
작은 record로 backfill합니다. 각 record와 OCR string은 완전한 compact JSON item으로만 넣습니다.
입력/검사/포함 수와 selection profile은 prompt manifest에 기록하고 candidate warning은 누락 개수를
요약합니다. 구조화된 item/character omission 원인과 전체 수치는 결과 최상위
`prompt_budget_notices`에 기록됩니다.

Canonical copy 전 evidence 문자열 합계와 `max_vlm_ocr_items`로 자른 OCR prefix 문자열 합계에는 각각
8,000,000자 hard cap이 있습니다. OCR은 exact plain string만 허용하며, 남은 prompt보다 raw JSON string
lower bound가 큰 항목은 escape scan 전에 건너뜁니다. Evidence nested source-block ID list와 trusted
label/connector ID set도 각 schema item 상한까지만 immutable snapshot으로 만들고, 그 snapshot만 canonical
validation과 selection에 사용합니다.

`max_image_dimension`과 `tile_size`의 상한은 4,096px입니다. View는 `original`이 첫 항목인 RGB Pillow
image여야 합니다. 이름, 개수, 한 변 4,096px, view당
16,777,216px, 전체 33,554,432px를 provider 호출 전에 검사합니다. 입력 dict는 `max_views + 1`개까지만
읽고, manifest와 image list는 같은 검증된
독립 plain-Pillow snapshot ordered list에서 만듭니다. 따라서 호출자 소유 image나 stateful Pillow
subclass를 검증 뒤 provider에 그대로 전달하지 않습니다. Caller의 property/load/copy hook은 실행하지
않으며 lazy ImageFile subclass는 호출 전에 load되어 있어야 합니다.

다음 값은 설정으로 늘릴 수 없는 reconstruction source hard cap입니다.

| 입력 | hard cap | 초과·비정규 입력 동작 |
| --- | ---: | --- |
| `source_block_ids`, `page_ids`, `source_blocks`, `vector_sources` | 각 256 items | 해당 collection 전체 격리 |
| initial/engine/fused evidence | reconstruction 전체 20,000 items | initial/engine collection 격리 또는 이후 evidence authority 차단 |
| source OCR | 50,000 items, 합계 1,000,000 chars | OCR collection 전체 격리 |
| evidence ID/text/source-block text | 합계 8,000,000 chars | evidence collection 전체 격리 또는 이후 evidence authority 차단 |
| typed IR candidate | envelope 3 fields, depth 64, 100,000 items, field 50,000 chars, UTF-8 text 1,000,000 bytes, compact JSON 4,000,000 bytes | 해당 candidate 격리 |
| observation/fused typed IR | 최대 64 candidates, compact JSON 합계 8,000,000 bytes | provider/fixture observation 거부 또는 fusion의 bounded prefix 유지 |
| `source_mapping` | depth 32, 25,000 items, string 50,000 chars, compact JSON 4,000,000 bytes | mapping만 `null`로 격리 |

위 `vector_sources` source-context 항목은 pipeline 경계에서 비정규/초과 collection을 전체
격리하는 규칙입니다. `VectorPrimitiveEngine`을 pipeline 밖에서 직접 주입했을 때의
추가 백스톱은 source iterable을 256개 prefix와 한 개 lookahead까지만 소비하고 warning을
남깁니다. 두 경계는 각각 caller container와 engine work을 방어하며 서로 대체하지 않습니다.

`source_mapping`은 exact `dict`/`list`/`tuple`과 JSON scalar만 허용합니다. Tuple은 JSON array로
정규화되고 key는 정렬되며, finite number와 JavaScript safe-integer 범위를 요구합니다. 이 snapshot은
engine, repair, 최종 result, sidecar에서 재사용·재검증되므로 container subclass의 iteration 또는
`deepcopy` hook을 실행하지 않습니다.

Typed IR hard cap도 설정으로 확장할 수 없습니다. Dict key와 string value를 출현 횟수대로 세며 tuple은
JSON array로 정규화합니다. 숫자는 finite JavaScript safe range여야 하고 cycle 또는 container/scalar
subclass는 거부합니다. Candidate의 `diagram_type`, `ir`, `confidence` 외 extra field는 unbounded copy 전에
거부합니다. Accessibility가 추가한 title/description과 semantic repair proposal도 같은 상한을 다시
통과해야 serializer와 sidecar로 이동합니다.

기본 `strict` security profile에서는 `enable_style_recovery=true`여도 style statement를 만들지
않습니다. 실제 style code를 원하면 `portable-rich`/`style-rich` compatibility와 `style-only` 같은
비-strict security profile을 명시해야 하며 결과는 계속 parse/render/SVG hard gate를 거칩니다.
PDF label 굵기는 trusted vector span evidence의 ID가 충돌하지 않고 text/bbox가 generated Flowchart
node에 모호하지 않게 대응할 때만 상수 `font-weight:bold`로 복원됩니다.
