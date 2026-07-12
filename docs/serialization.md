# Typed serializer와 fallback 계약

`SerializationResult`는 Mermaid 문자열만 반환하지 않고 다음을 함께 기록합니다.

- `requested_type`: classifier/typed IR이 요청한 의미 type
- `emitted_type`: serializer가 실제 사용한 Mermaid grammar
- `fallback_chain`: requested에서 emitted까지의 전체 경로
- `warnings`: 표현 손실 또는 parser/compatibility 제한
- `stability`: stable, extended, experimental

native 결과는 한 항목 chain만 허용합니다. fallback은 requested로 시작하고 emitted로 끝나야 하며
warning이 필수입니다. cycle, 빈 code, 중복 chain, 잘못 보고한 result serializer는 dispatch 단계에서
거부합니다. 기존 문자열 serializer는 `SerializationRegistry`가 같은 계약으로 감쌉니다.

## 현재 type mapping

| 요청 type | 실제 grammar | 비고 |
| --- | --- | --- |
| Flowchart, Sequence, Mindmap, Timeline, Gantt, Architecture | 동일 | Phase 1 native |
| State, Class, ER | 동일 | node/relation/member/attribute evidence 필수 |
| Requirement | `requirement` | Mermaid `requirementDiagram` |
| Block | `block` | Mermaid 11.16은 이 grammar의 accTitle/accDescr를 거부하여 접근성 text를 typed IR에 보존 |
| Swimlane | `flowchart` | subgraph fallback |
| BPMN | `bpmn → swimlane → flowchart` | BPMN 전용 notation 손실 |
| Generic Network | `flowchart` | portable node/edge 표현 |
| C4 | `architecture` | native C4 SVG가 strict gate의 data/xlink 정책과 불일치 |
| Deployment, Component | `architecture` | stereotype/interface/link label 일부는 typed IR에 유지 |
| Use-case | `flowchart` | actor glyph와 system boundary는 typed IR에 유지 |

State/Class/ER serializer는 provenance 없는 구조를 문법적으로 만들 수 있어도 거부합니다. unknown
endpoint, 추측 cardinality, ER의 identifying flag 누락도 `SerializationError`입니다. Requirement/Block과
fallback serializer 역시 unknown relation endpoint를 임의 node로 만들지 않습니다.

validation 이후 Mermaid runtime이 보고한 diagram type도 `runtime_diagram_type`에 저장합니다. deterministic
typed serializer의 declared emitted type과 runtime type이 다르면 render-valid 후보로 취급하지 않습니다.
direct Mermaid는 실제 runtime type으로 재분류하고 type-fitness를 0으로 두어 검토 경고를 유지합니다.
