# Release evaluation

`marker-mermaid evaluate`는 MMX-001 §23 테스트 corpus와 §24 승인 기준을 고정된
`mmx-001-v0.3-extended` profile로 집계합니다. 이 명령은 PDF 변환이나 VLM 호출을 실행하는 benchmark
runner가 아닙니다. 신뢰된 runner가 만든 prediction과 telemetry를 입력으로 받고, artifact 무결성과
집계 재현성을 책임집니다.

```bash
marker-mermaid evaluate corpus/manifest.json --output output/evaluation
```

필수 근거가 없는 gate는 성공으로 간주하지 않고 `unavailable`로 기록합니다. 전체 상태는 하나라도
`fail`이면 `fail`, 실패는 없지만 `unavailable`이 있으면 `unavailable`, 모두 충족하면 `pass`입니다.
CLI 종료 코드는 각각 `1`, `1`, `0`이며 manifest/path/hash 오류는 `2`, 보고서 I/O 오류는 `3`입니다.
Report의 `attestation` 값은 항상 `trusted_runner_input`입니다. 따라서 `pass`는 hash-bound 입력이 고정
profile의 수치 조건을 충족했다는 뜻이며, runner의 신원이나 telemetry의 진실성을 암호학적으로
증명하지 않습니다.

## Trust boundary

입력은 세 개의 서로 다른 artifact로 나뉩니다.

- `source`: 실제 image/PDF 또는 synthetic source
- `ground_truth`: 독립적으로 주석한 diagram type, Scene IR, OCR label, 숫자
- `prediction`: runner가 만든 type, `generated_scene_ir`, 게시 결과, validation 및 hard-gate telemetry

세 파일은 manifest 상대 경로와 SHA-256으로 고정됩니다. absolute path, `..`, symlink, root 밖으로
해석되는 path, digest가 다른 파일은 거부합니다. 소스에서 관찰한 `scene-ir.json`을 prediction으로
사용하면 안 됩니다. Typed/Scene candidate의 출력 구조인 sidecar `generated-scene-ir.json`을 prediction
artifact의 `generated_scene_ir`에 넣어야 합니다. Direct Mermaid에 이 구조가 없으면 `null`로 기록하며,
provenance/구조 gate는 fail-closed로 `unavailable` 또는 낮은 recall이 됩니다.

`telemetry`는 aggregator가 독립적으로 재실행해 검증하는 값이 아니라 trusted runner의 측정값입니다.
Runner는 격리된 runtime에서 다음을 직접 관찰해야 합니다.

- 원본 image가 최종 document에 존재하고 참조되는지
- 게시된 Mermaid가 security scan, parse, render, SVG inspection을 통과했는지
- candidate failure injection이 document 전체 실패로 전파되는지
- external action, 중복 삽입, orphan process, candidate budget 초과가 발생했는지

이 신뢰 경계를 숨기지 않기 위해 prediction schema의 필드 이름을 `telemetry`로 두고, report에는
manifest SHA-256 snapshot을 포함합니다.

Prediction은 `VisualEvidence[]` registry도 포함합니다. Generated node의 `evidence_ids` 문자열이 비어
있지 않은 것만으로 provenance를 인정하지 않고, 같은 hash-bound prediction artifact registry에 실제로
존재하는 `ocr_token`, `vector_text`, `contour`, `vlm_observation`, `user_edit` ID와
교집합이 있어야 합니다. `source_crop`, `line_segment`, `arrowhead`는 node provenance credit을
만들지 않습니다. 한 case에서 둘 이상의 generated node가 같은 eligible evidence ID를 참조하면
그 ID는 모든 claimant에서 revoke합니다. 이 충돌 계산은 case-local이므로 다른 corpus case의
같은 로컬 ID는 서로 충돌하지 않으며, relation/group 참조도 node claim 충돌에 포함하지 않습니다.

## Manifest contract

최상위 schema는 `mmx-eval-manifest-0.1`입니다.

```json
{
  "schema_version": "mmx-eval-manifest-0.1",
  "gate_profile": "mmx-001-v0.3-extended",
  "corpus": {
    "corpus_id": "enterprise-diagrams",
    "version": "2026.07",
    "license": "internal-evaluation-only",
    "split": "release-test"
  },
  "cases": [
    {
      "case_id": "architecture-001",
      "fixture_group": "architecture_c4",
      "fixture_tiers": ["real_enterprise", "multilingual"],
      "source_origin": "real",
      "scope": "end_to_end",
      "languages": ["ko", "en"],
      "source": {"path": "sources/architecture-001.png", "sha256": "...64 hex..."},
      "ground_truth": {"path": "truth/architecture-001.json", "sha256": "..."},
      "prediction": {"path": "predictions/architecture-001.json", "sha256": "..."}
    }
  ]
}
```

`fixture_tiers`는 하나의 case가 real enterprise이면서 multilingual일 수 있으므로 배열입니다.
`scope`는 `serializer`, `end_to_end`, `detector`, `fault_probe` 중 하나입니다. §24 기능 gate는
`source_origin=real`이고 `scope=end_to_end`인 case만 인정합니다. `fault_probe`는 prediction telemetry에
실제 candidate failure injection을 표시해야 합니다.

Configured diagram type마다 stability와 fixture group이 고정되어 있습니다. 예를 들어 flowchart는
`core/flowchart`, C4는 `experimental/architecture_c4`, pie는 `extended/data_chart`여야 합니다.
같은 source SHA-256은 path와 case ID가 달라도 두 fixture로 셀 수 없으며 manifest를 거부합니다.

Ground truth schema `mmx-eval-ground-truth-0.1`은 positive case에 독립 Scene IR을 요구합니다.
Negative case는 `expected_reconstruction=false`, `type_stability=negative`이며 type/Scene/label/number를
가질 수 없습니다. Prediction schema는 `mmx-eval-prediction-0.1`입니다. 게시 결과의
`syntax_valid`/`render_valid`가 false 또는 null인 위반 사례도 artifact 자체는 유효하며, hard gate가
이를 `fail`로 보고합니다.

Positive Scene은 node가 하나 이상이어야 하며 text-bearing node의 token multiset을 `ocr_labels`가 모두
포함해야 합니다. 숫자 유형은 `numeric_applicable=true`와 하나 이상의 유한 Decimal 값, 또는
`numeric_applicable=false`와 이유를 요구합니다. `1`, `1.0`, `1e0`은 같은 canonical Decimal로
비교합니다. Flowchart는 `path_applicable`을 명시하고 false이면 이유를 기록해야 합니다. 자동 게시된
A/B/C end-to-end 결과의 `human_accepted`는 prediction이 아니라 hash-bound ground truth annotation에
두며, 하나라도 빠지면 human-review coverage gate가 실패하고 accept rate는 unavailable입니다.

## Fixed gates

Fixture 최소치는 Flowchart 100, UML 100, Architecture/C4 80, BPMN/Swimlane 80, planning 80,
data chart 120, mindmap/tree 50, specialized 100, negative 150입니다. 기능 gate는 스펙의 22개 유형
각각에 실제 end-to-end parse/render 성공 fixture를 하나 이상 요구합니다.
Positive fixture 최소치에는 end-to-end case만, negative 최소치에는 detector case만 포함하므로
serializer/fault probe로 corpus 크기를 부풀릴 수 없습니다.

구조 precision/recall, flowchart edge F1과 path F1은 case별 F1 평균이 아니라 전체 true-positive,
false-positive, false-negative를 합친 micro score입니다. 동일 label이 다른 case에 나타나는 OCR recall도
case ID로 분리한 token multiset으로 세므로 case 내부/사이의 반복 label 누락도 반영합니다. Architecture node recall은 C4를 섞지 않고
정답 type이 정확히 `architecture`인 case만 사용합니다. Negative image에서 생성한 node/relation은 구조
precision의 false positive입니다.

Positive 품질 분모에는 `scope=end_to_end`만 들어가며 serializer와 fault probe는 제외합니다. Negative
hallucination은 `scope=detector`만 structural precision에 포함합니다. Data chart는 구조 다이어그램
precision/recall에서 제외하고 numeric exact match로 평가합니다. Flowchart edge F1은 arrow flag에서
만든 directed arc multiset이므로 모든 화살표를 뒤집으면 일치하지 않습니다. Node ID는 ground truth가
`shared_id_namespace=true`를 명시한 경우에만 직접 일치시키며, 기본은 unique normalized label로
독립 정답과 예측을 정렬합니다.

Path가 applicable인 flowchart는 reference path가 실제로 열거되어야 합니다. 생성 path 열거가
10,000-path 또는 100,000-state budget을 넘으면 해당 필수 metric은 조용히 제외되지 않고
`unavailable_case_ids`와 함께 unavailable이 됩니다. Experimental end-to-end 결과는 warning, sidecar,
review 가능성, hallucination precision 기록을 모두 별도 gate로 확인합니다.

## Output

출력 directory는 완성된 임시 tree로 만든 뒤 교체됩니다.

```text
output/evaluation/
├── .marker-mermaid-evaluation.json
├── evaluation-report.json
├── evaluation-report.md
├── manifest-snapshot.json
└── cases/
    └── architecture-001.json
```

JSON report schema는 `mmx-eval-report-0.1`입니다. Manifest digest, corpus metadata, fixture counts,
모든 gate의 observed/required/sample count, raw TP/FP/FN, evidence/unavailable case ID, case별
type/node/edge/path 지표를 보존합니다. `manifest-snapshot.json`은 normalize한 재직렬화가 아니라 검증한
원문 bytes이므로 report의 manifest SHA-256을 그대로 재현합니다.

기존 output directory를 교체하려면 evaluator ownership marker가 있어야 합니다. 일반 directory,
symlink, corpus root 또는 그 조상은 거부하므로 `--output .` 같은 입력이 기존 작업물을 지우지 않습니다.
