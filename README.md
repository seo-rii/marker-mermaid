# marker-mermaid

`marker-mermaid`는 Marker가 찾은 Figure, Picture, ComplexRegion을 근거 추적 가능한
Mermaid 후보로 복원하는 `marker-pdf 1.10.2` 확장입니다. 원본 이미지를 항상 보존하고,
보안 검사와 실제 Mermaid parse/render를 통과한 코드만 Markdown에 넣습니다.

이 저장소는 MMX-001 v0.3 Phase 1~5 유형의 serializer와 안전한 실행 경로를 제공하는
experimental engineering baseline입니다. Core/계획 serializer에 더해 State, Class, ER, Requirement, Block을 native 문법으로
생성하며 BPMN/Swimlane/C4/Deployment/Component/Use-case는 검증된 portable grammar로 명시적으로
fallback합니다. Pie, XY, Quadrant, Sankey, Radar, Treemap, Venn도 explicit numeric/set IR을
native 또는 loss-disclosed fallback으로 처리합니다. 대화형 workspace에서는 검증된 code/IR
편집, provenance overlay, 대안 선택, 제한된 자연어 patch, undo/redo, 승인/거절을 제공합니다.
Review canvas의 node drag는 source bbox를 덮어쓰지 않는 revisioned advisory layout hint로 저장되며,
edge endpoint drag는 화면상 node 선택만 수행한 뒤 기존의 검증된 reconnect transaction을 재사용합니다.
명시적 node group도 Scene↔Mermaid membership을 대조한 closed operation으로만 생성합니다.
edge 추가·삭제 역시 전체 Scene relation↔plain Mermaid edge mapping이 일치할 때만 revision됩니다.
edge label 추가·교체·제거도 stable relation ID와 전체 Scene↔Mermaid label mapping을 검증하며
provenance를 새로 만들지 않습니다.
Group 삭제는 member node/edge를 보존하면서 exact Scene↔subgraph block만 제거합니다.
유형별 추출 schema, 외부 평가 corpus, 일부 review 조작처럼 아직 구현하지 않은 v0.3 범위는
[스펙 대응표](docs/spec-coverage.md)에 숨김없이 구분했습니다.

## 핵심 보장

- 원본 이미지 보존은 끌 수 없습니다.
- 자동 게시 코드는 사전 보안 검사, `mermaid.parse()`, `mermaid.render()`, 사후 SVG 검사를
  모두 통과해야 합니다. runtime이 render 성공을 보고해도 비어 있지 않은 SVG artifact가 없으면
  render 실패로 처리합니다.
- 최종 Mermaid source, 검사된 SVG, 선택적 runtime PNG는 SHA-256 validation receipt로 결합되고,
  게시 정책·보안 프로필·결정 상태는 별도 process-private authorization seal로 고정됩니다. 검증 뒤
  source/SVG·품질 점수나 정책 결과가 바뀌면 Markdown과 게시 sidecar 경계가 fail-closed로 동작합니다.
  선택적 PNG가 바뀌면 Markdown code는 유지하되 preview를 생략하며, PNG 저장을 요청한 sidecar는
  거부합니다. Markdown/Marker는 동일한 봉인 snapshot에서 code와 preview를 만들고 sidecar는 원자적
  deep snapshot을 기록하므로 동시 변경으로 서로 다른 상태가 섞이지 않습니다.
- Process-private seal은 같은 process의 engine/plugin을 trusted code로 보는 무결성 경계입니다. 신뢰하지
  않는 Python plugin은 별도 process/container에서 실행해야 합니다.
- 후보 하나의 실패가 문서 전체를 실패시키지 않습니다.
- `extended` 기본 budget은 type 2개, candidate 3개, repair 3회입니다.
- 의미 점수가 없는 결과는 성공률을 부풀리지 않고 `U` 등급과 review 대상으로 둡니다.
- syntax/render 점수는 의미 점수를 희석할 수 없으며 게시 정책의 semantic threshold도 별도로 통과해야 합니다.
- `extended`의 자동 생성 node는 생성 결과 기준 collision-free provenance가 80% 미만이면
  review로 보냅니다. 하나의 node 근거를 둘 이상의 generated node가 재사용하면 그 근거는
  모두에서 모호한 것으로 간주해 coverage를 늘리지 않습니다.
- node/relation의 OCR, contour, VLM observation 등 provenance를 sidecar에 보존합니다.
- composite panel과 adjacent/continued multi-page fragment를 virtual source로 조립·출력합니다.
- panel/merge OCR bbox와 원 page/block을 잇는 affine provenance를 sidecar에 보존합니다.
- geometry contour/line/arrowhead evidence를 VLM보다 먼저 수집하며 engine별 후보를 공정하게 배분합니다.
- PDF vector/CV/OCR/VLM observation을 출처별 우선순위로 fusion하고 충돌 warning을 남깁니다.
- explicit flat/disjoint Flowchart group을 검증된 subgraph로 방출하고 lane/group membership을 SceneGroup sidecar에 보존합니다.
- exact member set과 bbox containment가 일치하는 trusted vector container만 group fill/stroke style로 복원합니다.
- node fill/border와 edge stroke도 collision-free built-in PDF vector contour/line 근거가 있을 때만 복원합니다.
- 등록된 PDF vector span 근거가 있는 Flowchart bold label을 안전한 상수 style로 복원합니다.
- trusted Marker/Vector text와 exact bbox/block, 또는 충돌 없는 built-in Geometry relation이 지지할 때만 Flowchart label·방향·무라벨 누락 edge를 복구합니다.
- 평가 가능한 경우 edge·arrow·layout·root-to-terminal path 구조 점수를 기록합니다.
- 렌더 런타임은 외부 네트워크를 차단하며 종료 시 Chromium process group을 정리합니다.

## 설치

Python 3.11+, Node 20+가 필요합니다. Marker 통합은 의도적으로 기준 버전에 고정됩니다.
Atomic sidecar publication은 Linux의 `renameat2(RENAME_NOREPLACE)`와 macOS의
`renameatx_np(RENAME_EXCL)`을 지원하며, 해당 no-replace primitive가 없는 OS에서는 기존 bundle을
덮어쓸 가능성이 있는 fallback 대신 sidecar 쓰기를 거부합니다.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[marker,vision,dev]'
marker-mermaid install-runtime
marker-mermaid doctor
```

Node 런타임은 `mermaid@11.16.0`, `playwright@1.61.1`과 해당 Chromium revision을 사용합니다.
기본 설치 위치는 `$XDG_CACHE_HOME/marker-mermaid/runtime`입니다. 개발 checkout에 이미
`node_modules`가 있거나 `MARKER_MERMAID_RUNTIME_DIR`을 설정하면 그 위치를 사용합니다.

## 빠른 사용법

PDF 변환은 Marker가 지원하는 LLM service를 그대로 사용합니다.

```bash
marker-mermaid convert input.pdf \
  --output output/document \
  --config examples/config.extended.json \
  --llm-service marker.services.gemini.GoogleGeminiService
```

네트워크 없이 파이프라인과 출력 형식을 재현하려면 이미지와 fixture observation을 사용합니다.

```bash
marker-mermaid reconstruct examples/flowchart.pbm \
  --fixture examples/flowchart-observation.json \
  --output output/fixture
```

Mermaid 파일만 검증할 수도 있습니다.

```bash
marker-mermaid validate diagram.mmd
```

생성 결과를 local workspace에서 검토하고 수정합니다.

```bash
marker-mermaid review output/document
```

고정된 MMX-001 corpus manifest를 집계하고 release gate 보고서를 만들 수 있습니다. Manifest와
모든 source/ground-truth/prediction artifact는 SHA-256으로 고정됩니다.

```bash
marker-mermaid evaluate corpus/manifest.json --output output/evaluation
```

종료 코드는 trusted-runner 입력의 전체 gate 통과 `0`, gate 실패 또는 필수 근거 부재 `1`,
잘못된 manifest/artifact `2`, 보고서 I/O 실패 `3`입니다. Corpus runner와 manifest 계약은
[Release evaluation](docs/evaluation.md)을 참고하세요.

수정 Mermaid는 strict security scan과 실제 parse/render를 다시 통과해야 저장됩니다. Scene IR,
SVG/PNG, 이력, immutable revision도 같은 commit으로 갱신됩니다. 자세한 사용법과 지원되는
자연어 명령은 [Review Workspace](docs/review-workspace.md)를 참고하세요.

## 출력

```text
output/document/
├── document.md
├── document_meta.json
├── images/
│   └── _page_4_Figure_2.jpeg
└── diagrams/
    └── page_4_figure_2/
        ├── manifest.json
        ├── final.mmd
        ├── final.svg             # 자동 게시 bundle은 필수, 그 외 설정에 따라 선택적
        ├── final.png             # runtime 생성 및 write_png 설정에 따라 선택적
        ├── scene-ir.json
        ├── generated-scene-ir.json # 생성 후보 구조(평가 대상, 가용한 경우)
        ├── typed-ir.json
        ├── node-id-map.json        # 안전한 typed→fused ID remap이 있는 경우
        ├── provenance.json
        ├── source-map.json
        ├── scores.json
        ├── review-history.json
        ├── review-state.json        # 첫 review 수정 후
        ├── layout-hints.json        # advisory node center (drag 후, 선택적)
        ├── versions/                # immutable review revisions
        └── alternatives/
```

자세한 스키마와 atomic write 규칙은 [출력 형식](docs/output-format.md)을 참고하세요.

## 문서

- [아키텍처와 처리 흐름](docs/architecture.md)
- [후보 영역 발견](docs/discovery.md)
- [Geometry engine](docs/geometry.md)
- [Type-aware visual priors](docs/visual-priors.md)
- [Page-level missed diagram detector](docs/page-detector.md)
- [Vector extraction과 fusion](docs/vector-fusion.md)
- [품질 평가와 점수 availability](docs/quality.md)
- [Release corpus와 MMX-001 gate](docs/evaluation.md)
- [접근성 title/description 생성](docs/accessibility.md)
- [Typed serializer와 fallback 계약](docs/serialization.md)
- [Typed extraction 계약과 평가 Scene](docs/typed-extraction.md)
- [차트 serializer와 숫자 안전성](docs/charts.md)
- [계획·특수 다이어그램 serializer](docs/specialized-diagrams.md)
- [결정적 source repair](docs/source-repair.md)
- [Evidence-backed semantic repair](docs/semantic-repair.md)
- [Style recovery](docs/style-recovery.md)
- [설정 레퍼런스](docs/configuration.md)
- [Marker 1.10.2 통합](docs/marker-integration.md)
- [보안 모델](docs/security.md)
- [출력 형식](docs/output-format.md)
- [Review Workspace](docs/review-workspace.md)
- [스펙 대응표와 로드맵](docs/spec-coverage.md)
- [개발 및 테스트](docs/development.md)
- [연구 배경](docs/references.md)
- [변경 이력](CHANGELOG.md)

Mermaid 브라우저 API는 공식 [usage 문서](https://mermaid.js.org/config/usage)와
[API interface](https://mermaid.js.org/config/setup/mermaid/interfaces/Mermaid.html)를 기준으로
사용합니다. Marker 통합은 설치된 1.10.2 실 API에 대한 compatibility test로 고정합니다.

Marker와 직접 통합되는 이 프로젝트는 `GPL-3.0-only`로 배포됩니다.
