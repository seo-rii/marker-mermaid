# Evidence-backed semantic repair

Marker 기본 pipeline은 typed Flowchart/Generic Network label에 한해 deterministic semantic repair를
시도합니다. 이는 자유 형식 LLM self-correction이 아니라 topology를 바꿀 수 없는 좁은 enrichment입니다.

교정은 다음 조건을 모두 만족해야 합니다.

- 후보가 typed IR에서 생성된 Flowchart 또는 Generic Network이다.
- source Scene과 typed node가 exact ID로 일치한다.
- source Scene label이 비어 있지 않고 현재 typed label과 실제로 다르다.
- source element가 참조한 `vector_text`, 또는 score 0.8 이상의 `ocr_token` text가 source label과
  NFKC/casefold 정규화 후 정확히 일치한다.

충족한 label을 한 번에 모두 교정하며 node ID, node 수, edge endpoint/type, diagram type, style은 바꾸지
않습니다. Supporting evidence ID를 typed node에 추가하고 accessibility description도 generated text였던
경우에만 다시 계산합니다. 명시적으로 작성된 accessibility text는 유지합니다.
이미 style recovery를 채택한 후보는 재직렬화가 style을 버릴 수 있으므로 기본 semantic repair에서
제외합니다.

## 채택 gate

Repair proposal은 code와 갱신된 typed IR을 함께 반환합니다. Pipeline은 수정 code를 다시 security
scan/parse/render하고 runtime diagram type이 유지되는지 확인합니다. 초기 후보와 같은 평가 함수로 OCR,
numeric, provenance, edge, arrow, layout, path, type fitness를 모두 다시 계산합니다. 다음 조건을 모두
만족할 때만 채택합니다.

- 이전 aggregate와 새 aggregate가 모두 평가 가능하다.
- aggregate가 epsilon보다 크게 엄격 개선된다.
- non-runtime semantic score가 감소하지 않는다.
- 기존 numeric/provenance publication gate를 그대로 통과한다.

따라서 `aggregate_score=None`인 held candidate를 label 교정만으로 게시 가능하게 만들 수 없습니다. 원본
baseline candidate는 변경하지 않고 alternative에 남으며 repair candidate에는 구조화된 correction,
before/after score, 채택 여부가 기록됩니다.

현재 기본 repair는 label만 다룹니다. Edge 방향, 누락 node, layout, raw Mermaid 수정은 code와 typed IR을
동시에 안전하게 갱신할 AST/semantic patch 계층이 마련되기 전까지 자동 수행하지 않습니다.
