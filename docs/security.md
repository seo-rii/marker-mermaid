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

Style recovery는 Scene IR 색을 그대로 CSS로 복사하지 않습니다. node와 vector-backed edge 모두 hex와
제한된 named color, `stroke-width`, `stroke-dasharray`만 허용하며 `strict` 또는 `portable-basic`
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

구조 연산 API는 자연어 command와 분리된 closed discriminated schema를 사용합니다. 현재 edge
재연결과 node 삭제만 허용하며, IR relation/node와 독립 Mermaid line이 정확히 대응하지 않거나 group,
style, chained/labeled edge 같은 추가 참조가 있으면 mutation 전에 거부합니다. optimistic revision은
operation을 IR에 해석하기 전과 실제 commit lock 안에서 모두 검사합니다. source bbox를 Mermaid layout
좌표로 재사용하는 이동 연산과 revisioned `user_edit` provenance가 없는 node 추가는 제공하지 않습니다.

review provenance는 root `provenance.json`의 sidecar manifest hash와 `mmx-review-0.4` current digest를
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
