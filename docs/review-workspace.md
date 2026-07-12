# Review Workspace

`marker-mermaid review <output-directory>`는 변환 sidecar를 수정 가능한 local workspace로 엽니다.
기본 주소는 `http://127.0.0.1:8765/`이며 원본과 최신 Mermaid render, provenance bbox, issue,
대안 후보를 한 화면에서 비교합니다.

```bash
marker-mermaid review output/document
marker-mermaid review output/document --port 9000 --no-open
```

## 편집과 검증

Mermaid editor와 Scene IR JSON editor는 함께 저장됩니다. 저장 요청은 현재 revision의 version과
SHA-256을 포함하므로 다른 tab에서 먼저 수정했다면 `409 Conflict`가 나고 최신 내용을 다시 불러와야
합니다. Mermaid code는 자동 생성 후보와 같은 strict security scan, `mermaid.parse()`,
`mermaid.render()`, SVG 검사를 모두 통과해야 합니다. 성공한 render의 SVG/PNG도 code와 같은
revision에 저장됩니다.

대안 후보 선택, 승인, 사유가 필수인 거절, undo/redo를 지원합니다. 최초 수정 시 원본 revision
`versions/r000000.*`을 만들며 각 변경은 `review-history.json`에 사용자 작업과 사유를 남깁니다.
undo 뒤 새 편집을 하면 활성 timeline은 분기하지만 기존 snapshot과 audit entry는 삭제하지 않습니다.

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

## HTTP 및 파일 안전성

브라우저 mutation에는 page bootstrap에 포함된 CSRF token과 same-origin 요청이 필요합니다.
서버는 JSON body를 1 MB로 제한하고 bundle ID/path traversal 및 symlink artifact를 거부합니다.
listener와 다른 `Host`도 bootstrap/API 처리 전에 거부합니다. validator render는 16 MB artifact budget을
넘으면 어떤 bundle 파일도 바꾸기 전에 실패합니다.
HTTP로 제공하는 파일은 `images/*`와 각 bundle의 `final.svg/png`뿐이며 review state, history,
immutable version 파일은 API 응답이나 static route로 직접 공개하지 않습니다.
undo/redo는 revision에 없던 optional Scene IR/SVG/PNG도 실제로 삭제하고 manifest hash를 함께 정리합니다.
review revision은 처리 중 I/O 오류에는 rollback하지만 여러 파일을 교체하므로 process/power loss까지
보장하는 crash-atomic transaction은 아닙니다. immutable revision directory와 단일 pointer swap은 후속입니다.

review server는 인증 시스템이 아닙니다. 기본 loopback bind를 권장합니다. non-loopback host로 열면
같은 네트워크의 사용자가 workspace를 볼 수 있으므로 별도의 인증 reverse proxy 없이 공용망에
노출하면 안 됩니다.

## 현재 제한

provenance overlay와 JSON editor는 제공하지만 node drag-and-drop 및 edge를 canvas에서 직접
재연결하는 조작은 아직 구현하지 않았습니다. version history는 undo/redo timeline으로 제공하며
과거 revision을 임의 선택하는 UI와 VLM 기반 자유 형식 명령도 후속 범위입니다.
