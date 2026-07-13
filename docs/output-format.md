# 출력 형식

## Markdown 불변조건

원본 image가 항상 먼저 나오고, 같은 source의 Mermaid fence는 최대 한 번만 삽입됩니다.
`syntax_valid && render_valid`가 아니거나 policy가 게시를 거부하면 image만 남습니다. C 등급은
`Experimental reconstruction` warning을 동반합니다.

한 Marker anchor에 virtual source가 있으면 original Mermaid, panel image/Mermaid, merged image/Mermaid
순서로 출력합니다. virtual source가 review/failed 상태여도 조립에 성공한 원본은 보존합니다.

## Sidecar bundle

각 source는 `diagrams/<safe-source-id>/`에 독립 bundle을 가집니다. writer는 같은 parent의 임시
directory에 모두 쓴 뒤 `os.replace`로 최종 directory를 공개합니다. 이미 존재하는 bundle은
자동 덮어쓰지 않습니다. path component는 allowlist로 정규화하며 absolute path와 `..`를
허용하지 않습니다.

`manifest.json`의 `schema_version`은 `mmx-sidecar-0.4`입니다. `0.4`부터 소스에서 관찰한
`scene-ir.json`과 선택 후보에서 다시 구성한 `generated-scene-ir.json`을 구분합니다. 전자는
OCR/CV/VLM 추출 근거이며 후자는 품질 평가 대상입니다. Direct Mermaid처럼 생성 구조를 안전하게
역추출할 수 없는 후보는 `generated-scene-ir.json`을 생략하므로 구조 품질을 평가 불가로 처리해야
합니다.

```json
{
  "schema_version": "mmx-sidecar-0.4",
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
  "files": {
    "final.mmd": "sha256...",
    "final.svg": "sha256...",
    "source-map.json": "sha256..."
  },
  "failures": []
}
```

`files`의 값은 content SHA-256입니다. `final.*`은 hard gate를 통과해 selected가 된 candidate에만
생성됩니다. 실패하거나 선택되지 않은 후보는 `alternatives/`에 JSON과 가능한 `.mmd`로 남습니다.

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
대안 후보를 선택한 뒤에는 생성 시점 manifest를 덮어쓰지 않고
`review-state.json.selected_candidate_id`가 현재 review 선택을 나타냅니다.
`source-map.json`은 serialized `DiscoveredSource`, fragment crop/page bbox, canvas placement,
source→canvas/page→canvas affine을 보존하여 canvas provenance를 PDF page와 source block으로 역추적합니다.

`include_rendered_preview`를 켠 Marker Markdown 출력은 검증된 runtime PNG를 `images/`에 추가합니다.
원본 image는 계속 먼저 유지되며 preview는 Mermaid code의 게시 결정을 우회하지 않습니다.
requested type과 emitted/runtime type을 분리하므로 portable fallback을 native 복원처럼 표시하지 않습니다.

writer는 파일을 만들기 전에 source/image/sidecar/alternative 이름 충돌, 누락 source image, 기존 bundle,
metadata JSON 직렬화를 검사합니다. source별 bundle은 임시 directory에서 원자적으로 공개합니다.

## JSON 직렬화

PNG bytes와 SVG text는 candidate JSON에 중복으로 넣지 않습니다. 각각 artifact file로 저장하고
candidate JSON에는 validation, score, warning, IR, code를 둡니다. document metadata는 source별
summary와 sidecar path를 제공합니다.
