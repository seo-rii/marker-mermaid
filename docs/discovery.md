# 후보 영역 발견

`discovery.py`는 Marker 객체에 의존하지 않는 deterministic proposal 계층입니다. Marker block,
page-level detector, 사용자 선택 영역이 같은 함수를 재사용하도록 PIL image와 plain bbox만 받습니다.

## Composite panel

`propose_composite_panels()`는 다음 신호를 결합합니다.

- foreground density가 낮고 충분히 넓은 수직·수평 whitespace gutter
- 길이가 길고 얇은 separator line
- split 양쪽에 존재하는 독립 connected-component group
- 전체 면적 대비 최소 panel 면적

중심에서 가장 강한 수직/수평 split 하나씩만 선택하므로 최대 네 panel을 제안합니다. 모든 결과
region에 의미 있는 component가 있어야 하며, unsplit source를 삭제하지 않습니다.
`split_composite_figure()`가 원본 좌표 bbox와 crop을 함께 반환합니다. OpenCV가 없으면 bounded
8-neighbor Python connected-component 구현을 사용합니다.

## Fragment merge

`propose_fragment_merges()`는 인접 bbox 또는 호환되는 boundary touch라는 공간 신호와 shared
caption/continued라는 의미 신호를 요구합니다. 단순히 가까운 두 Figure만으로는 merge하지 않습니다.
cross-page proposal은 앞 page bottom과 다음 page top의 boundary touch 및 의미 신호가 모두 필요합니다.

proposal은 source block을 수정하지 않습니다. `DiscoveredSource.fragments`는 각 page의 bbox,
block mapping, crop bbox, image size, virtual canvas offset을 별도로 보존하여 multi-page continuation도
단일 bbox로 뭉개지지 않게 합니다.

## Full-page coverage

`assess_full_page_coverage()`는 page와 candidate 교집합의 width/height/area ratio 및 네 edge distance를
계산합니다. page 밖 overscan은 coverage에서 clipping합니다. 기본 판정은 area 90% 이상이고 모든
edge가 page dimension의 4% 이내인 경우입니다.

## 현재 경계

이 계층은 proposal과 source registry 모델까지 제공합니다. Marker page registry에서 virtual source를
실제로 crop/조립하고 renderer에 panel/merged 원본을 추가하는 연결은 별도 구현 단위로 진행합니다.
따라서 현재 대응표는 `구현`이 아니라 `기반`으로 표시합니다.
