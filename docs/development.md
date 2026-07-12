# 개발 및 테스트

## 로컬 검증

```bash
pip install -e '.[marker,vision,dev]'
marker-mermaid install-runtime
ruff check src tests
ruff format --check src tests
pytest
```

기본 단위 테스트는 fake runtime/fixture engine을 사용해 네트워크가 필요 없습니다. `integration`
marker는 실제 Mermaid Chromium 또는 Marker 1.10.2 import를 확인합니다. 실제 Mermaid test도
network route는 차단되어 있습니다.

## 테스트 계층

- config/model: budget, 원본 불변조건, ID/reference integrity, 등급 경계
- security: click/directive/URL/HTML/style 악성 corpus
- serializer: Phase 1 모든 serializer의 실제 Mermaid parse/render
- pipeline: engine failure isolation, candidate budget, deterministic selection
- sidecar/Markdown: 원본 우선, manifest/hash, alternatives
- Marker compatibility: Reference/BlankPage 사이 processor 순서와 exact version

## Fixture 원칙

fixture JSON은 VLM observation schema 자체를 사용합니다. 따라서 실제 LLM의 비결정성과 API 비용
없이 serializer/validator/policy regression을 재현할 수 있습니다. synthetic source는 저작권 문제가
없는 자체 도형을 사용합니다. 외부 연구 dataset을 추가할 때는 원 출처 license와 split 정보를
fixture manifest에 기록해야 합니다.

## 새 serializer 추가

1. `ALL_TYPES`에 canonical type이 있는지 확인합니다.
2. typed IR의 필수 값과 “모르면 생성하지 않음” 규칙을 정합니다.
3. `serializers.py`에 deterministic serializer를 추가합니다.
4. strict security profile에서 real Mermaid parse/render fixture를 추가합니다.
5. native syntax가 아니면 fallback type과 원 type을 metadata/warning에 모두 남깁니다.
6. 숫자 유형은 OCR evidence가 없을 때 임의 값을 만들지 않습니다.

## Version pin 갱신

Mermaid/Playwright를 함께 갱신하고 `package-lock.json`을 재생성한 뒤 모든 diagram grammar와 악성
input test를 실행합니다. Marker version 변경은 processor ordering, Block image/metadata API,
renderer image naming을 다시 조사한 별도 compatibility change로 다룹니다.

