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

`MarkerSourceDiscovery`는 Marker의 구조화 block뿐 아니라 `current_children`의 loose object와 reference도
같은 iterator로 탐색합니다. registry는 `source_id → DiscoveredSource`, pixel registry는
`fragment_id → crop 전 PIL image`로 분리됩니다. 동일 page, bbox, image size, pixel digest를 가진
nested Figure/Picture는 canonical source 하나로 축약합니다.

`assemble_discovered_source()`는 panel crop과 same-page/cross-page fragment를 흰색 RGB virtual canvas에
결정적으로 조립합니다. 각 placement에는 source crop, canvas bbox, source→canvas 및 page→canvas affine,
page/block mapping이 남습니다. 조립 전 dimension과 pixel budget을 검사하며, 한 panel/merge 실패는
original이나 다른 source 처리를 중단하지 않습니다.

현재 경계는 Marker가 이미 제공한 block과 full-page image입니다. block 밖의 missed diagram을 새로
제안하는 page-level detector는 후속 범위입니다.
