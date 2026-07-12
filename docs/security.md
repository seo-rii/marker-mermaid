# 보안 모델

## 위협 범위

Mermaid input은 신뢰하지 않습니다. LLM, OCR, fixture, 사용자 수정은 모두 동일한 검사 경로를
통과합니다. Mermaid 자체 `securityLevel: strict`는 제품 allowlist가 아닙니다. 외부 링크가
SVG에 남을 수 있으므로 다음의 중첩 방어를 사용합니다.

1. byte/후보 budget과 product source scanner
2. 네트워크가 차단된 Chromium context
3. `mermaid.parse()`
4. `mermaid.render()`
5. SVG XML 재검사
6. 게시 정책 평가

native C4는 Mermaid 11.16에서 parse/render되지만 생성 SVG가 undeclared `xlink` prefix를 사용한
`data:` image를 포함합니다. strict SVG/XML gate를 완화하지 않고 `architecture-beta` fallback을
사용합니다. 이 손실은 candidate warning과 fallback chain에 기록됩니다.

## strict에서 금지되는 입력

- `click`, callback, JavaScript
- `http:`, `https:`, `ftp:`, `file:`, `data:`, protocol-relative 외부 참조
- init/config directive (`%%{...}%%`)
- script, iframe, object, embed, link, style, img, svg HTML tag
- `@import`, 외부 CSS `url()`
- remote icon pack
- `style`, `classDef`, `linkStyle`

`style-only`은 마지막 세 Mermaid style statement만 허용하고 외부 URL/CSS는 계속 거부합니다.
`trusted-local`은 자동 Markdown에 게시할 수 없습니다.

Style recovery는 Scene IR 값을 그대로 CSS로 복사하지 않습니다. node와 vector-backed edge에는 hex와
제한된 named color, `stroke-width`, `stroke-dasharray`만 허용하고, vector-backed label 굵기는 상수
`font-weight:bold`만 허용합니다. 굵기는 registered bold `vector_text` evidence와 모호하지 않은
source→candidate mapping을 모두 요구합니다. evidence는 실제 vector engine origin, 고유 ID, source bbox
내부 span, source/candidate/span label 일치까지 재검사합니다. `strict` 또는 `portable-basic`
조합에서는 code를 바꾸지 않고 evidence를 IR에만 남깁니다. edge color/style은 모든 Mermaid edge의
순서를 정확히 mapping할 수 있을 때만 하나의 `linkStyle`로 출력합니다. 생성 style code도 동일한
source scanner와 SVG 검사를 통과해야 합니다.

대화형 review workspace도 저장 전 strict scanner와 parse/render/SVG 검사를 동일하게 적용합니다.
`trusted-local` callback 실행기는 제공하지 않으므로 `click`과 callback은 계속 거부됩니다.

## Review HTTP 경계

review server는 기본적으로 `127.0.0.1`에만 bind합니다. mutation API는 session별 CSRF token,
same-origin `Origin` 검사, JSON content type, 1 MB body limit, optimistic version/digest를 요구합니다.
요청 `Host`는 실제 listener와 일치해야 하므로 loopback DNS rebinding host에는 bootstrap도 제공하지 않습니다.
동시 HTTP request는 기본 8개 slot으로 제한하며 초과 요청은 thread를 만들지 않고 503을 반환합니다.
각 accepted socket은 기본 10초 timeout을 사용해 incomplete-header slowloris가 slot을 영구 점유하지
못합니다. Wildcard listener의 추가 hostname은 `--allowed-host` exact allowlist에 명시해야 합니다.
HTML은 inline script/style 없이 제공하며 CSP는 `script-src 'self'`, `connect-src 'self'`,
`frame-ancestors 'none'`을 적용합니다. artifact server는 원본 image와 `final.svg/png`만 공개하고
revision/state 파일은 HTTP로 노출하지 않습니다.
허용된 static 경로는 `openat`-style directory descriptor와 `O_NOFOLLOW`로 모든 path component를 열고
검사한 descriptor를 직접 stream하므로 symlink 및 check/use 교체를 거부합니다. validator가 만든 SVG/PNG는 저장 전에
각 16 MB로 제한하고, undo/redo는 optional IR/SVG/PNG의 생성과 삭제를 같은 rollback 경계에서 처리합니다.

이 서버에는 사용자 인증이 없습니다. `--host 0.0.0.0` 같은 non-loopback bind는 신뢰하는 격리망에서만
사용해야 하며 CLI가 경고를 출력합니다. CSRF token은 인증을 대신하지 않습니다.

승인은 이전 validation metadata를 재사용하지 않습니다. 현재 digest를 optimistic lock으로 확인한 뒤
같은 strict validator에서 code를 다시 parse/render하고, 성공한 새 render artifact와 승인 revision을
함께 기록합니다. Review validator를 구성하지 않은 API embedding은 승인할 수 없습니다.

구조 연산 API는 자연어 command와 분리된 closed discriminated schema를 사용합니다. 현재
source-anchored node 추가, edge 재연결, node 삭제와 advisory node 이동만 허용합니다. 기존 IR relation/node와 독립 Mermaid
line이 정확히 대응하지 않거나 group, style, chained/labeled edge 같은 추가 참조가 있으면 mutation 전에 거부합니다. optimistic revision은
operation을 IR에 해석하기 전과 실제 commit lock 안에서 모두 검사합니다. node 추가는 safe ID/label,
reason, positive bbox, explicit Scene canvas bounds를 요구하며 client가 evidence ID/kind/score/source를
정하지 못하게 서버가 revision 기반 `user_edit` evidence를 생성합니다. 이동 payload는 현재 Scene node
ID와 finite/non-boolean `0..1` center만 허용하며 bbox/style/URL/evidence field를 받을 수 없습니다.
`layout-hints.json`은 content-addressed revision과 manifest digest로 검증되지만 provenance 주장을 만들지
않습니다. source bbox를 Mermaid layout 좌표로 재사용하는 이동과 provenance 없는 자유 배치 추가는
제공하지 않습니다.

`group_nodes`는 client가 group ID, bbox, provenance, layout 또는 Mermaid fragment를 정하지 못하게
합니다. 서버가 Scene 순서의 unique safe node ID로 deterministic group ID와 exact bbox union을 만들며,
member bbox의 finite/non-boolean/order/canvas bounds를 확인합니다. 기존 Scene group과 bounded flat
Mermaid subgraph membership은 operation 전에 ID/member 기준 1:1이어야 합니다. nested/unbalanced
subgraph, 중복·겹침 membership, implicit/duplicate node declaration, group/node ID collision은 전체
transaction을 거절합니다. group label만 single-line length/escape 검사를 거쳐 quoted subgraph label이
되며 최종 code는 동일한 strict parse/render/SVG gate를 다시 통과합니다.

`add_edge`는 closed payload에서 source/target ID만 받고 top-level bounded evidence note를 요구합니다.
relation/evidence ID와 relation type·semantic·arrow·confidence는 서버가 next revision으로 고정하며,
label/style/polyline 입력은 허용하지 않습니다. 추가 전 기존 IR ordered endpoint multiset과 Mermaid plain
edge multiset 전체를 1:1 대조하고, non-plain/labeled/chained/bidirectional edge signal은 fail-closed로
거부합니다. endpoint node도 각각 하나의 quoted rectangle declaration이 있어야 합니다. 생성 evidence는
`user_edit`, `bbox=None`, note text, current source block IDs로 제한됩니다.

`delete_edge`도 같은 global mapping preflight 뒤 stable relation ID, unique ordered pair, unique plain line을
모두 확인하고 검증한 line index만 제거합니다. edge ordinal을 다른 style에 잘못 적용하지 않도록 comment와
quoted node label을 제외한 `linkStyle` token이 어디에 있어도 거부합니다. delete는 evidence를 지우지 않아
undo가 relation을 같은 provenance에 다시 연결할 수 있습니다. 두 operation 모두 strict render 실패나
stale optimistic lock에서 code/IR/render/history/provenance/layout 어느 파일도 commit하지 않습니다.

advisory canvas의 endpoint drag도 별도 mutation 권한을 만들지 않습니다. client는 화면 좌표로 유일한
최근접 node를 선택한 뒤 기존 `reconnect_edge` schema의 relation/source/target ID만 전송합니다. 좌표,
polyline, bbox, provenance, layout field는 payload에 없으며 server는 current revision의 stable ID와
Scene↔Mermaid 1:1 mapping을 다시 해석합니다. 반대 endpoint 보존과 self-loop/no-op 거부도 server에서
재검사되고, select form과 drag는 동일한 optimistic lock·strict render·atomic revision 경계를 씁니다.

Review의 read-only difference blend는 새 endpoint, canvas readback, pixel upload 또는 server artifact를
만들지 않습니다. 안전한 source URL과 실제 존재하는 current `final.png`에 대해서만 digest-bound
descriptor를 만들고, 기존 same-origin static allowlist를 사용하며 off-by-default입니다. 두 image는
독립적인 `contain + center`로 합성될 뿐 정렬·점수 근거가 되지 않습니다. PNG IHDR은 browser load 전에
각 축 8,192 및 5천만 pixel budget을 검사하며 기존 16 MB artifact budget도 그대로 적용됩니다. source와
render layer도 decoded bounds와 descriptor URL을 재확인하며 stale/error event는 현재 diff에 적용하지
않습니다.

Revision restore는 `r` 뒤 6자리 이상 숫자인 ID만 받고, optimistic version/code digest를 bundle lock
안에서 먼저 확인한 뒤 validated active timeline membership을 검사합니다. revision file path나 cursor
index, 분기 탈락 snapshot은 API로 받거나 노출하지 않습니다. 복원은 undo/redo와 같은 snapshot digest,
optional artifact 삭제, provenance/layout content digest, manifest hash, rollback 경계를 사용하며
`checkout_revision` user audit를 append합니다. History payload의 알 수 없는 field도 거부합니다.

review provenance/layout은 root artifact의 sidecar manifest hash와 `mmx-review-0.4.1` current digest를
검사합니다. 각 revision은 content-addressed provenance digest를 참조하며 code/IR/render와 같은
rollback 및 undo/redo 경계에서 root artifact와 manifest hash를 교체합니다. legacy 0.3 state는 기존
manifest hash가 있으면 먼저 검증한 뒤 정적 provenance digest를 고정해 lazy migration합니다. HTTP
editor는 provenance replacement switch를 노출하지 않으며 trusted structured operation만 명시적으로
교체할 수 있습니다. replacement를 포함한 모든 review commit은 Scene element/relation의
`evidence_ids`가 current provenance의 고유 ID 집합에 포함되는지도 검사합니다.

VLM/fixture JSON에는 Scene/IR 개수·깊이·문자·point·ID 상한과 finite-number 검사를 적용하며 sidecar
JSON은 비표준 `NaN`/Infinity를 허용하지 않습니다. Marker preview image도 dimension 8,192와 5천만
pixel 상한을 넘거나 Pillow decompression-bomb 판정이 나면 preview만 격리해 생략합니다.

## SVG 검사

단일 SVG root와 dimension/viewBox를 요구합니다. script 계열 element, event handler attribute,
외부 href, style attribute 및 `<style>` text의 외부 CSS를 거부합니다. strict profile은 `foreignObject`도 거부하며 runtime에서
`htmlLabels: false`를 강제합니다. fragment reference인 `href="#local-id"`만 허용합니다.

## Chromium 격리와 수명

Playwright context의 모든 network route를 abort합니다. remote font/icon을 등록하지 않습니다.
worker는 browser 하나를 재사용하지만 candidate마다 DOM을 초기화하고 deterministic ID seed를
사용합니다. worker stdout은 nonblocking JSONL buffer로 읽으며 응답 전체를 64 MB로 제한합니다.
newline이 오지 않는 partial 응답도 Python deadline을 넘기지 않습니다. timeout, malformed/oversized
response 또는 종료 시 기록한 worker process group에 SIGTERM을 보내고 제한 시간 후 SIGKILL합니다.
`bindFunctions`는 호출하지 않습니다.

`sandbox-experimental`은 config enum에는 예약되어 있지만 별도의 OS sandbox implementation은
아직 없습니다. 현재 runtime은 어떤 profile에서도 동일한 network isolation을 유지합니다.
