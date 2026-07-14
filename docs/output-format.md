# 출력 형식

## Markdown 불변조건

원본 image가 항상 먼저 나오고, 같은 source의 Mermaid fence는 최대 한 번만 삽입됩니다.
`syntax_valid && render_valid`가 아니거나 policy가 게시를 거부하거나 최종 source/SVG validation
receipt가 현재 artifact와 일치하지 않으면 image만 남습니다. B/C 등급은 warning을 동반하며 C 등급은
더 강한 `Experimental reconstruction` 문구를 사용합니다.

Pipeline은 validation 전에 source에 종단 LF가 없을 때만 하나를 추가합니다. 이후에는 trailing whitespace와
추가 newline을 제거하지 않으며, receipt의 `code_sha256`, `final.mmd`, Markdown fence 내부 payload가 같은
UTF-8 bytes를 가리킵니다. 종단 LF가 없는 독립 validator 입력은 parse/render할 수 있어도 자동 게시
certificate를 받지 못합니다. Fence delimiter는 source 안의 가장 긴 backtick run보다 길게 선택하므로,
multiline label 안에 물리적인 세-backtick 줄이 있어도 Markdown block을 조기에 닫을 수 없습니다.

한 Marker anchor에 virtual source가 있으면 original Mermaid, panel image/Mermaid, merged image/Mermaid
순서로 출력합니다. virtual source가 review/failed 상태여도 조립에 성공한 원본은 보존합니다.

## Sidecar bundle

각 source는 `diagrams/<safe-source-id>/`에 독립 bundle을 가집니다. writer는 같은 parent의 임시
directory에 모두 쓴 뒤 같은 directory descriptor를 기준으로 한 no-replace rename으로 최종 directory를
공개합니다. 최종 publication은 Linux의 `renameat2(RENAME_NOREPLACE)` 또는 macOS의
`renameatx_np(RENAME_EXCL)`을 사용해 검사 직후 생긴 destination도 덮어쓰지 않습니다. 안전한
no-replace primitive가 없는 runtime은 unsafe fallback 없이 게시 전에 실패합니다. 이미 존재하는
bundle은 자동 덮어쓰지 않습니다. path component는 allowlist로
정규화하며 absolute path와 `..`를 허용하지 않습니다. `diagrams`는 output root의 실제 direct
directory여야 하며 symlink는 거부합니다. 임시 directory 생성, 중첩 파일 쓰기, 최종 rename, 실패
cleanup은 모두 처음 연 `diagrams` descriptor와 `openat`/`mkdirat`/`unlinkat` 계열 연산에 고정됩니다.
따라서 검사 뒤 path나 symlink가 교체되어도 writer가 다른 tree를 열거나 제거하지 않습니다.

`manifest.json`의 `schema_version`은 `mmx-sidecar-0.5`입니다. `0.5`는 게시 후보의 최종
Mermaid source와 inspected SVG를 결합하는 `generation_validation_receipt`를 추가합니다. `0.4`부터 소스에서 관찰한
`scene-ir.json`과 선택 후보에서 다시 구성한 `generated-scene-ir.json`을 구분합니다. 전자는
OCR/CV/VLM 추출 근거이며 후자는 품질 평가 대상입니다. Direct Mermaid처럼 생성 구조를 안전하게
역추출할 수 없는 후보는 `generated-scene-ir.json`을 생략하므로 구조 품질을 평가 불가로 처리해야
합니다.

```json
{
  "schema_version": "mmx-sidecar-0.5",
  "source_id": "_page_4_Figure_2",
  "source_image": "images/_page_4_Figure_2.jpeg",
  "source_kind": "panel",
  "source_block_ids": ["/page/4/Figure/2"],
  "page_ids": [4],
  "anchor_block_id": "/page/4/Figure/2",
  "status": "success",
  "grade": "B",
  "publish": true,
  "review_required": false,
  "selected_candidate_id": "candidate-1",
  "requested_diagram_type": "c4",
  "emitted_diagram_type": "architecture",
  "runtime_diagram_type": "architecture",
  "fallback_chain": ["c4", "architecture"],
  "serialization_stability": "experimental",
  "generation_validation_receipt": {
    "schema_version": "1",
    "code_sha256": "sha256...",
    "svg_sha256": "sha256...",
    "png_sha256": "sha256...",
    "security_profile": "strict",
    "emitted_diagram_type": "architecture",
    "runtime_diagram_type": "architecture"
  },
  "generation_publication_receipt": {
    "schema_version": "1",
    "source_id": "_page_4_Figure_2",
    "selected_candidate_id": "candidate-1",
    "candidate_validation_sha256": "sha256...",
    "candidate_quality_sha256": "sha256...",
    "publish_policy": "best_effort_validated",
    "security_profile": "strict",
    "publish": true,
    "review_required": false,
    "status": "success",
    "grade": "B"
  },
  "generation_artifact_presence": {
    "final.mmd": true,
    "final.svg": true,
    "final.png": true
  },
  "files": {
    "final.mmd": "sha256...",
    "final.svg": "sha256...",
    "final.png": "sha256...",
    "scores.json": "sha256...",
    "review-history.json": "sha256...",
    "source-map.json": "sha256..."
  },
  "prompt_budget_notices": [
    {
      "engine": "marker_structured_vlm",
      "selection_profile": "structural-quota-v1",
      "prompt_chars": 72144,
      "max_prompt_chars": 100000,
      "schema_reserve_chars": 14753,
      "max_evidence_items": 256,
      "max_ocr_items": 512,
      "evidence_total": 380,
      "evidence_considered": 259,
      "evidence_included": 256,
      "ocr_total": 640,
      "ocr_considered": 512,
      "ocr_included": 498,
      "omission_reasons": [
        "evidence_item_limit",
        "evidence_char_limit",
        "ocr_item_limit",
        "ocr_char_limit"
      ],
      "selected_evidence_sha256": "sha256..."
    }
  ],
  "failures": []
}
```

`prompt_budget_notices`는 optional additive `0.5` 필드이며 Structured VLM 호출마다 adapter가 만든
bounded audit record입니다. provider response schema에는 이 필드와 선택 evidence ID 집합이 없으므로
응답이 notice나 게시 권한을 위조할 수 없습니다. 후보 생성 여부와 무관하게 남으며, `prompt_chars +
schema_reserve_chars <= max_prompt_chars`와 input/considered/included 수, item/character omission 원인을
기록합니다. bounded prompt를 완성한 뒤 provider 호출 또는 응답 정규 검증이 실패해도 같은 notice를
실패 결과와 sidecar에 남깁니다. 위 숫자는 출력 형식을 설명하는 예시이며 schema 직렬화 길이는 지원되는
Pydantic 환경에 따라 달라질 수 있습니다. `selected_evidence_sha256`은 정렬된 선택 ID 집합에 대한 opaque run
commitment이자 상관관계 식별자입니다. 선택 ID 집합은 process-private이므로 sidecar만으로 독립 검증할
수 있는 감사 digest나 게시 권한은 아니며, 실제 게시 권한도 process-private metadata로 유지됩니다.

`files`의 값은 content SHA-256입니다. `final.*`은 hard gate를 통과해 selected가 된 candidate에만
생성됩니다. 실패하거나 선택되지 않은 후보는 `alternatives/`에 JSON과 가능한 `.mmd`로 남습니다.
`generation_validation_receipt.code_sha256`과 `svg_sha256`은 bundle 생성 시점의 `final.mmd`와
`final.svg` exact UTF-8 artifact digest입니다. 공개 digest는 자동 생성 baseline의 audit 정보이며 그
자체가 게시 권한은 아닙니다. generation pipeline은 같은 receipt에 process-private HMAC seal을 붙이고
Markdown renderer와 sidecar writer가 이를 다시 확인합니다. `publish=true`인 결과가 seal을 잃었거나 두
artifact 중 하나가 변경되면 writer는 임시 bundle을 제거하고 원자적으로 실패합니다. 역직렬화한 결과를
다시 게시하려면 trusted validator로 source와 SVG를 새로 생성·검사해야 합니다. Review가 시작되면 이
receipt는 immutable `r000000` 자동 생성 baseline을 설명하고, 현재 revision은 `manifest.files`와
`review-state.json`의 content digest가 별도로 추적합니다.
ReviewStore의 상세 load는 `0.5` bundle의 두 generation receipt와
`generation_artifact_presence`를 첫 mutation 전에는 root `final.*`, 이후에는 immutable
`versions/r000000.*` baseline 및 `scores.json`에 대조합니다. 현재 revision은 별도로
`review-state.json`과 `manifest.files`의 content digest로 검증합니다. 요약 목록은 code 중심의 경량
검사만 수행합니다. 자동 생성 baseline과 사용자 편집 revision을 섞어 receipt를 재해석하지 않으며,
기존 sidecar schema의 미검사 PNG를 새 artifact처럼 재승인하지 않으면서 read 호환성은 유지합니다.
선택적 `png_sha256`은 validation runtime이 PNG를 만들었다면 유지되고,
`generation_artifact_presence`가 해당 bytes의 bundle 포함 여부를 별도로 표시합니다. 따라서
`write_png=false`는 `final.png`만 생략하며 publication receipt가 참조하는 validation receipt 자체를
변형하지 않습니다. 반면 자동 게시 hard gate의 근거인 SVG는 `write_svg=false`여도 `final.svg`로 강제
보존하여 receipt를 bundle 안에서 독립적으로 검증할 수 있게 합니다. nonautomatic bundle이 SVG를
생략하면 서로 참조하는 두 generation receipt도 함께 생략하여 orphan reference를 만들지 않습니다.
`generation_publication_receipt`는 같은 baseline의 policy/status/review 결정을 고정하므로
`review_required`, `sidecar_only`, `trusted-local` 결과의 flag만 바꿔 자동 게시할 수 없습니다.
두 candidate digest의 canonical encoding은 다음과 같습니다.

- `candidate_validation_sha256`: `generation_validation_receipt`를 JSON-mode enum 값으로 변환하고,
  `ensure_ascii=false`, key 정렬, 공백 없는 `,`/`:` separator로 JSON 직렬화한 UTF-8 bytes의 SHA-256
- `candidate_quality_sha256`: `scores.json`의 `aggregate_score`, `grade`, `metrics`, `warnings`를
  projection하되 aggregate와 각 metric을 exponent 없는 decimal string으로 바꿉니다. 소수부 끝의 0과
  decimal point를 제거하고 `-0`은 `"0"`으로 정규화하며, 평가 불가 aggregate의 `null`은 유지합니다.
  Metric key는 lowercase ASCII `[a-z][a-z0-9_]*`만 허용해 언어별 Unicode key ordering 차이를 없앱니다.
  그 객체를 같은 canonical JSON 규칙으로 직렬화한 UTF-8 bytes의 SHA-256입니다.

예를 들어 `aggregate_score=-0.0`, `metrics={"tiny": 1e-7, "zero": -0.0}`, grade C, 빈 warning은
`{"aggregate_score":"0","grade":"C","metrics":{"tiny":"0.0000001","zero":"0"},"warnings":[]}`
으로 encode되고 SHA-256은
`ee36d80539010204f914e727bf574ddd015272566ff6981b57a377d86d2d09a5`입니다. NaN과 infinity는
canonical input으로 허용하지 않습니다. 이 규칙은 pretty-printed sidecar file의 content hash와
구분됩니다. 자동 게시 writer는 경로를 계산하기 전에 `ReconstructionResult` 전체를 한 번 깊은 복사하고,
복사 전후 publication core와 private seal의 연속성을 확인한 다음 그 snapshot의 receipt와 artifact만
임시 directory에 기록해 원자적으로 rename합니다. 따라서 동시에 live result가 바뀌거나 `__deepcopy__`
hook이 source를 바꾸면 혼합 bundle을 만들지 않고 실패합니다.

선택된 `flowchart` 또는 `generic_network` typed 후보가 full/injective node-ID remap을 안전하게 완료한
경우에만 `node-id-map.json`을 추가하고 `manifest.json.files`에 content hash를 기록합니다. 파일은 각
mapping의 `source_owner`, 원래 `source_id`, `fused_id`, 독립 `vector`/`geometry` authority owner,
`match_method`(`identity`/`unique_iou`), 최소 0.45의 IoU, 원래 `source_text`와 양쪽 bbox/evidence ID를
보존합니다. immutable mapping의 `claim_digest`는 이 필드들의 canonical SHA-256 consistency digest입니다.
이는 ID 변경의 audit 자료이며 `provenance.json`을 대체하거나 새 evidence를 선언하지 않습니다.

파일의 top-level은 mapping object의 JSON array입니다. `source_bbox`와 `authority_bbox`는 source별 pixel
좌표가 아니라 `[0, 1]` 범위로 정규화된 `[x1, y1, x2, y2]`입니다. `source_owner`와
`authority_owner`는 해당 fusion 실행 안에서 입력을 구분하는 결정적 식별자이며 문서 재실행 사이의 영구
ID로 사용하지 않습니다. 모든 source/authority evidence ID는 같은 bundle의 reconstruction provenance에
정확히 한 번 존재해야 하며, 누락·중복 reference가 있으면 atomic writer가 bundle 생성을 거부합니다.
writer는 evidence payload를 현재 Pydantic schema로 다시 검증하고, source evidence bbox/text, authority
contour bbox, mapping evidence가 fused Scene node에 실제로 연결됐는지, 양쪽 block 교집합이
reconstruction의 `source_block_ids`와 겹치는지도 다시 검사합니다. 각 evidence ID는 mapping 전체에서도
한 번만 참조할 수 있습니다. generation pipeline은 mapping list에 process-private HMAC certification
seal을 붙이고 writer는 이를 요구하므로, model copy나 직접 구성한 mapping을 자동 추출 결과로 재인증할
수 없습니다. 이 seal은 sidecar 필드가 아니며 같은 reconstruction process 안의 trust boundary입니다.
mapping이 있으면 `write_provenance=false` 설정보다 이 참조 무결성 계약이 우선하므로
`provenance.json`을 강제로 함께 기록합니다.
이 파일은 generation-time audit artifact입니다. 이후 Review 편집은 revision history와 user evidence를
추가하지만 기존 자동 mapping을 새로운 추출 결과인 것처럼 다시 계산하지 않습니다.

지원하지 않는 nested/non-flow 유형, direct Mermaid, Scene fallback 또는 mapping이 모호·부분적·충돌한
후보에는 이 파일을 만들지 않습니다. 이 경우 typed candidate는 원래 ID 공간 전체를 유지하며 일부
reference만 바뀐 sidecar는 생성되지 않습니다. 따라서 파일 부재는 remap을 수행하지 않았다는 뜻이지
그 자체로 candidate parse/render 실패를 뜻하지 않습니다.

`review-history.json`은 빈 배열로 시작하며 review edit, candidate 선택, 자연어 patch, 승인·거절,
undo/redo를 append-only `ReviewHistoryEntry`로 기록합니다. 첫 mutation은 `review-state.json`과
`versions/r000000.*` 초기 snapshot을 만들고 이후 revision을 immutable하게 추가합니다. Mermaid,
Scene IR, SVG/PNG, provenance, advisory layout, manifest hash, state, history는 한 review commit으로 교체되며 I/O 실패 시
기존 artifact를 복원합니다. provenance payload는 SHA-256 content-addressed
`versions/provenance/<digest>.json`으로 중복 없이 보존하고 각 revision snapshot이 digest를 참조합니다.
layout payload도 `versions/layout/<digest>.json`으로 보존하며 root `layout-hints.json`과 manifest hash를
undo/redo에서 함께 생성·삭제합니다. Layout은 normalized node center만 담고 source Scene bbox를 바꾸지
않습니다.
`mmx-review-0.4.1` state는 current/legacy provenance와 advisory layout digest를 기록합니다. 0.4
snapshot provenance와 0.3 정적 provenance timeline도 기존 snapshot을 재작성하지 않고 lazy
migration과 undo/redo가 가능합니다. 낙관적 `version`과 code SHA-256이 stale
browser write를 차단합니다. Review API는 state가 검증한 active `timeline`과 `cursor`만 노출하며,
`checkout_revision`은 기존 snapshot을 새로 만들지 않고 target root artifacts와 manifest hash를
복원합니다. 이후 편집은 cursor 뒤 active timeline을 분기하지만 기존 immutable snapshot은 유지합니다.
Mermaid source가 바뀌었는데 validator가 boolean/`None`만 반환하면 이전 SVG/PNG는 현재 code의
render로 간주하지 않고 root artifact에서 제거합니다. 승인은 반드시 `ReviewValidationResult`로 새 strict
SVG와 선택적 inspected PNG를 반환한 경우에만 가능하므로 stale render를 승인 근거로 재사용하지 않습니다.
대안 후보를 선택한 뒤에는 생성 시점 manifest를 덮어쓰지 않고
`review-state.json.selected_candidate_id`가 현재 review 선택을 나타냅니다.
`source-map.json`은 serialized `DiscoveredSource`, fragment crop/page bbox, canvas placement,
source→canvas/page→canvas affine을 보존하여 canvas provenance를 PDF page와 source block으로 역추적합니다.
이 파일은 pipeline이 수용한 exact JSON-compatible mapping의 canonical snapshot만 기록합니다. Object
key는 정렬되고 tuple은 JSON array로 변환됩니다. Mapping은 depth 32, 전체 25,000 items, field당 50,000
characters, compact escaped JSON 4,000,000 bytes로 제한되며 non-finite 또는 JavaScript safe-integer
범위를 벗어난 숫자는 허용하지 않습니다. Sidecar writer는 serialization이나 deep copy 전에 같은
hook-free walker로 mapping을 다시 고정하고, snapshot 도중 live mapping이 바뀌면 bundle을 publish하지
않습니다. Pipeline에서 격리된 mapping은 `source-map.json`을 만들지 않으며 원인은 `failures`에 남습니다.

`typed-ir.json`과 alternative candidate JSON에는 sink 직전에 다시 canonicalized된 typed IR만 기록합니다.
각 IR은 UTF-8 text 1,000,000 bytes와 compact escaped JSON 4,000,000 bytes 이하이며 exact plain JSON
container/scalar, depth/item/field/numeric/cycle 계약 및 알려진 record별 256개 `evidence_ids` 상한을
통과해야 합니다. Writer는 selected와 alternatives를
`model_dump`, JSON serialization 또는 deep copy하기 전에 안전한 shallow candidate에 이 snapshot을
교체합니다. Live candidate가 생성 뒤 바뀌었거나 snapshot 도중 다시 바뀌면 임시 bundle을 publish하지
않습니다.

`provenance.json`의 retained `VisualEvidence.source_block_ids`는 duplicate를 포함한 논리적 occurrence
20,000개와 Python 문자열 길이 8,000,000자를 넘을 수 없습니다. `id`, `kind`, `text`, `font_weight`,
source-block ID 전체의 기존 8,000,000-character evidence cap도 별도입니다. Exact boundary는 보존하고
`+1`은 collection 전체를 원자적으로 거부합니다. Writer는 live evidence에 `model_dump`를 호출하거나
result를 deep-copy하거나 JSON을 만들기 전에 hook-free detached snapshot으로 이 계약을 확인하고,
검증된 snapshot만 sink payload와 `provenance.json`에 사용합니다. Output preflight도 어떤 image를 쓰기
전에 모든 final result에 같은 검사를 적용하고, 그 detached evidence를 가진 reconstruction snapshot을
이후 sidecar write까지 재사용합니다. 따라서 image 저장 중 caller의 live evidence가 바뀌어도 검증하지
않은 provenance가 뒤늦게 bundle에 섞이지 않습니다.

이는 저장 순서와 메모리 경계를 강화하는 내부 runtime 변경이며 `provenance.json` record shape,
`manifest.json`, `mmx-sidecar-0.5` schema version을 바꾸지 않습니다. Marker OCR 생산과 Review의
root/revision read, trusted replacement, digest/commit, structured-add 경계도 같은 aggregate gate를
사용합니다. Evaluation prediction artifact도 hash 검증 뒤 같은 source-block aggregate gate를 통과해야
하며, 실패하면 evaluation report tree를 만들거나 교체하지 않습니다. Prediction/report schema는
`mmx-eval-prediction-0.1`/`mmx-eval-report-0.1`을 유지합니다.

`include_rendered_preview`를 켠 Marker Markdown 출력은 validation receipt의 PNG SHA-256과 exact bytes가
일치하는 runtime PNG만 `images/`에 추가합니다.
원본 image는 계속 먼저 유지되며 preview는 Mermaid code의 게시 결정을 우회하지 않습니다.
requested type과 emitted/runtime type을 분리하므로 portable fallback을 native 복원처럼 표시하지 않습니다.

writer는 파일을 만들기 전에 source/image/sidecar/alternative 이름 충돌, 누락 source image, 기존 bundle,
metadata JSON 직렬화와 final-result evidence budget을 검사합니다. source별 bundle은 임시 directory에서
원자적으로 공개합니다.

## JSON 직렬화

PNG bytes와 SVG text는 candidate JSON에 중복으로 넣지 않습니다. 각각 artifact file로 저장하고
candidate JSON에는 validation, score, warning, IR, code를 둡니다. document metadata는 source별
summary와 sidecar path를 제공합니다.
