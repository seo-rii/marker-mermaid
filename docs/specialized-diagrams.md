# 계획·특수 다이어그램 serializer

계획 및 특수 유형도 다른 typed serializer와 같은 `SerializationResult` 계약을 사용합니다. 입력에 없는
ID, 숫자, 좌표, 날짜, branch 또는 relation endpoint를 생성하지 않으며 native grammar가 strict
parse/render/SVG gate와 맞지 않으면 requested/emitted type과 손실 warning을 함께 기록합니다.

## 계획 유형

| 유형 | 출력 | 필수 evidence |
| --- | --- | --- |
| Journey | Timeline fallback | section/task, 1~5 정수 score, actor 목록 |
| Kanban | native `kanban`, runtime 거부 시 Flowchart | column/card ID, card의 명시적 `column_id` |
| GitGraph | native `gitGraph`, runtime 거부 시 Flowchart | `main` 초기 branch, 순서가 있는 commit/branch/merge, 전역 고유 commit ID |

Mermaid 11.16 Journey는 strict SVG 검사에서 금지하는 `foreignObject`를 생성합니다. 따라서 score와
actor를 Timeline event text로 보존하고 Journey scoring layout 손실을 warning으로 남깁니다.
Journey score는 별도 source OCR/vector 숫자와 일치해야 자동 게시되며 task ID가 중복되면 attribution을
조용히 합치지 않고 review로 보냅니다.
Timeline item delimiter와 renderer truncation을 피하기 위해 section/task/actor의 colon은 `∶`, entity-like
prefix는 `＆`/`＃` compatibility glyph로 표시하고 warning을 남깁니다. Journey title의 angle bracket도
`‹`/`›`로 표시하며 원문은 typed IR과 sidecar에 유지합니다.

Kanban serializer와 generated Scene은 하나의 bounded column/card plan을 사용합니다. Raw ID와 Mermaid
정규화 ID의 충돌, unknown `column_id`를 먼저 거부하고 모든 emitted ID에 예약어 안전 `kanban_` namespace를
적용합니다. Native와 Flowchart runtime fallback은 같은 emitted ID, label, containment를 사용합니다.
Native runtime 실패 시 새 후보를 만들지 않고 같은 candidate
slot에서 column/card node와 containment edge를 Flowchart로 한 번 재검증하며 lane/board layout 손실을
warning으로 기록합니다.
Native Kanban markdown label이 literal quote/backtick을 보존하지 못하면 `″`/`ˋ` glyph와 warning을
사용합니다. Portable Flowchart fallback이 literal quote/backslash를 보존하지 못하면 `″`/`∖`를 사용합니다.

GitGraph는 Mermaid가 자동 commit ID를 만들도록 두지 않으며 merge도 source/target과 merge commit ID가
모두 있어야 합니다. 공용 branch-head replay plan이 commit/merge node, parent relation, branch membership과
Flowchart fallback을 함께 결정합니다. `initial_branch`는 정규화 결과가 아니라 source 값 자체가 정확한
`main`이어야 하고, branch-before-commit, 같은 head merge, self merge, raw/normalized ID 충돌과 record budget
초과는 fail closed입니다. GitGraph commit ID는 Mermaid 인코딩 뒤 표시값도 고유해야 합니다. 세 planning
type의 2,000-record cap은 구조 탐색의 절대 상한이고, 모든 native/fallback source가 validator와 같은
50,000자·5,000줄 hard budget을 넘는지도 게시 전에 다시 확인합니다.

Canonical field와 compatibility alias가 함께 있으면 정규화한 의미가 같아야 합니다. Journey
`title/label`·`label/text`, Kanban `label/title`·`label/text`, GitGraph `name/id`·`commit_type/style` 충돌은
source evidence를 임의로 선택하지 않고 거부합니다. GitGraph의 commit/branch/merge별 known field 집합도
닫혀 있어 다른 operation에만 의미가 있는 field를 조용히 폐기하지 않습니다.

Pinned Mermaid 11.16 GitGraph는 일반 HTML numeric entity를 label에서 정확히 복원하지 않으므로 broad entity
encoder를 사용하지 않습니다. Quote와 backslash는 grammar quoting으로 원문 glyph를 보존하고 URL/directive,
callback, import, entity-like active token에는 보이지 않는 separator만 삽입합니다. `<`와 `>`는 native SVG가
원문을 보존하지 않아 `‹`와 `›`로 바꾸고 compatibility warning을 남깁니다. 이 규칙은 commit ID, tag,
accessible title/description에 동일하게 적용되며 실제 SVG text integration fixture로 검증합니다.

## 특수 유형

- Packet은 각 field의 integer `start`/`end`를 요구합니다. range overlap, 역순, 누락을 거부하고
  non-contiguous range는 gap 값을 만들지 않은 Flowchart fallback으로 보존합니다.
- Ishikawa와 TreeView는 hierarchy ID, cycle, 최대 depth를 검증합니다.
- Event Modeling은 lane/frame/relation을 확인한 뒤 lane-aware Flowchart로 출력합니다. pinned renderer가
  현재 native AST error를 반환하므로 native 성공으로 표시하지 않습니다. strict scanner가 금지하는
  keyword·URL-like token이 label 안에 있을 때는 실제 동작 문법이 되지 않도록 zero-width separator를
  삽입하되 화면의 단어와 문장부호를 보존합니다. source의 `&name;` 형태도 Mermaid HTML entity로
  재해석되지 않도록 `&` 뒤를 비활성 분리합니다. Flowchart edge label의 literal `|`만 grammar delimiter와
  NFKC 이후에도 구분되는 `∣`로 출력하고 semantic OCR에는 원문을 유지합니다. quote/backslash를
  `″`/`∖`로 바꿀 때는 Event Modeling도 다른 Flowchart fallback과 같은 compatibility warning을 남깁니다.
- Wardley는 각 component의 0~1 범위 `x`/`y`를 요구하며 누락 좌표를 배치 알고리즘으로 추정하지 않습니다.
- Cynefin은 다섯 공식 domain과 그 item, 명시적 domain transition만 허용합니다.
- Railroad는 terminal/nonterminal/sequence/choice/optional/repetition AST를 bounded recursion으로
  직렬화하고 모든 nonterminal reference가 rule에 존재하는지 검사합니다.
- ZenUML은 pinned runtime에 extension이 없어 Sequence fallback을 사용합니다.
- Organization과 Data Lineage는 각각 TreeView와 Flowchart fallback으로 hierarchy/endpoint를 보존합니다.
  Organization의 TreeView가 runtime validation에서 거절되면 같은 candidate slot에서 다시
  `organization → treeview → flowchart` chain으로 검증합니다.

Packet·TreeView·Ishikawa는 하나의 HTML entity encoder를 공유하지 않습니다. pinned native grammar별로
실제 SVG text를 보존하는 quoting을 적용하고, TreeView의 quote/backslash나 Ishikawa의 ampersand/angle처럼
native renderer가 원문 glyph를 보존하지 못하는 label은 명시적 Flowchart fallback으로 전환합니다.
Ishikawa raw-line label이 `ishikawa` 또는 `ishikawa-beta` 예약 헤더로 시작하면 화면 글자는 유지하면서
header token만 비활성 분리합니다.
Flowchart 자체가 literal quote/backslash를 보존하지 못하는 경우에는 `″`/`∖` compatibility glyph와 warning을
사용합니다. unsafe URL/HTML/control token이 접근성 문구에 들어오면 원문은 typed IR/review metadata에 남기고
자동 SVG에는 generic title/description과 warning을 넣습니다. fallback IR은 원본 type-specific root를 다시
전달하지 않아 접근성 파생 과정에서 unsafe label이 재유입되지 않습니다.

대표 native/fallback fixture는 pinned Mermaid 11.16에서 실제 strict security scan, parse, render, SVG
inspection을 통과하는 integration test로 고정합니다. Packet/Ishikawa/TreeView와 Treemap/Venn은 native
runtime rejection 뒤 같은 candidate slot에서 evidence-preserving portable fallback을 한 번 재검증합니다.
Kanban/GitGraph도 같은 방식으로 native rejection 뒤 공용 planning plan의 Flowchart를 한 번 재검증합니다.
Organization은 실제 TreeView runtime fixture와 simulated rejection→Flowchart pipeline fixture를 함께
고정합니다. Data Lineage의 별도 runtime fixture는 아직 이 범위에 포함되지 않습니다. experimental native도
validation hard gate를 우회하지 않습니다.
