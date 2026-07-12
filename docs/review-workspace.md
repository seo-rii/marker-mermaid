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

대안 후보 선택, 승인, 사유가 필수인 거절, undo/redo를 지원합니다. 승인은 저장 당시 성공 여부를
신뢰하지 않고 현재 code를 strict validator로 다시 parse/render하며 새 SVG/PNG를 같은 revision에
저장합니다. Validator가 없는 embedding에서는 승인 자체를 거부합니다. 최초 수정 시 원본 revision
`versions/r000000.*`을 만들며 각 변경은 `review-history.json`에 사용자 작업과 사유를 남깁니다.
자연어 patch는 generic edit로 축약하지 않고 `reverse_edge`, `relabel_node`, `group_nodes`의 target과
구조화된 before/after를 그대로 commit history에 보존합니다.
undo 뒤 새 편집을 하면 활성 timeline은 분기하지만 기존 snapshot과 audit entry는 삭제하지 않습니다.
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
현재 지원 연산은 다음 네 가지입니다.

- `add_node`: safe ID/label, Scene canvas 안의 positive source bbox, evidence note를 요구합니다. 서버가
  revision 기반 `user_edit` evidence ID를 생성하고 node `evidence_ids`, provenance snapshot, quoted
  rectangle Mermaid declaration을 한 transaction으로 추가합니다. UI의 네 bbox field는 Mermaid render
  위치가 아니라 원본 overlay와 같은 Scene coordinate입니다.
- `reconnect_edge`: stable relation ID와 새 source/target node ID를 지정합니다. Scene IR relation과
  독립적인 Mermaid edge line이 정확히 1:1로 대응할 때만 connector를 보존하며 양쪽을 함께 바꿉니다.
- `delete_node`: quoted rectangle node declaration과 모든 incident relation/edge가 정확히 대응할 때
  node와 edge를 함께 삭제합니다. group/style/class/click/bare membership 같은 추가 참조, parallel edge,
  chained/labeled edge가 있으면 전체 연산을 거절합니다.
- `move_node`: 현재 Scene에 존재하는 stable node ID와 normalized center `[x, y]`를 받습니다. source
  `bbox`, Mermaid code, provenance는 바꾸지 않고 `layout-hints.json`만 revision합니다. 같은 code digest인
  layout-only revision도 version이 증가하므로 stale drag는 `409`로 거절됩니다.

구조 연산 payload는 operation별 필수 field를 갖는 closed schema이며 알 수 없는 field도 거부합니다.
요청을 현재 IR에 해석하기 전에 version/digest를 확인하고, 저장 시 lock 안에서 다시 확인합니다. 성공한
결과는 full Scene schema와 strict Mermaid parse/render를 통과한 뒤에만 하나의 revision으로 저장되며,
실패하면 IR, code, render, history 중 어느 것도 바뀌지 않습니다.

오른쪽 advisory canvas는 stable ID로 만든 deterministic grid에서 시작하고 저장된 partial hint만
덮어씁니다. source bbox를 초기 배치로 재사용하지 않습니다. Pointer move는 browser preview만 갱신하고
pointerup에서 한 번만 commit하며, 실패하면 저장된 위치로 되돌립니다. 방향키로도 0.025 단위 이동할 수
있습니다. Mermaid flowchart 문법은 임의 고정 좌표를 표현하지 않으므로 hint가 `final.svg`의 정확한
위치를 바꾼다고 주장하지 않으며 자동 `layout_similarity` 점수에도 주입하지 않습니다. 대안 후보를
선택하면 hint를 clear하고, node 삭제/직접 Scene 편집은 남은 node ID와 교집합만 보존합니다. undo/redo는
layout artifact의 존재와 내용을 함께 복원합니다.

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

provenance/node overlay와 JSON editor, source-anchored node 추가, advisory node drag-and-drop,
ID 기반 edge 재연결/node 삭제는 제공합니다. Mermaid render의 실제 좌표 강제와 edge endpoint를
canvas에서 직접 끌어 놓는 조작은 아직 구현하지 않았습니다. version history는 undo/redo timeline으로 제공하며
과거 revision을 임의 선택하는 UI와 VLM 기반 자유 형식 명령도 후속 범위입니다.
