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
| structured Marker VLM | 구현 | engine별 격리 source snapshot, enabled-type root contract와 Phase 1·stable State/Class/ER·모든 Phase 2 native/fallback·Phase 3 chart(Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn)·planning Journey/Kanban/GitGraph·special Packet/Ishikawa/TreeView/Wardley/Cynefin/Event Modeling/ZenUML/Organization/Data Lineage nested record 후검증/prompt contract, chart finite JSON number와 closed token, planning strict score/order와 GitGraph closed token, special strict bit range/effect leaf/recursive hierarchy/positioned component/official domain/frame type, C4 token/legacy `type`, Deployment/Component canonical relation/port, Use-case open relation label과 fixed Flowchart proxy 형, optional partial field/evidence와 원본 IR/extra metadata 보존, typed IR의 3-field envelope와 hook-free exact-built-in snapshot, candidate UTF-8 1 MB/escaped JSON 4 MB·observation 및 fused-output JSON 8 MB/64개 집계 예산, pipeline/fusion/accessibility/repair/sidecar sink 재검증, Marker 1.10.2 schema reserve와 stock Ollama inline-schema 호환을 포함한 request text/item/view budget, reconstruction-global evidence budget, structural quota/backfill, prompt-selected prior만의 게시 권한, durable budget notice; generic `ir` envelope와 Railroad nested schema는 후속 |
| Direct Mermaid | 구현 | extended/maximal, 동일 hard gate |
| Fusion engine | 구현 | source-explicit Scene consensus; prior payload의 bbox/text 정합 source evidence와 owner-local spatial contour, 최소 IoU 0.45, noncolliding provenance를 요구하는 flat Flowchart/Generic Network full/injective node-ID remap, atomic refusal, direction-conflict 전파와 hash-bound mapping/provenance sidecar. nested/non-flow remap은 후속 |
| Flowchart | 구현 | typed serializer, validated flat/disjoint group→subgraph emission과 SceneGroup round-trip + real render fixture |
| Architecture | 구현 | native·generated Scene·runtime Flowchart fallback 공용 bounded service/group/edge identity plan, `architecture-beta` 우선 serializer, runtime 거부 시 동일 candidate slot의 nested fallback, 양쪽 full-gate render fixture |
| Sequence | 구현 | typed serializer + real render fixture |
| Mindmap | 구현 | typed serializer; Mermaid 11.16 accessibility 제한 문서화 |
| Timeline/Gantt | 구현 | typed serializer + real render fixture |
| BPMN/Swimlane | 구현 | portable flowchart subgraph fallback |
| Phase 2 software types | 구현 | State/Class/ER/Requirement/Block native와 C4/Deployment/Component/Use-case fallback 모두 strict nested extraction contract 구현; C4와 Deployment/Component는 bounded record/Architecture plan의 emitted identity·group·unlabeled topology를 사용하고 runtime 거부 시 nested Flowchart로 재시도하며 특수 notation과 relation label은 typed IR에 보존, 진단용 native C4는 publication metric에서 분리; Use-case는 strict relation/endpoint plan을 serializer와 Scene이 공유하고 unsupported group을 억제하며 stadium actor와 round use-case를 구분하는 Flowchart 명시 fallback |
| Phase 3 charts | 구현 | Pie/XY/Quadrant는 strict nested extraction 뒤 native-only serializer를 사용하고 Scene adapter가 없음; Sankey/Radar/Treemap/Venn도 strict nested extraction 뒤 typed native/fallback을 사용하며 Sankey/Treemap/Venn은 generated Scene attribution을 제공하고 Radar는 sidecar provenance만 보존; 7개 유형 모두 별도 source OCR/vector numeric gate 적용 |
| Planning types | 구현 | 세 유형 strict nested extraction과 2,000-record cap; Journey→Timeline Scene/OCR projection 및 독립 score 숫자 gate, 공용 emitted-ID/topology plan을 쓰는 native Kanban/GitGraph와 generated Scene, runtime 거부 시 동일 candidate slot의 Flowchart fallback, GitGraph 실제 SVG text 보존 quoting |
| Phase 5 special types | 구현 | Packet/Ishikawa/TreeView strict nested extraction, native+runtime fallback, reserved-safe 공유 plan, generated Scene/provenance·source budget; Wardley/Cynefin strict nested extraction과 native/Scene/OCR 공유 plan, 실제 SVG text·source budget, Wardley `[y,x]`→normalized `(x,1-y)` layout·token 반올림·plain link, Cynefin 고정 runtime template·`confusion +N more`·review-only gate; Event Modeling/ZenUML strict nested extraction과 reserved-safe Flowchart/Sequence fallback·generated Scene/OCR 공유 plan; Organization/Data Lineage strict nested extraction과 reserved-safe TreeView/Flowchart fallback·generated Scene/OCR 공유 plan; Railroad native |
| Organization/Data Lineage | 구현 | canonical recursive hierarchy/dataset·process·relation 계약, frozen logical/emitted identity·visible-label·topology plan, record-local provenance, zero generated geometry, terminal-aware TreeView/Flowchart shape·arrow, grammar-safe lineage edge glyph, exact OCR projection, 500-record·50,000자·5,000줄 예산; Organization runtime rejection은 중첩 Flowchart fallback으로 재검증 |
| AST repair/mermaid-ast | 기반 | pre-validation bounded repair, event/history, AST adapter seam; mermaid-ast package adapter 후속 |
| style recovery | 기반 | trusted PDF vector origin 기반 Flowchart node/group fill·border, bold label, edge color/style와 attribution 구현; raster group/lane과 chart series 후속 |
| OCR recall | 구현 | bounded occurrence multisets, spatial/bbox-less dedup, structural/Gantt/Class/ER/Timeline/Journey/Kanban/GitGraph/Packet/Ishikawa/TreeView 및 emitted Architecture group·C4 fallback boundary/service·Requirement·Deployment/Component·Use-case·EventModeling·Wardley·Cynefin·ZenUML serializer-visible labels, Cynefin fixed template/confusion summary 포함, non-emitted metadata 배제, compatibility glyph의 실제 SVG text 투영, invalid/error/over-budget review gate |
| numeric consistency | 구현 | source에 존재하는 숫자만 비교; Journey score와 Packet bit range도 독립 source gate 적용 |
| edge agreement | 구현 | aligned topology F1, 불가 시 source/render edge IoU fallback |
| visual entailment | 기반 | 생성 node evidence coverage proxy와 게시 gate; model scorer 후속 |
| arrow/layout/path score | 구현 | explicit-arrow/path F1, relative layout; 근거 부족 시 unavailable |
| render-and-compare repair | 기반 | trusted text label, unique built-in Geometry connector와 결합한 existing conditional edge label-only, conflict-free reversed·unlabeled-missing edge repair, IR/code/resource 재검증과 공통 재평가; node/conditional topology·endpoint·direction·new branch·Yes-No inference·parallel/layout patch 후속 |
| publication validation receipt | 구현 | 최종 Mermaid/SVG/선택적 PNG digest와 policy/security/status 결정을 두 공개 receipt 및 process-local certificate/seal로 결합하고 Markdown·Marker·게시 sidecar 경계에서 재확인 |
| atomic sidecars | 구현 | preflight, manifest/hash/validation receipt/alternatives/provenance/source affine map |
| Review Workspace | 구현 | source-sized Scene-coordinate provenance/node overlay와 OCR/vector label 선택, bounds-normalized difference blend, 실패 bundle bootstrap, summary mutation lock, guarded draft/conflict reload, dirty-discard·stale-response 방어, 대안 선택, 승인/거절, active revision timeline과 canonical append-only log를 보존하는 newest-first 100개 audit view |
| code/IR/provenance revision | 구현 | strict code/Scene/evidence schema, content-addressed provenance, 0.3 lazy migration, rollback/undo/redo와 active timeline restore |
| NL patch | 구현 | 품질 invalidation, 명시 ID 기반 patch와 구조화 audit history |
| structured operations | 구현 | linked OCR/vector evidence 기반 node relabel, provenance-backed edge 추가, stable relation ID 기반 label 추가·교체·제거와 exact-ID edge 재연결·삭제, node 삭제, group 생성·삭제, screen-space endpoint drag와 accessible forms, global IR↔Mermaid node/edge/group 1:1 gate, pre-interpretation optimistic lock |
| source-anchored node add | 구현 | bounded canvas bbox, server-created user_edit evidence, code/IR/provenance transaction |
| layout drag-and-drop | 구현 | source bbox와 분리된 normalized advisory node move, screen-space edge endpoint snap, pointer/keyboard fallback, content-addressed revision과 undo/redo |
| release evaluation | 기반 | hash-bound manifest, micro metrics, hard/fixture/22-type/quality gate와 JSON/Markdown report; 대규모 corpus와 격리 runner 후속 |

## 릴리스 해석

현재 버전은 Phase 1~5 serializer를 제공하는 experimental engineering baseline이지 MMX-001의 모든
end-to-end 기능 gate를 달성한 production `extended` 릴리스가 아닙니다. 특히 나머지 special 유형의
Railroad nested contract,
generic `ir` envelope의 discriminated extraction schema, 아직 fallback
adapter가 없는 experimental grammar, 연구 데이터셋 규모의 실제 corpus와 trusted runner 측정이
필요합니다. precision/recall 목표와 유형별 최소 fixture 수량을 판정하는 고정 평가기는
제공합니다. 자동 게시 hard gate, 원본 보존, candidate failure
isolation, budget, sidecar/review 가능성은 현재 test 대상으로 삼습니다.
