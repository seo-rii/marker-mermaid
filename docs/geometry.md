# Geometry engine

`GeometryEngine`은 OpenCV가 있을 때 contour, rectangle, Hough line, triangle arrowhead를 검출하고
`DiagramSceneIR`과 `VisualEvidence`로 변환합니다. OpenCV가 없으면 예외 대신 warning과 empty
observation을 반환하여 다른 engine이 계속 동작합니다.

## 보수적 승격 규칙

- rectangle-like closed contour만 node가 됩니다.
- line endpoint 양쪽이 서로 다른 contour에 유일하게 연결될 때만 relation이 됩니다.
- arrowhead tip이 endpoint tolerance 안에서 유일할 때만 방향을 부여합니다.
- line 시작점에 arrowhead가 있으면 source/target과 polyline을 뒤집어 canonical source→target으로 둡니다.
- geometry는 label이나 semantic edge type을 추측하지 않습니다.
- 중첩 contour, 중복 line, 겹치는 arrowhead는 결정적으로 제거됩니다.

모든 node는 contour evidence를, 모든 relation은 line evidence와 가능한 arrowhead evidence를 가집니다.
원 Marker block ID도 evidence에 유지합니다.

## Ensemble 위치

Marker 기본 engine 순서는 Vector Primitive → Geometry → Structured VLM입니다. pipeline의 evidence
list는 같은 source context와 공유되며 새 evidence가 생길 때 visual prior를 다시 생성합니다. 따라서
VLM은 vector text/shape, contour/line, 실제 검출된 arrowhead endpoint overlay를 prompt와 image view에서
함께 확인합니다.

`FusionEngine`은 vector geometry, CV geometry, OCR/VLM label, VLM semantic relation을 각각 명시된
우선순위로 합칩니다. fused 후보와 원 engine 후보를 함께 candidate budget에 round-robin으로 넣어
fusion 실패나 과도한 병합에도 원 관찰을 대안으로 유지합니다.

## 게시 안전성

geometry-only Scene IR은 portable flowchart로 렌더할 수 있지만 모든 label이 `None`이면 자동 게시하지
않습니다. candidate는 `U` 등급과 warning을 갖고 sidecar/review에만 남으며, OCR 또는 VLM label fusion이
있어야 일반 게시 후보가 됩니다.
