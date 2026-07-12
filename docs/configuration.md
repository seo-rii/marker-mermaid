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

## 게시 정책

| 정책 | parse/render 통과 후 동작 |
| --- | --- |
| `strict_validated` | aggregate가 `review_below_score` 이상일 때만 게시 |
| `best_effort_validated` | aggregate가 `publish_min_score` 이상인 A/B/C 게시 |
| `review_required` | Markdown에 넣지 않고 sidecar/review만 생성 |
| `sidecar_only` | Markdown에 넣지 않고 sidecar만 생성 |

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
| `runtime_dir` | cache 자동 탐색 | Node worker와 dependencies 위치 |
| `render_timeout_seconds` | `20` | candidate당 parse/render 제한 |
| `max_mermaid_chars` | `50000` | browser 전달 전 source 문자 상한 |
| `max_mermaid_lines` | `5000` | browser 전달 전 source line 상한 |
| `max_views` | `8` | VLM에 전달할 view 상한 |

`write_ir`, `write_svg`, `write_png`, `write_alternatives`, `write_provenance`는 각 sidecar
artifact 생성을 제어합니다. 선택된 `final.mmd`, `scores.json`, `review-history.json`, manifest는
bundle의 최소 감사 기록으로 항상 남습니다.

`include_original_image`와 `extract_images`는 타입 수준에서 `true`만 허용합니다. Marker 공통
`--disable_image_extraction`과 함께 사용할 수 없습니다.

## 구현 상태가 있는 옵션

edge map, Hough line, arrow overlay, OCR overlay, thumbnail, tile은 구현되어 있습니다. page-level
missed detector, composite split, fragment/multi-page merge, PDF vector primitive extraction은 모델과
설정 예약 상태이며 아직 실제 동작하지 않습니다. 자세한 구분은 [스펙 대응표](spec-coverage.md)를
참고하세요.
