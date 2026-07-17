# Geometry engine

`GeometryEngine` detects contours, rectangles, Hough lines, and triangular arrowheads when
OpenCV is available, then converts them into `DiagramSceneIR` and `VisualEvidence`. Without
OpenCV, it returns a warning and an empty observation instead of raising, allowing other engines
to continue.

## Conservative promotion rules

- Only rectangle-like closed contours become nodes.
- A line becomes a relation only when both endpoints connect uniquely to different contours.
- Direction is assigned only when an arrowhead tip uniquely falls within endpoint tolerance.
- If an arrowhead is found at the start of a line, source/target and the polyline are reversed to
  produce canonical source-to-target order.
- Geometry never guesses labels or semantic edge types.
- Nested contours, duplicate lines, and overlapping arrowheads are removed deterministically.

Every node carries contour evidence. Every relation carries line evidence and any available
arrowhead evidence. The originating Marker block ID remains attached to that evidence.

## Position in the ensemble

The default Marker engine order is Vector Primitive → Geometry → Structured VLM. Pipeline
evidence shares the same source context, and visual priors are rebuilt when new evidence arrives.
The VLM can therefore inspect vector text and shapes, contours and lines, and detected arrowhead
endpoint overlays in both the prompt and image views.

`FusionEngine` combines vector geometry, CV geometry, OCR/VLM labels, and VLM semantic relations
according to their explicit precedence. Fused and original engine candidates enter the candidate
budget in round-robin order, keeping the raw observations available when fusion fails or merges
too aggressively.

## Publication safety

A geometry-only Scene IR can render as a portable flowchart, but it is not automatically
published when every label is `None`. Such a candidate receives grade `U` and a warning and is
kept only in sidecars and review. It becomes eligible for ordinary publication only after OCR or
VLM label fusion supplies labels.
