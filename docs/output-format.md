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

`manifest.json`의 `schema_version`은 `mmx-sidecar-0.3`입니다.

```json
{
  "schema_version": "mmx-sidecar-0.3",
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
`review-history.json`은 빈 배열로 시작하며 review edit, candidate 선택, 자연어 patch, 승인·거절,
undo/redo를 append-only `ReviewHistoryEntry`로 기록합니다. 첫 mutation은 `review-state.json`과
`versions/r000000.*` 초기 snapshot을 만들고 이후 revision을 immutable하게 추가합니다. Mermaid,
Scene IR, SVG/PNG, provenance, manifest hash, state, history는 한 review commit으로 교체되며 I/O 실패 시
기존 artifact를 복원합니다. provenance payload는 SHA-256 content-addressed
`versions/provenance/<digest>.json`으로 중복 없이 보존하고 각 revision snapshot이 digest를 참조합니다.
`mmx-review-0.4` state는 current/legacy provenance digest를 기록하므로 0.3 timeline도 기존 snapshot을
재작성하지 않고 lazy migration과 undo/redo가 가능합니다. 낙관적 `version`과 code SHA-256이 stale
browser write를 차단합니다.
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
