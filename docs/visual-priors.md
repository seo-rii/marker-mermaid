# Type-aware visual priors

`build_visual_priors()`는 원본 의미를 바꾸지 않는 bounded 보조 view를 생성합니다. 원본은 EXIF 회전을
적용한 RGB image로 유지하고 VLM 전송용 복사본만 축소합니다. 큰 이미지의 tile은 축소본이 아니라
source-resolution image에서 직접 잘라 작은 글자와 arrowhead를 보존합니다. Tile 이름에는
`x1/y1/x2/y2` source 좌표가 포함됩니다.

## View budget

`max_views` 안에서 항상 original을 먼저 배치하고, 가능한 경우 global thumbnail을 둡니다. 큰 source와
tile 기능이 활성화되면 1~2개 slot을 tile에 먼저 예약합니다. 나머지 slot은 상위 type prediction에
맞춘 profile 우선순위로 채웁니다.

| Profile | 우선 prior |
| --- | --- |
| flow/BPMN/state | edge, arrow, OCR, contour, vector |
| architecture/C4 | contour, OCR, vector, color cluster |
| chart | OCR, Hough axis/line, threshold, grayscale |
| mindmap/tree | contour, edge, OCR |
| timeline/Gantt/planning | OCR, Hough line, threshold |
| packet | OCR, grid-like Hough line, threshold |

초기에는 general profile을 사용합니다. Geometry/vector/classifier engine이 evidence와 top-k type을 추가할
때 pipeline이 view를 다시 만들므로 뒤의 Structured VLM은 더 구체적인 prior 순서를 받습니다. Prompt의
view manifest는 실제 image 순서와 각 view의 width/height를 명시합니다.

## 생성 규칙

- `grayscale`과 local adaptive threshold는 낮은 대비 text/line을 보조합니다.
- Canny edge와 Hough line은 OpenCV가 있을 때 사용합니다. Edge 생성 실패는 Pillow fallback을 사용하고,
  Hough 실패는 warning과 함께 해당 view만 생략합니다.
- OCR/vector/arrow/contour overlay는 대응 evidence가 하나 이상 있을 때만 slot을 소비합니다.
- color cluster는 제한된 8색 quantization이며 원본 색을 대체하지 않습니다.
- Hough line이 하나도 검출되지 않으면 빈 흰 view를 보내지 않습니다.

`tile_size`는 최소 64이고 `tile_overlap`은 `0 <= overlap < tile_size`여야 합니다. 이 검사는 무한 loop와
무의미한 tile geometry를 구성 단계에서 차단합니다. 모든 view 실패는 candidate failure와 분리되어
원본-only reconstruction을 계속할 수 있습니다.
