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
우선 사용합니다. 이 Architecture grammar까지 runtime에서 거부되면 같은 candidate slot에서 nested
Flowchart를 한 번만 만들고 source security scan, parse/render, SVG, terminal runtime type을 모두 다시
검사합니다. C4뿐 아니라 Architecture/Deployment/Component도 같은 재검증 경계를 사용하며, fallback이
실패하면 해당 후보만 invalid로 유지합니다. 각 손실과 전환은 candidate warning, fallback chain과 repair
history에 기록되고 후보 budget이나 보안 allowlist를 늘리지 않습니다.

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

검증 runtime의 SVG와 PNG는 각각 UTF-8/byte 기준 16,000,000 bytes 이하만 허용합니다. SVG는 XML·외부
resource 검사를 통과해야 하고, PNG는 실제 PNG signature/format과 최대 한 변 8,192 pixels, 전체
50,000,000 pixels 이하인지 검사합니다. 잘못되거나 과대한 SVG는 render hard gate를 실패시키고, 선택적
PNG만 잘못된 경우에는 Mermaid/SVG 게시를 유지하면서 preview bytes를 버립니다. Review validator가 새로
반환하는 PNG에도 같은 검사를 적용하되 기존 bundle read 호환성은 별도로 유지합니다.

Validation/publication HMAC seal은 공개 model 생성자, JSON round-trip, 일반적인 사후 mutation이 검증을
가장하는 것을 막는 process-local capability입니다. 같은 Python process에서 실행되는 engine과 plugin은
trusted code로 간주하며, underscore/private API나 module memory를 읽을 수 있는 적대적 Python 코드를
sandbox하지는 않습니다. 신뢰하지 않는 extractor/plugin은 별도 OS process/container에 격리하고 검증된
IR 또는 image만 이 process로 전달해야 합니다.

Source scanner는 줄 시작뿐 아니라 세미콜론 뒤도 Mermaid statement 시작으로 해석합니다. `flowchart`와
`graph`에서는 실제 node 또는 bracketed subgraph label opener 바로 뒤의 큰따옴표만 LF/CRLF를 가로지르는
quoted-label 상태를 시작합니다. `class`, `direction`, Gantt title과 접근성 text에 나타난 임의 큰따옴표는
다음 statement를 숨기지 않습니다. apostrophe, backtick, backslash도 quote delimiter나 escape로 취급하지
않으므로 같은 줄에 이어 쓴 `click`, `style`, `classDef`, `linkStyle`은 각 profile의 동일한 규칙을
적용받습니다. 큰따옴표로 감싼 node label, `accTitle`/`accDescr` text, 줄 시작의 optional whitespace 뒤
`%%` comment 안의 세미콜론·keyword만 statement로 오인하지 않습니다.

Specialized typed serializer의 label에 위 token이 관찰돼도 active statement로 방출하지 않습니다.
keyword와 URL-like token 내부의 zero-width separator는 scanner와 parser 양쪽에서 동작을 비활성화하고,
Flowchart label의 source `&`도 entity로 재해석되지 않도록 같은 방식으로 분리합니다. Event Modeling edge의
`|`·`;`는 NFKC로 delimiter/statement가 되돌아오지 않는 `∣`·`⁏`로 표시합니다.
Quote·backslash·entity-like literal도 pinned Flowchart SVG가 실제 보존하는
`″`·`∖`·`＆`/`＃` glyph로 바꾸고 warning을 남깁니다. source가 제공한 control/format 문자와
line/paragraph separator는 공백 정규화 전에 거부하므로 invisible character를 이용해 검사 규칙을 우회할
수 없습니다. Packet,
TreeView, Ishikawa는 각 native grammar의 실제 SVG text 동작에 맞춘 encoder를 사용하며 보존할 수 없는
문자는 검증 가능한 Flowchart fallback과 compatibility warning으로 전환합니다. unsafe 접근성 원문은
typed IR/review metadata에만 유지하고 자동 SVG에는 generic 문구를 넣습니다. native/fallback label과
SVG title/description은 pinned Mermaid integration test로 검사합니다.

ZenUML Sequence fallback은 participant의 source ID를 예약어와 분리된
`zenuml_participant_*` namespace로 방출하고 message에는 이 namespaced endpoint만 씁니다.
Mermaid message에 ID 문법이 없으므로 `zenuml_message_*`는 Scene/provenance slot에만 부여합니다.
Alias/message text의
`#`·`;`·entity-like literal은 각각 `＃`·`⁏`·`＆`/`＃`로 표시하고,
active keyword·URL·callback·config token은 source 안에서만 invisible separator로
분리합니다. `accTitle`·`accDescr`도 active token·entity·`#` 규칙을 공유하지만,
Sequence accessibility SVG가 angle bracket을 double-escape하므로 `<`·`>`는 NFKC-stable
`〈`·`〉`로 표시합니다. 한 줄 accessibility grammar에서 text로 입증된 `;`는 원문 glyph를
유지합니다. 대체 사실은
compatibility warning으로 남습니다.

Wardley·Cynefin native serializer도 control/format/line separator를 정규화 전에 거부하고,
entity-like literal을 renderer가 손실시키는 자리에 보이는 `＆`/`＃` compatibility glyph를 사용합니다.
대체 사실은 warning에 남고 원문은 typed IR/sidecar에 보존됩니다. Strict nested 계약은
Wardley `x`/`y`에 boolean·NaN·infinity를, `anchor`에 integer/string coercion을 허용하지
않으며 Cynefin domain을 닫힌 official token 집합으로 검증합니다.
Organization·Data Lineage fallback은 raw ID를 Mermaid identifier로 직접 방출하지 않고
type 전용 namespace와 normalization-collision 검사를 사용합니다. label은 control/format/
lone-surrogate를 정규화 전에 거절하고 quote·backslash·entity-like literal, relation
delimiter와 edge-grammar `()[]{}@`, accessibility angle bracket를 pinned runtime에 보이는
compatibility glyph로 바꿉니다. Edge `@`는 `＠`로 표시하되 source에만 zero-width
separator를 더해 NFKC 후 `@import`도 비활성으로 유지합니다. Active keyword·URL
token은 source에서만 zero-width separator로 비활성화하며 원문은 typed IR/sidecar에
남습니다. Data Lineage relation은 resolved, non-self, non-duplicate endpoint만 Flowchart에
넘깁니다.

Railroad는 strict discriminated expression contract로 variant와 payload container를 먼저 제한하고,
공유 plan이 rule name 충돌, reference, depth와 record budget을 검사합니다. Native grammar가
ASCII angle bracket·모든 ASCII `#`·entity-like `&` prefix·NFKC quote/backslash hazard를 안전하게 보존하지
못하므로 각각 `〈`/`〉`, `＃`, `＆`, `″`/`∖` canonical visible glyph로 바꿉니다. Mermaid 전역
`encodeEntities`가 bare `#word;`와 `#35;`도 변형하므로 ASCII `#`에는 예외를 두지 않고 warning을 남깁니다. 원
semantic field는 typed IR/sidecar에 남깁니다. Rule/terminal/nonterminal/special과 접근성 text의 active
URL·directive·callback·HTML-like token 및 compatibility-normalized hazard에는 source-only zero-width
separator를 넣어 scanner와 parser에서 동작만 비활성화합니다. `style...:#...;`와
`classDef...:#...;` substring도 Mermaid preprocessor에서 statement로 승격되지 않도록 source에서만
분리합니다. Emitted source 원문과 NFKC-normalized source를 각각 strict scanner로 검사하며 어느 한쪽이라도
active rule이 남으면 후보를 거부합니다. Railroad identifier grammar에 안전하지 않은
scanner/preprocessor source-active name과 case-folded expression-word namespace, `railroad-beta`, case-folded lowercase
`title*` prefix는 logical `railroad_rule_*` ID와 분리된 collision-safe `rrmapped_N[_suffix]` native
name으로 mapping하고 visible change warning을 남깁니다. Logical `railroad_rule_*` ID는 Scene/provenance에만
유지하고 Scene/OCR은
실제 mapped `native_name =` text를 기록합니다. Normalized safe rule name은 그대로이며 allocator는 먼저
모든 safe native name을 reserve한 뒤 suffix로 충돌을 피합니다. Mapped rule의 source name도 typed
IR/sidecar와 nonterminal label에는 각각 raw/normalized 형태로 유지됩니다. 그 밖의 generated Scene/OCR
label은 separator 없는 canonical compatibility text를 정확히 사용하고 원문 AST는 typed IR/sidecar에
그대로 남습니다. Direct Scene projection도 `evidence_ids`가 null/생략 또는 string list가 아니면 fail
closed합니다.

Production은 raw와 NFKC-normalized emitted source에 strict source scan을 적용한 뒤 raw source만
CandidateValidator parse/render hard gate로 보냅니다. Pinned integration fixture의 NFKC parse/render는 bare
hash와 `style`/`classDef` substring이 grammar injection을 만들지 않는지 확인하는 안전성 probe이며,
normalized SVG가 raw SVG와 같은 compatibility glyph를 표시한다고 요구하지 않습니다. 같은 substring을
포함한 rule name도 source-active mapping 뒤에만 runtime에 전달합니다.

Wardley·Cynefin·Event Modeling·ZenUML·Organization·Data Lineage·Railroad serializer의 생성 source는
security scanner에 넘기기 전에도 50,000자·5,000줄을 넘으면 전체 후보 단위로 거부됩니다.

PDF vector provider도 신뢰하는 collection이 아닙니다. Vector source와 raw text/drawing,
PyMuPDF drawing command는 전체를 먼저 materialize하지 않고 각 상한보다 한 개만 더 읽어
초과를 판정합니다. Reconstruction 전체에 기본 256 source, 2,048 primitive/command
record, 5,000 text record, 8,000,000 text character를 공유하며 primitive·text 설정 최대의
합은 observation evidence 20,000개를 넘을 수 없고 primitive 상한은 Scene node 5,000개를
넘을 수 없습니다. 문자 상한도 공용 evidence 입력 상한보다 늘릴 수 없습니다.

예산은 validation 후의 유효 record나 deduplication 후의 결과가 아니라 원시 시도에서
소모됩니다. 따라서 malformed, crop 밖, duplicate record와 빈 nested drawing container도
유효한 결과처럼 작업 예산을 사용하며, count 또는 character dimension이 닫힌 뒤에는
나중 source가 그 예산을 다시 사용할 수 없습니다. Polygon은 256 point, polyline은 512
point를 넘으면 record 전체를 거부하여 잘린 geometry가 provenance로 남지 않게 합니다.
전체 보존 geometry는 100,000 point, kind·command·color·style 같은 non-label token은
각 256자로 제한합니다. Exact duplicate는 hash로 제거하고 approximate bbox dedup은 250,000회,
text ownership과 endpoint ownership은 각각 1,000,000회 비교 뒤 fail-closed warning으로
종료합니다. Warning도 observation당 256개로 제한합니다. Custom extractor 및 직접 주입된
`VectorObservation`은 자체 work metadata를 신뢰하지 않고 engine/Scene 경계에서 다시
bound·clamp됩니다. 이 검사는 Pydantic 최종 거부나 O(n²) deduplication 후에 의존하지 않고
외부 iterable을 전체 소비하거나 후단 검증에 넘기기 전에 적용됩니다. Duck-typed text span도
direct attribute와 `get_text("dict"/"words")` 모두 label을 한 번 읽어 plain snapshot으로 고정한
뒤 파싱과 `strip()` 전에 그 exact-string 길이를 raw character work에 합산합니다. Numeric scalar는
finite float로 안전하게 변환 가능한 exact `int`/`float`만 허용해 초대형 정수도 격리합니다.

Source mapping도 vector source별 linear scan 대신 built-in `observe()` 호출에서 한 번만 bounded
index로 고정합니다. Exact built-in placement list/tuple은 256개까지 받고 한 개 lookahead로
초과를 판정하며, 257번째가 있으면 일부 prefix를 쓰지 않고 index 전체를 invalid로 둡니다.
각 placement의 exact list/tuple `source_block_ids`도 256개까지 index하고 257개면 그
placement의 block/page+block key 전체를 생략합니다. Placement 자체와 유효한 page key는
all/page ambiguity에 계속 남아 partial block-ID authority나 허위 unique match를 만들지 않습니다.
Index는 exact-dict placement reference만 보존하고 build 중 affine/bbox를 파싱하지 않습니다.
따라서 malformed transform placement도 후보에서 미리 제외되지 않아 ambiguity를 없애지 않습니다.

Index는 all/page/block/page+block tuple을 생성하고 각 source에서 O(1) dictionary lookup만
수행합니다. Source의 explicit page ID가 있으면 해당 page를 먼저 고정하며 block key가
다른 page placement로 우회할 수 없습니다. Present-but-invalid page ID는 sole placement도
선택하지 않습니다. Placement block key는 exact bounded string만 사용하고, source identity는
exact bounded string/integer 또는 필드가 검증된 Marker `BlockId`에서 canonical string으로
만듭니다. 이 과정에서 arbitrary `str()`/hash/equality hook을 호출하지 않습니다. 조회 결과가
유일할 때만 선택 placement의 affine/bbox를 지연 파싱하고, 선택 affine이 invalid하면 bbox
fallback으로 fail closed합니다. 이 index는 공개 config/API 표면을 늘리지 않는 built-in 통합
내부 방어입니다.

단, 이 상한은 provider가 반환한 값의 소비부터 적용됩니다. Duck-typed property/callable,
custom extractor, `get_text()`/`get_drawings()` 호출 및 라이브러리 내부 materialization 자체에는
아직 wall-clock/RSS 격리가 없습니다. 따라서 provider 구현은 trusted local code여야 하며,
untrusted provider 실행에는 별도 worker process와 kill/reap 자원 제한이 필요합니다.

Style recovery는 Scene IR 값을 그대로 CSS로 복사하지 않습니다. Node와 edge는 exact built-in PDF vector
engine이 현재 source block에서 새로 등록한 collision-free contour/line과 bbox/endpoint ownership을
증명해야 하며 edge는 source/vector/generated/code 네 방향 표현도 일치해야 합니다. 그 trusted vector
값에만 hex와 제한된 named color, `stroke-width`,
`stroke-dasharray`를 허용하고, vector-backed label 굵기는 상수
`font-weight:bold`만 허용합니다. 굵기는 registered bold `vector_text` evidence와 모호하지 않은
source→candidate mapping을 모두 요구합니다. evidence는 실제 vector engine origin, 고유 ID, source bbox
내부 span, source/candidate/span label 일치까지 재검사합니다. VLM/fixture가 color나 vector evidence
kind/ID를 self-declare해도 style authority가 되지 않습니다. `strict` 또는 `portable-basic`
조합에서는 code를 바꾸지 않고 evidence를 IR에만 남깁니다. edge color/style은 모든 Mermaid edge의
순서를 정확히 mapping할 수 있을 때만 하나의 `linkStyle`로 출력합니다. 생성 style code도 동일한
source scanner와 SVG 검사를 통과해야 합니다.

Flowchart/Generic Network의 typed→fused node-ID mapping은 engine이 선언한 bbox/owner 문자열만으로
권한을 얻지 않습니다. 호출 전 evidence payload, trusted source image canvas와 block 집합, owner-local
geometry contour를 함께 검사하고 mapping record를 immutable하게 봉인합니다. Sidecar writer는 private
pipeline certification seal, claim digest, 현재 evidence schema, fused-node reference와 source block을
다시 확인합니다. 하나라도 어긋나면 임시 bundle을 지우고 `node-id-map.json`을 게시하지 않습니다.

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
source overlay는 이 same-origin allowlist URL을 새 image element에만 설정하고 pixel readback, object URL,
추가 canvas/fetch 경로를 만들지 않습니다. URL 변경 시 이전 element와 focusable bbox를 폐기하며 현재
request identity와 일치한 load만 Scene-coordinate overlay를 다시 표시할 수 있습니다.
허용된 static 경로는 `openat`-style directory descriptor와 `O_NOFOLLOW`로 모든 path component를 열고
검사한 descriptor를 직접 stream하므로 symlink 및 check/use 교체를 거부합니다. validator가 만든 SVG/PNG는 저장 전에
각 16 MB로 제한하고, undo/redo는 optional IR/SVG/PNG의 생성과 삭제를 같은 rollback 경계에서 처리합니다.

이 서버에는 사용자 인증이 없습니다. `--host 0.0.0.0` 같은 non-loopback bind는 신뢰하는 격리망에서만
사용해야 하며 CLI가 경고를 출력합니다. CSRF token은 인증을 대신하지 않습니다.

승인은 이전 validation metadata를 재사용하지 않습니다. 현재 digest를 optimistic lock으로 확인한 뒤
같은 strict validator에서 code를 다시 parse/render하고, 성공한 새 render artifact와 승인 revision을
함께 기록합니다. Review validator를 구성하지 않은 API embedding은 승인할 수 없습니다.

구조 연산 API는 자연어 command와 분리된 closed discriminated schema를 사용합니다. 현재
source-backed node label 선택·추가·삭제, edge 추가·재연결·label 설정·삭제, group 생성·삭제와 advisory
node 이동만 허용합니다. 기존 IR relation/node와 독립 Mermaid line이 정확히 대응하지 않거나 연산별
허용 범위 밖의 group, style, chained/labeled edge 같은 추가 참조가 있으면 mutation 전에 거부합니다.
optimistic revision은 operation을
IR에 해석하기 전과 실제 commit lock 안에서 모두 검사합니다. node 추가는 safe ID/label,
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

`set_edge_label`은 stable relation ID와 필수 `label`만 받는 closed payload입니다. label은 `null` 또는
control/format/surrogate/line-separator가 없는 200자 이하 non-empty single-line 문자열이며, 문자열은
quoted pipe wrapper 안에 넣습니다. literal `|`은 quote 안에서 보존하고, double quote와 backslash는
각각 visible compatibility glyph `″`(U+2033), `∖`(U+2216)으로 바꾸며 `&`, `<`, `>` 뒤에는
U+200B separator를 붙여 Mermaid entity/HTML syntax로 재해석되지 않게 합니다. 원문은 Scene IR과
audit history에 그대로 남습니다. 변환 뒤에도 source-wide scanner가 금지하는
external/protocol-relative URL, directive, callback, HTML, CSS import 또는 remote icon pattern이 남으면
label을 거절합니다. 서버는 모든 Scene relation과 독립 Mermaid edge의 ordered endpoint 및 canonical
label을 먼저 1:1 대조하고 parallel, chained, unsupported connector, label mismatch, ambiguous line을
fail-closed로 거부합니다. 성공 시 대상
relation의 `label`과 정확히 한 edge의 quoted `|"..."|` segment만 추가·교체·제거합니다. provenance와
`evidence_ids`는 그대로 보존하며 사용자 입력을 새로운 시각 근거로 만들지 않습니다. 같은 label은
`no_change`로 거절되고, optimistic lock, full Scene schema, strict parse/render/SVG, validated revision과
undo/redo gate는 다른 구조 연산과 같습니다.

`delete_group` payload는 stable `group_id` 하나만 허용합니다. 삭제 전 모든 Scene group의 safe ID,
group/node collision, disjoint existing members, exact bbox union과 모든 flat Mermaid subgraph의 ID/member
mapping 및 grouped member declaration count를 다시 검증합니다. bounded parser가 기록한 target header부터
matching `end`까지의 line slice만 제거하고, target block 밖에 같은 group ID token이 있으면 dangling
style/class/click/reference를 추측하지 않고 거절합니다. member element·relation·bbox와 provenance/layout,
source artifact는 수정하지 않으며 strict render와 optimistic revision transaction을 그대로 사용합니다.

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

provenance 기반 node relabel payload는 `node_id`와 `evidence_id`만 허용합니다. optimistic
version/digest를 확인한 current bundle에서 서버가 evidence text를 해석하므로 client는 label, evidence
kind/score/bbox 또는 provenance replacement를 주입할 수 없습니다. 선택 evidence는 고유한
`ocr_token`/`vector_text`이고 target node에 정확히 한 번, 다른 node에는 연결되지 않아야 합니다. text는
control/format/surrogate/line-separator가 없는 200자 이하 single-line이어야 하며, 기존 provenance와 node
`evidence_ids`를 수정하지 않은 채 target, before/after label과 선택 ID를 user audit에 남깁니다. 결과
code는 다른 edit와 같은 strict scanner, parse/render, SVG 검사와 commit-lock optimistic 재검사를
통과해야 합니다.

VLM/fixture JSON에는 Scene/IR 개수·깊이·문자·point·ID 상한과 finite-number 검사를 적용하며 sidecar
JSON은 비표준 `NaN`/Infinity를 허용하지 않습니다. Structured VLM은 mutable evidence를 canonical
model로 다시 검증합니다. canonical copy 전 evidence ID/text/source-block 문자의 합이 8,000,000을
넘는 입력은 거부합니다. Evidence와 OCR root container는 exact plain list만 허용하고 각각 한 번 만든
bounded snapshot을 이후 검사와 선택에 공통 사용합니다. 선택 대상으로 자른 OCR prefix도 plain `str`만
허용하고 문자열 합계 8,000,000자를 넘으면 escape scan 전에 거부합니다. OCR/evidence 전체를 먼저 JSON
직렬화하지 않으며, 설정된 item/문자 예산과 구조 quota 안에서 선택합니다. Prompt에 맞지 않는 OCR
string은 raw length lower bound를 먼저 검사해 큰 문자열을 JSON escape scan 없이 건너뜁니다. Marker 1.10.2의 canonical
response-schema text reserve까지 더한 고정 system/schema/view 영역이 prompt 상한을 넘으면 외부 provider
호출 전에 실패합니다. 선택 record는 완전한 JSON 단위로만 포함되고 최종 prompt도 UTF-8로 다시
검사합니다.

Post-construction mutation도 신뢰하지 않습니다. Evidence의 bbox/score/text/font/id를 exact type,
finite-number, field-size 계약으로 먼저 검사하고, nested `source_block_ids`는 허용 개수보다 하나만 더 읽는
bounded snapshot으로 고정합니다. 이 snapshot들로 새 canonical payload를 만들어 검증하므로 live evidence
model을 뒤에서 다시 dump하지 않습니다. Trusted connector/label ID도 exact `set`/`frozenset`에서 bounded
UTF-8 ID snapshot을 만든 뒤 그 immutable snapshot만 우선순위와 provenance 선택에 사용합니다.

Marker 1.10.2 stock Ollama가 nested `$defs`를 버리는 경로에는 local reference만 허용하는 bounded inline
schema adapter를 사용합니다. 외부·재귀 reference나 schema 상한 초과는 provider 호출 전에 실패하며,
Ollama 응답도 공통 canonical model 검증을 우회하지 않습니다.

VLM candidate의 게시 provenance 권한은 해당 호출 직전의 비충돌 evidence 중 실제 prompt에 선택된
ID에만 있습니다. 문자/item 예산으로 빠진 ID와 같은 응답이 새로 선언한 `vlm_observation`은 review
overlay와 sidecar evidence로 보존할 수 있지만, 원본 후보·fusion 후보·repair의 자동 게시 근거가 될 수
없습니다. 다른 trusted engine은 자기 호출에서 canonicalized된 evidence 권한을 유지하며, fusion typed
candidate는 선택된 원 owner의 닫힌 권한 집합과 독립적으로 인증된 ID mapping 근거만 이어받습니다.
중복 direct Mermaid candidate도 전체 fusion input 권한의 합집합이 아니라 실제 선택된 원 owner의 권한만
이어받으며, 명시적 빈 집합은 빈 상태로 유지됩니다.

Structured VLM view도 첫 `original`, portable name, RGB Pillow type, 개별/전체 pixel budget을 호출 전에
검사합니다. View dict는 전체를 materialize하지 않고 설정 상한보다 하나 많은 항목까지만 읽어 개수를
판정합니다. 호출자 소유 image를 검사 뒤 그대로 넘기지 않고, 독립된 plain `PIL.Image.Image` snapshot을
Pillow의 exact pixel core에서 복제하고 다시 크기 검증해 view manifest와 provider image list 양쪽에
사용합니다. 호출자 객체의 `size`/`mode` property와 `load`/`copy` hook은 이 복사 경로에서 실행하지
않습니다. Lazy ImageFile subclass는 호출 전에 이미 load되어 exact Pillow pixel core를 가져야 합니다.
Marker preview image는 별도로 dimension 8,192와 5천만 pixel 상한을 넘거나 Pillow decompression-bomb
판정이 나면 preview만 격리해 생략합니다.

Reconstruction 진입점도 engine adapter와 별개의 trust boundary입니다. Source block/page ID, OCR,
initial evidence, opaque source/vector object list는 exact plain list와 item/aggregate 문자 상한으로 먼저
snapshot합니다. 잘못되거나 초과한 collection은 prefix를 부분 신뢰하지 않고 collection 전체를 격리해
안전한 기본값과 source-context failure를 사용합니다. Initial·engine·fusion evidence는 reconstruction
전체 20,000-item/8,000,000-character cap을 공유하며, cap 뒤 evidence는 publication authority를 얻지
않습니다. 각 engine 호출 전 image/view/evidence/OCR/mapping/trusted-set snapshot을 다시 복원해 앞선
custom engine의 mutation을 다음 engine으로 전달하지 않습니다. Built-in fusion 후보 여부도
engine-controlled 이름 비교가 아니라 내부 pipeline 표식으로만 결정됩니다.

`source_mapping`은 exact built-in `dict`/`list`/`tuple`과 JSON scalar만 받는 iterative walker로
복사합니다. Depth 32, 25,000 items, field 50,000 characters, escaped compact JSON 4,000,000 bytes와
finite/safe numeric 범위를 적용하고 tuple은 list로 정규화합니다. Built-in container primitive만 사용해
subclass iteration/lookup/`deepcopy` hook을 실행하지 않으며 reference cycle도 거부합니다. Pipeline은
engine과 repair에 이 snapshot만 전달하고, sidecar writer는 JSON 직렬화·deep copy 전에 재검증한 뒤
before/live/snapshot canonical digest가 같을 때만 bundle을 publish합니다.

Typed IR도 같은 hook-free exact-built-in walker를 사용하되 depth 64, 100,000 items, field 50,000
characters, 누적 UTF-8 text 1,000,000 bytes, compact escaped JSON 4,000,000 bytes를 적용합니다. 하나의
observation은 모든 typed candidate를 합쳐 8,000,000 JSON bytes를 넘을 수 없습니다. Dict key, 반복 alias,
JSON escape와 structural separator를 모두 세고 tuple은 list로 정규화하며 cycle, subclass, non-finite 또는
safe range 밖 숫자를 직렬화 전에 거부합니다. Pipeline과 fusion은 mutated model을 dump하기 전 명시 field로
snapshot하고, accessibility/repair 결과도 재검증합니다. Sidecar는 selected와 alternative의 live IR을
안전한 shallow candidate에 교체한 뒤에만 전체 result를 deep-copy합니다. Candidate envelope는 3개 공개
field를 넘기면 `dict.copy` 전에 거부하며, fusion은 여러 observation을 합친 최종 후보에도 64개/8MB 전역
상한을 다시 적용해 한 입력 초과가 전체 fusion 실패로 번지지 않게 합니다. Envelope field name은 exact
built-in string인지 bounded copy에서 먼저 확인하며 Pydantic validation error는 원본 hostile input을 숨겨
오류 문자열 생성도 equality/repr hook을 실행하지 않습니다.
알려진 typed semantic record의 `evidence_ids`는 Scene과 공유하는 256-reference 상한을 prompt와 nested
post-validation에 모두 노출합니다. 생략·`null`·빈 목록은 호환을 위해 유지하되, 초과 목록은 fusion,
pipeline, accessibility/repair 및 sidecar sink의 현재-payload 재검증에서도 후보 단위로 격리됩니다.
Fusion은 Scene element/relation evidence 및 같은 VisualEvidence의 source-block 합집합을 257번째 고유
reference 전에 중단합니다. 같은 ID의 모든 입력을 원자적으로 판정하며, 초과 시 앞서 성공한 일부
합집합도 새 provenance로 게시하지 않고 cross-input enrichment를 버려 precedence winner record를
유지합니다. Vector text 결합도 새 SceneElement로 검증하며 초과한 label/font attribution 전체를
생략합니다. Pipeline은 내부 fused Scene/evidence record뿐 아니라 evidence의 exact plain list와 20,000개
전역 상한도 scoring 전에 다시 검증하므로 post-construction list mutation이 publication receipt와
sidecar 사이를 우회하지 못합니다.

## SVG 검사

runtime의 `render_valid` 보고만 신뢰하지 않으며 비어 있지 않은 문자열 SVG artifact를 함께 요구합니다.
누락·빈 문자열·공백뿐인 SVG는 사후 검사를 건너뛰지 않고 render 실패로 바꿉니다. 유효 artifact에는
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
