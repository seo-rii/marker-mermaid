# Style recovery

Scene IR의 vector/CV style evidence를 Mermaid에 반영하는 현재 범위는 Flowchart 계열의 node fill,
border color, dashed/thick border와 dashed/thick link입니다. 색은 `#RGB(A)`, `#RRGGBB(AA)` 또는 작은
named-color allowlist만 허용합니다. `url()`, 함수형 color, 임의 CSS property는 출력하지 않습니다.

Style statement를 code에 넣으려면 다음 조건을 모두 충족해야 합니다.

- `enable_style_recovery=true`
- compatibility profile이 `portable-rich`, `style-rich`, `trusted-local` 중 하나
- security profile이 `strict`가 아님
- emitted grammar가 Flowchart이고 Scene ID가 실제 선언 ID에 모호하지 않게 대응

기본 설정은 `portable-rich + strict`이므로 style evidence는 Scene IR에 보존되지만 자동 Markdown code는
바뀌지 않습니다. `style-rich + style-only`처럼 명시적으로 허용하면 `style`/`linkStyle`을 append하고
pre-validation repair history에 `recover_style` event를 남깁니다. 이후에도 security scan,
`mermaid.parse()`, render, SVG inspection을 반드시 거칩니다.

외부 Markdown consumer는 Mermaid version/theme에 따라 색과 선을 다르게 보일 수 있어 compatibility
warning을 기록합니다. group background, font emphasis, lane/series color는 아직 IR-only이며 후속 범위입니다.
