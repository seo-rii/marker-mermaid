# Review Workspace

`marker-mermaid review <output-directory>`는 변환 sidecar를 수정 가능한 local workspace로 엽니다.
기본 주소는 `http://127.0.0.1:8765/`이며 원본과 최신 Mermaid render, provenance bbox, issue,
대안 후보를 한 화면에서 비교합니다.

```bash
marker-mermaid review output/document
marker-mermaid review output/document --port 9000 --no-open
marker-mermaid review output/document --host 0.0.0.0 --allowed-host review.internal --no-open
```

## 편집과 검증

Mermaid editor와 Scene IR JSON editor는 함께 저장됩니다. 저장 요청은 현재 revision의 version과
SHA-256을 포함하므로 다른 tab에서 먼저 수정했다면 `409 Conflict`가 나고 최신 내용을 다시 불러와야
합니다. Mermaid code는 자동 생성 후보와 같은 strict security scan, `mermaid.parse()`,
`mermaid.render()`, SVG 검사를 모두 통과해야 합니다. Scene IR도 bbox, 고유 ID, group/relation endpoint
참조를 포함한 `DiagramSceneIR` schema를 통과해야 합니다. 성공한 render의 SVG/PNG도 code와 같은
revision에 저장됩니다. provenance도 schema/고유 evidence ID를 검증한 뒤 content-addressed snapshot과
root manifest hash를 같은 revision에 저장합니다. Advisory layout hint도 source bbox와 분리된 closed
normalized schema와 content-addressed snapshot으로 revision됩니다. code, IR, provenance 또는 layout이
바뀐 revision은 원본 기반 점수를 자동 승계하지 않고
`unscored_user_revision`으로 표시합니다.

저장이 `422` 또는 다른 오류로 실패해도 두 editor의 local draft는 유지됩니다. `409`에서는
최신 server revision의 version과 digest를 다시 불러오되 local draft는 덮어쓰지 않으며,
충돌 draft의 저장은 잠급니다. 사용자가 `Reload latest`로 draft 폐기를 승인한 뒤의 다음 저장은
갱신된 version/digest를 사용합니다. `Reload latest`는 server를 다시 조회하고 ID가 일치하는 상세
응답을 받은 경우에만 local draft를 교체합니다. 충돌 refresh가 실패하면 draft와 저장 잠금을 유지합니다.
일반 dirty draft는 `Discard draft`로 현재 로드된 revision을 복원할 수 있습니다. 대안 선택,
승인·거절, undo/redo·restore, diagram 전환 등 editor 외부 작업은 dirty draft를 버리기
전에 사용자 확인을 요구합니다. 전환 중 늦게 도착한 이전 diagram 응답은 request generation과
diagram ID 검사로 무시합니다.

초기 bootstrap과 diagram 목록은 summary일 뿐 편집 기준으로 사용하지 않습니다. 상세 bundle을
성공적으로 읽기 전에는 editor, 명령, 후보 선택, 구조 변경, 승인·거절을 잠급니다. 상세 조회가
실패하면 `Retry load`로 다시 불러올 수 있으며, 성공한 응답을 받은 뒤에만 mutation이 활성화됩니다.

대안 후보 선택, 승인, 사유가 필수인 거절, undo/redo를 지원합니다. 승인은 저장 당시 성공 여부를
신뢰하지 않고 현재 code를 strict validator로 다시 parse/render하며 새 SVG/PNG를 같은 revision에
저장합니다. Validator가 없는 embedding에서는 승인 자체를 거부합니다. 최초 수정 시 원본 revision
`versions/r000000.*`을 만들며 각 변경은 `review-history.json`에 사용자 작업과 사유를 남깁니다.
자연어 patch는 generic edit로 축약하지 않고 `reverse_edge`, `relabel_node`, `group_nodes`의 target과
구조화된 before/after를 그대로 commit history에 보존합니다.
Undo/Redo 옆의 `Restore revision`은 서버가 검증한 active timeline ID만 표시합니다. 선택 시 현재 working
copy의 code, IR, SVG/PNG, provenance, layout, decision, candidate와 manifest hash를 그 snapshot으로
원자적으로 복원하고 `checkout_revision` audit를 append합니다. 이는 read-only preview가 아니며 version이
한 번 증가합니다. 임의 path, cursor index, active timeline에서 이미 분기 탈락한 snapshot은 요청할 수
없고, optimistic version/digest를 target membership 검사 전에 확인합니다.

restore/undo 뒤 새 편집을 하면 선택 cursor 이후 active timeline은 분기하지만 기존 immutable snapshot과
audit entry는 디스크에서 삭제하지 않습니다. UI는 restore 전에 이 동작을 명시적으로 경고합니다.
모든 자동 후보가 실패해 `final.mmd`가 없는 bundle도 첫 번째 bounded alternative를 편집 baseline으로
불러옵니다. 저장 전 validation은 동일하며, 최초 성공 편집 때만 `final.mmd`를 공개합니다.

## 자연어 patch

자연어 입력은 외부 모델이나 임의 코드 실행을 사용하지 않습니다. 명시적인 node ID만 받는 작은
한·영 command grammar를 사용하며 다음 작업을 지원합니다.

- `DB에서 API로 가는 화살표를 반대로 바꿔 줘.`
- `reverse edge DB -> API`
- `API 노드의 라벨을 결제 승인으로 바꿔 줘.`
- `relabel node API to Payment approval`
- `User, API, DB 노드를 하나의 subgraph로 묶어.`
- `group nodes User, API as Services`

공간/순번/대명사 참조처럼 원본 화면 해석이 필요한 명령은 부분 수정 없이 거절합니다. diagram type
변경도 대응 code를 안전하게 재생성할 정보가 부족하므로 editor에서 code와 IR을 명시적으로 함께
고치거나 해당 type의 대안 후보를 선택해야 합니다.

## 검증된 구조 연산

workspace의 `Validated structure operations`는 자유 형식 JSON 편집보다 작은 동기화 경계를
제공합니다. 원본 overlay에서 node bbox를 클릭하거나 ID select를 사용해 entity를 선택할 수 있습니다.
현재 지원 연산은 다음 여덟 가지입니다.

원본 image와 provenance SVG는 stage 전체가 아니라 image가 실제로 차지하는 shrink-to-fit canvas를
공유합니다. normalized Scene은 `0 0 1 1`, pixel Scene은 유효한 `canvas_size`, 크기가 없는 pixel
Scene만 현재 source URL과 일치하는 decoded image의 natural size를 viewBox로 사용합니다. SVG는 이
Scene canvas 전체를 source crop 전체에 affine mapping하며 별도 aspect-ratio letterbox를 만들지
않습니다. Evidence와 node bbox는 같은 Scene coordinate 계약을 따르고 canvas 밖 bbox는 클릭 가능한
대상으로 만들지 않습니다. source URL이 바뀌면 기존 image/overlay를 즉시 숨기고 새 image element와
request identity를 사용하므로 늦게 도착한 이전 load가 현재 bbox를 다시 표시할 수 없습니다. load
실패나 빈 source에서는 focus 가능한 overlay element도 제거합니다.

- `add_node`: safe ID/label, Scene canvas 안의 positive source bbox, evidence note를 요구합니다. 서버가
  revision 기반 `user_edit` evidence ID를 생성하고 node `evidence_ids`, provenance snapshot, quoted
  rectangle Mermaid declaration을 한 transaction으로 추가합니다. UI의 네 bbox field는 Mermaid render
  위치가 아니라 원본 overlay와 같은 Scene coordinate입니다.
- `reconnect_edge`: stable relation ID와 새 source/target node ID를 지정합니다. Scene IR relation과
  독립적인 Mermaid edge line이 정확히 1:1로 대응할 때만 connector를 보존하며 양쪽을 함께 바꿉니다.
- `add_edge`: 두 explicit node ID와 필수 evidence note만 받습니다. client는 relation/evidence ID, label,
  type, style, polyline, confidence를 정할 수 없습니다. 서버가 next revision 기반 ID와 bbox 없는
  `user_edit` evidence를 만들고 `user_edge`/`unknown`, unlabeled, arrow-at-target인 relation 및 plain
  `source --> target` line을 함께 추가합니다. self-loop, parallel ordered pair, implicit/duplicate endpoint
  declaration은 거절합니다.
- `delete_edge`: stable relation ID가 IR에서 유일하고 같은 ordered pair가 하나이며 정확히 하나의 plain
  Mermaid edge line과 대응할 때 line index와 relation을 함께 제거합니다. evidence는 undo에서 relation을
  다시 연결할 수 있도록 보존합니다. labeled/chained/non-plain edge와 `linkStyle`이 있는 code는 거절합니다.
- `delete_node`: quoted rectangle node declaration과 모든 incident relation/edge가 정확히 대응할 때
  node와 edge를 함께 삭제합니다. group/style/class/click/bare membership 같은 추가 참조, parallel edge,
  chained/labeled edge가 있으면 전체 연산을 거절합니다.
- `move_node`: 현재 Scene에 존재하는 stable node ID와 normalized center `[x, y]`를 받습니다. source
  `bbox`, Mermaid code, provenance는 바꾸지 않고 `layout-hints.json`만 revision합니다. 같은 code digest인
  layout-only revision도 version이 증가하므로 stale drag는 `409`로 거절됩니다.
- `group_nodes`: native multi-select에서 고른 두 개 이상의 ungrouped stable node ID와 필수 label을
  받습니다. 순서는 client 입력이 아니라 Scene element 순서로 canonicalize하고, 짧으면 읽을 수 있는
  `group_<members>`, 길면 같은 member 집합에 대해 결정적인 hash ID를 서버가 만듭니다. 각 member는
  정확히 하나의 quoted rectangle Mermaid declaration과 finite·ordered·in-canvas Scene bbox가 있어야
  합니다. 기존 Scene group과 bounded flat Mermaid subgraph가 ID/member 기준 1:1이고 모든 membership이
  disjoint일 때만 bbox union과 bare membership subgraph를 함께 추가합니다. nested/unbalanced subgraph,
  implicit/duplicate declaration, 기존 membership, client group ID, extra field는 거절합니다.
- `delete_group`: stable group ID만 받고 member node와 edge는 유지한 채 Scene group과 정확히 대응하는
  flat Mermaid `subgraph ... end` block 하나만 제거합니다. 전체 Scene group↔Mermaid membership과 grouped
  member declaration을 먼저 검증하고 parser가 확정한 header/end line slice만 삭제합니다. nested,
  unbalanced, duplicate/mismatched group과 block 밖 group ID reference는 거절합니다.

구조 연산 payload는 operation별 필수 field를 갖는 closed schema이며 알 수 없는 field도 거부합니다.
요청을 현재 IR에 해석하기 전에 version/digest를 확인하고, 저장 시 lock 안에서 다시 확인합니다. 성공한
결과는 full Scene schema와 strict Mermaid parse/render를 통과한 뒤에만 하나의 revision으로 저장되며,
실패하면 IR, code, render, history 중 어느 것도 바뀌지 않습니다.

edge add/delete 전에는 기존 Scene relation의 ordered endpoint multiset과 Mermaid의 지원되는 plain edge
multiset 전체가 1:1인지 확인합니다. `--o`, `--x`, bidirectional, labeled, chained 등 지원하지 않는 edge
syntax가 하나라도 있으면 일부만 추측해 편집하지 않습니다. add의 evidence note는 trim 후 1..4096자
single-line이며 source block mapping과 함께 provenance에 저장됩니다. add를 undo하면 relation, line,
evidence가 함께 사라지고 redo하면 같은 server ID로 복원됩니다. delete는 provenance와 layout을 바꾸지
않으며 add/delete 모두 source image, element bbox, group, 기존 relation을 그대로 둡니다.

Group form은 grouped option을 비활성화해 설명하고 선택 수를 live status로 알립니다. 두 node와 비어 있지
않은 label이 없으면 submit할 수 없습니다. 성공 시 Scene/Code와 audit만 바뀌며 source element bbox,
relation, provenance, source image, advisory layout hint는 그대로 유지됩니다. Mermaid subgraph는 renderer의
자동 배치를 바꿀 수 있지만, 이는 source geometry나 정확한 coordinate 편집을 뜻하지 않습니다.
Group 삭제 form은 stable ID·label·member count를 표시하고 node/edge 유지 사실을 confirm에 반복합니다.
삭제 성공 후에도 element/relation/provenance/layout/source는 그대로이며 undo가 같은 block과 group을
복원합니다.

오른쪽 advisory canvas는 stable ID로 만든 deterministic grid에서 시작하고 저장된 partial hint만
덮어씁니다. source bbox를 초기 배치로 재사용하지 않습니다. Pointer move는 browser preview만 갱신하고
pointerup에서 한 번만 commit하며, 실패하면 저장된 위치로 되돌립니다. 방향키로도 0.025 단위 이동할 수
있습니다. Mermaid flowchart 문법은 임의 고정 좌표를 표현하지 않으므로 hint가 `final.svg`의 정확한
위치를 바꾼다고 주장하지 않으며 자동 `layout_similarity` 점수에도 주입하지 않습니다. 대안 후보를
선택하면 hint를 clear하고, node 삭제/직접 Scene 편집은 남은 node ID와 교집합만 보존합니다. undo/redo는
layout artifact의 존재와 내용을 함께 복원합니다.

canvas의 relation 선을 선택하면 source/target endpoint handle이 나타납니다. handle을 다른 node에
놓으면 browser가 화면 CSS pixel 거리로 고정 반경 안의 유일한 최근접 node를 찾고, 기존
`reconnect_edge`에 relation ID와 양 endpoint node ID만 한 번 전달합니다. source handle은 target을,
target handle은 source를 그대로 보존합니다. 좌표, polyline, bbox, provenance, layout hint는 이 요청에
포함하지 않습니다. 동률, 반경 밖, 자기 loop, 기존 endpoint, 미이동, pointer cancel/capture 손실,
bundle·version·digest·relation endpoint 변경은 preview만 폐기합니다. select 기반 reconnect form은
키보드와 보조 기술용 경로로 계속 제공되며, 실제 저장은 어느 입력 방식이든 동일한 1:1 edge mapping,
strict render, optimistic concurrency, revision/audit transaction을 통과해야 합니다.

### Read-only visual difference

`Difference blend`는 현재 source와 revision의 validated `final.png`를 같은 viewport에 각각 aspect
ratio를 유지한 `contain + center` 방식으로 배치하고 CSS `difference` blend를 적용합니다. PNG가 없거나
source URL이 안전한 output image 경로가 아니면 control을 비활성화합니다. PNG IHDR의 각 축이 8,192를
넘거나 총 5천만 pixel을 넘는 경우에도 browser decode 전에 비활성화합니다. 기본값은 꺼짐이며 slider는
source layer의 강도만 10% 단위로 바꿉니다. 켠 경우에만 실제 표시할 source image와 digest-bound PNG
layer를 hidden 상태로 load하고 두 layer의 URL·decoded size를 모두 확인한 뒤 같은 요소를 reveal합니다.
bundle/revision이 바뀐 뒤 도착한 stale load event는 descriptor key가 다르면 버립니다. load 실패 시
토글을 끄고 live status로 원인을 알립니다. URL은 기존 same-origin static allowlist를 그대로 사용합니다.

`bounds-contain-center-v1`에서 viewport가 `(Vw, Vh)`, decoded image가 `(Iw, Ih)`이면 각 layer를
`scale = min(Vw/Iw, Vh/Ih)`로 독립 scaling하고, 남은 공간을 양쪽에 절반씩 두어 중앙 정렬합니다. 즉
display size는 `(Iw*scale, Ih*scale)`, offset은 `((Vw-Iw*scale)/2, (Vh-Ih*scale)/2)`입니다.

이 보기는 bounds-normalized 수동 검토 보조 기능입니다. crop, rotation, translation, feature registration,
semantic alignment 또는 pixel alignment를 수행하거나 주장하지 않습니다. 서버 artifact를 만들지 않고
quality score, 승인, revision, history, provenance, layout hint를 바꾸지 않습니다. 따라서 시각적 겹침은
자동 `EdgeAgreement`나 `LayoutSimilarity` 결과로 해석하면 안 됩니다.

## HTTP 및 파일 안전성

브라우저 mutation에는 page bootstrap에 포함된 CSRF token과 same-origin 요청이 필요합니다.
서버는 JSON body를 1 MB로 제한하고 bundle ID/path traversal 및 symlink artifact를 거부합니다.
허용 static artifact는 directory descriptor 기준 `O_NOFOLLOW`로 열고 열린 file descriptor를 그대로
stream하여 검사 후 symlink 교체 경쟁도 차단합니다. listener와 다른 `Host`도 bootstrap/API 처리 전에
거부하며 wildcard bind에서 추가 hostname은 `--allowed-host`로 정확히 지정해야 합니다. HTTP header를
완성하지 않는 connection은 기본 10초 뒤 종료되어 8개 worker slot을 반환합니다. validator render는 16 MB artifact budget을
넘으면 어떤 bundle 파일도 바꾸기 전에 실패합니다.
HTTP로 제공하는 파일은 `images/*`와 각 bundle의 `final.svg/png`뿐이며 review state, history,
immutable version 파일은 API 응답이나 static route로 직접 공개하지 않습니다.
Diagram 목록은 최대 1,000개 summary만 반환하고 최대 5,000개 bundle 후보만 상세 검증합니다. 목록 경로는
SVG/PNG, Scene IR, review history를 읽지 않으며 개별 bundle을 열 때만 전체 digest를 검증합니다.
undo/redo는 revision에 없던 optional Scene IR/SVG/PNG/provenance/layout도 실제로 삭제하고 manifest hash를 함께
정리합니다. 0.3 review timeline의 정적 provenance는 첫 mutation/undo에서 검증된 legacy digest로
고정하며 immutable 과거 snapshot을 재작성하지 않습니다.
review revision은 처리 중 I/O 오류에는 rollback하지만 여러 파일을 교체하므로 process/power loss까지
보장하는 crash-atomic transaction은 아닙니다. immutable revision directory와 단일 pointer swap은 후속입니다.

review server는 인증 시스템이 아닙니다. 기본 loopback bind를 권장합니다. non-loopback host로 열면
같은 네트워크의 사용자가 workspace를 볼 수 있으므로 별도의 인증 reverse proxy 없이 공용망에
노출하면 안 됩니다.

## 현재 제한

source-sized provenance/node overlay와 bounds-normalized read-only difference blend, active timeline
revision restore, JSON editor, source-anchored node 추가, advisory node drag-and-drop, ID 기반 edge
재연결/node 삭제, canvas endpoint drag는 제공합니다. Mermaid render의 실제 좌표 강제는 아직
구현하지 않았습니다. version history는 undo/redo timeline으로 제공하며
분기 탈락 snapshot preview와 VLM 기반 자유 형식 명령은 후속 범위입니다.
