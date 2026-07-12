# 연구 및 구현 참고

MMX-001 v0.3 설계는 다음 공개 연구/구현의 아이디어를 반영합니다.

| 자료 | 프로젝트에서 사용한 개념 |
| --- | --- |
| [Flowchart2Mermaid](https://arxiv.org/abs/2512.02170) | mixed-initiative review 방향 |
| [EdgeFlow](https://arxiv.org/abs/2605.27332) | edge-map prior, path 평가, 반복 상한 |
| [Arrow-Guided VLM](https://arxiv.org/abs/2505.07864) | arrow endpoint/direction overlay |
| [BPMN-VLM](https://arxiv.org/abs/2511.22448) | structure 우선, OCR label enrichment |
| [Reference-Free Evaluation](https://arxiv.org/abs/2602.13376) | OCR recall과 entailment precision |
| [FlowPathAgent](https://arxiv.org/abs/2506.01344) | node/path provenance |
| [FlowLearn](https://arxiv.org/abs/2407.05183) | ensemble 및 multimodal evaluation |
| [FlowVQA](https://arxiv.org/abs/2406.19237) | 방향성과 logical progression 평가 |

Mermaid runtime은 공식 문서를 기준으로 구현합니다.

- [Usage: parse and render](https://mermaid.js.org/config/usage)
- [Mermaid API interface](https://mermaid.js.org/config/setup/mermaid/interfaces/Mermaid.html)
- [Security level](https://mermaid.js.org/config/schema-docs/config-properties-securitylevel.html)
- [Configuration schema](https://mermaid.js.org/config/schema-docs/config)

