# 결정적 Mermaid source repair

`ast_repair.py`는 validation 전에 실행되는 작은 보수적 repair 계층입니다. Mermaid parser를 흉내 내거나
LLM으로 전체 코드를 다시 쓰지 않습니다. 다음처럼 의미를 추가하지 않는 경우만 수정합니다.

- 맨 앞 Unicode BOM 제거
- 입력 전체를 감싼 단일 Mermaid Markdown fence 제거
- 독립 flowchart node 선언에서 명백하게 하나만 빠진 closing quote 보완
- 모든 사용 위치가 구조 token으로 확인된 flowchart identifier 정규화
- 완전히 동일한 중복 node 선언 제거
- 마지막 newline 추가

각 변경은 candidate `repair_history`에 iteration 0 event로 저장하며 line, before/after, reason을
포함합니다. event budget을 넘었거나 두 번째 pass에서 다시 바뀌는 non-idempotent 결과는 부분 적용하지
않습니다. dangling edge, conflicting duplicate, identifier collision은 node/edge를 추측하지 않고 issue
warning으로 남깁니다. repair 뒤 security scanner가 실패하면 변경 전체를 폐기하고 원문이 정규 hard
gate에서 거부되도록 둡니다.

`MermaidAstAdapter` protocol은 `mermaid-ast` 같은 외부 parser의 parse→render round-trip 안정성을
검사할 seam을 제공합니다. 현재 기본 배포에는 별도 `mermaid-ast` package adapter를 포함하지 않으며,
adapter 결과가 source를 대신하거나 security/real-render 검증을 우회하지도 않습니다.
