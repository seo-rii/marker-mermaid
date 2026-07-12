# MMX-001 v0.3 대응표

이 문서는 기술 스펙 v0.3을 구현 상태별로 구분합니다. `구현`은 test가 있는 실행 경로,
`기반`은 model/protocol/config가 있으나 전체 자동화가 없는 상태, `후속`은 아직 제공하지 않는
기능입니다.

| 스펙 영역 | 상태 | 현재 구현 |
| --- | --- | --- |
| strict/extended/maximal | 구현 | mode-derived top-k/candidate/repair budget |
| 4개 게시 정책 | 구현 | hard gate와 등급/threshold truth table |
| Figure/Picture/ComplexRegion | 구현 | Marker discovery와 원본 추출 |
| full-page candidate | 구현 | coverage/edge 판정과 Marker source 분류/출력 |
| page missed detector | 구현 | bounded proposal/crop, occupied exclusion, anchored Markdown 또는 unanchored page sidecar queue |
| composite split | 구현 | proposal, raw-fragment crop, virtual 원본/결과 출력 |
| fragment/multi-page merge | 구현 | caption/continued proposal, canvas assembly, first-fragment anchor 출력 |
| Scene IR/provenance | 구현 | Pydantic 무결성, 생성 node attribution, Extended 80% 게시 gate, sidecar |
| accessibility | 구현 | requested type 기반 title/description 파생, direct revalidation, 비지원 grammar warning/IR 보존 |
| type-aware visual priors | 구현 | edge/Hough/arrow/OCR/contour/threshold/grayscale, source-resolution tile과 view manifest |
| color cluster/vector primitive | 구현 | PIL color map, Marker PyMuPDF/duck-typed provider와 page→canvas affine |
| structured Marker VLM | 구현 | enabled-type root contract prompt/validation과 bounded response schema adapter |
| Direct Mermaid | 구현 | extended/maximal, 동일 hard gate |
| Fusion engine | 구현 | source-explicit precedence, spatial match, label/edge/type/provenance consensus |
| Flowchart | 구현 | typed serializer + real render fixture |
| Architecture | 구현 | `architecture-beta` serializer + real render fixture |
| Sequence | 구현 | typed serializer + real render fixture |
| Mindmap | 구현 | typed serializer; Mermaid 11.16 accessibility 제한 문서화 |
| Timeline/Gantt | 구현 | typed serializer + real render fixture |
| BPMN/Swimlane | 구현 | portable flowchart subgraph fallback |
| Phase 2 software types | 구현 | State/Class/ER/Requirement/Block native; C4/Deployment/Component/Use-case 명시 fallback |
| Phase 3 charts | 구현 | Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn typed native/fallback, 일부 runtime fallback과 numeric gate |
| Planning types | 구현 | Journey→Timeline fallback, native Kanban/GitGraph와 evidence-strict reference 검사 |
| Phase 5 special types | 구현 | Packet/Ishikawa/TreeView native+runtime fallback, Wardley/Cynefin/Railroad native, EventModeling/ZenUML fallback |
| Organization/Data Lineage | 구현 | TreeView/Flowchart portable fallback과 endpoint 검증 |
| AST repair/mermaid-ast | 기반 | pre-validation bounded repair, event/history, AST adapter seam; mermaid-ast package adapter 후속 |
| style recovery | 기반 | Flowchart node fill/border와 vector-backed edge color/style 구현; group/font/lane/chart series 후속 |
| OCR recall | 구현 | Unicode token coverage |
| numeric consistency | 구현 | source에 존재하는 숫자만 비교 |
| edge agreement | 구현 | aligned topology F1, 불가 시 source/render edge IoU fallback |
| visual entailment | 기반 | 생성 node evidence coverage proxy와 게시 gate; model scorer 후속 |
| arrow/layout/path score | 구현 | explicit-arrow/path F1, relative layout; 근거 부족 시 unavailable |
| render-and-compare repair | 기반 | 기본 evidence-backed Flowchart label repair와 공통 재평가; edge/direction/layout semantic patch 후속 |
| atomic sidecars | 구현 | preflight, manifest/hash/alternatives/provenance/source affine map |
| Review Workspace | 구현 | source/render/provenance/node 비교, 실패 bundle bootstrap, 대안 선택, 승인/거절, 이력 API |
| code/IR edit, NL patch | 구현 | strict code/Scene schema 재검증, 품질 invalidation, rollback revision, 명시 ID 기반 patch |
| structured operations | 구현 | exact-ID edge 재연결/node 삭제, IR↔Mermaid 1:1 gate, pre-interpretation optimistic lock |
| drag-and-drop/node add | 후속 | source bbox와 layout 분리, revisioned user-edit provenance 도입 후 제공 |

## 릴리스 해석

현재 버전은 Phase 1~5 serializer를 제공하는 experimental engineering baseline이지 MMX-001의 모든
end-to-end 기능 gate를 달성한 production `extended` 릴리스가 아닙니다. 특히 유형 내부 discriminated extraction schema,
아직 fallback adapter가 없는 experimental grammar, 연구 데이터셋 규모의
precision/recall 목표와 유형별 최소 fixture 수량은 별도 평가 corpus가 필요합니다. 자동 게시 hard gate, 원본 보존, candidate failure
isolation, budget, sidecar/review 가능성은 현재 test 대상으로 삼습니다.
