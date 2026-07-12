# Page-level missed diagram detector

Marker가 Figure/Picture/ComplexRegion으로 만들지 않은 구조 영역을 찾기 위해 full page image에 bounded
detector를 적용합니다. 최대 처리 dimension으로 downscale한 뒤 Canny(OpenCV가 있을 때) 또는 Pillow
edge map에서 connected component를 만들고, 한 축에서 정렬되며 작은 gap을 가진 component만 제한적으로
병합합니다.

proposal은 다음 보수적 gate를 통과해야 합니다.

- 가로·세로 양쪽에 긴 structural edge가 존재
- page 대비 최소 면적과 bounded aspect ratio 충족
- edge/ink density가 text-line 또는 busy photo 범위가 아님
- 기존 Marker diagram block bbox와 1% 넘게 겹치지 않음
- deterministic NMS와 최대 region budget 통과

`DiagramRegion`은 원본 page-image 좌표의 bbox, confidence, component count, edge density, signal을
보존합니다. Marker adapter는 PDF page bbox로 affine 변환한 `page_proposal` SourceFragment를 만들고,
동일 page에 기존 diagram block이 있으면 가장 가까운 block을 Markdown insertion anchor로만 사용합니다.
proposal crop의 `source_block_ids`는 비워 두어 anchor block을 시각 evidence로 잘못 귀속하지 않습니다.
page에 anchor가 전혀 없으면 proposal은 discovery registry에는 남지만 현재 Marker Markdown renderer에는
자동 삽입되지 않습니다.

OpenCV import나 실행이 실패하면 Pillow backend로 전환하고 warning을 기록합니다. page image와 Marker의
전역 Block registry는 변경하지 않으며, detector 실패는 해당 page error로 격리됩니다.
