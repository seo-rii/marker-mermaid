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

대화형 review workspace도 저장 전 strict scanner와 parse/render/SVG 검사를 동일하게 적용합니다.
`trusted-local` callback 실행기는 제공하지 않으므로 `click`과 callback은 계속 거부됩니다.

## Review HTTP 경계

review server는 기본적으로 `127.0.0.1`에만 bind합니다. mutation API는 session별 CSRF token,
same-origin `Origin` 검사, JSON content type, 1 MB body limit, optimistic version/digest를 요구합니다.
HTML은 inline script/style 없이 제공하며 CSP는 `script-src 'self'`, `connect-src 'self'`,
`frame-ancestors 'none'`을 적용합니다. artifact server는 원본 image와 `final.svg/png`만 공개하고
revision/state 파일은 HTTP로 노출하지 않습니다.

이 서버에는 사용자 인증이 없습니다. `--host 0.0.0.0` 같은 non-loopback bind는 신뢰하는 격리망에서만
사용해야 하며 CLI가 경고를 출력합니다. CSRF token은 인증을 대신하지 않습니다.

## SVG 검사

단일 SVG root와 dimension/viewBox를 요구합니다. script 계열 element, event handler attribute,
외부 href, 외부 CSS를 거부합니다. strict profile은 `foreignObject`도 거부하며 runtime에서
`htmlLabels: false`를 강제합니다. fragment reference인 `href="#local-id"`만 허용합니다.

## Chromium 격리와 수명

Playwright context의 모든 network route를 abort합니다. remote font/icon을 등록하지 않습니다.
worker는 browser 하나를 재사용하지만 candidate마다 DOM을 초기화하고 deterministic ID seed를
사용합니다. Python timeout 또는 종료 시 worker process group에 SIGTERM을 보내고 제한 시간 후
SIGKILL합니다. `bindFunctions`는 호출하지 않습니다.

`sandbox-experimental`은 config enum에는 예약되어 있지만 별도의 OS sandbox implementation은
아직 없습니다. 현재 runtime은 어떤 profile에서도 동일한 network isolation을 유지합니다.
