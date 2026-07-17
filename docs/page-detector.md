# Page-level missed-diagram detector

The bounded page detector searches a full-page image for structural regions that Marker did not
classify as `Figure`, `Picture`, or `ComplexRegion`. It first downscales the page to the configured
maximum dimension, builds connected components from Canny edges when OpenCV is available (or a
Pillow edge map otherwise), and conservatively merges only components that align on one axis and
have a small gap.

A proposal must pass all of these gates:

- Long structural edges exist on both the horizontal and vertical axes.
- The region meets the minimum page-relative area and bounded aspect-ratio requirements.
- Edge and ink density do not look like ordinary text lines or a busy photograph.
- The region overlaps no existing Marker diagram-block bounding box by more than 1%.
- The region survives deterministic non-maximum suppression and the maximum-region budget.

`DiagramRegion` preserves the bounding box in original page-image coordinates, confidence,
component count, edge density, and contributing signals. The Marker adapter applies an affine
transform into the PDF page coordinate system and creates a `page_proposal` `SourceFragment`. If
the page already contains a diagram block, the nearest block is used only as the Markdown
insertion anchor. The proposal crop keeps an empty `source_block_ids` list so the anchor is not
incorrectly attributed as visual evidence.

When no anchor exists on the page, the proposal enters an internal `PageGroup` metadata queue.
It still goes through reconstruction and original-source sidecar storage, but it is not inserted
into automatic Markdown because there is no Marker image block to carry the original image.

If importing or running OpenCV fails, the detector switches to the Pillow backend and records a
warning in the detector result. Current Marker discovery metadata does not propagate that backend
warning. The detector creates grayscale data after downscaling and retains only the required crop
for each proposal rather than copying the full page. It never mutates the page image or Marker's
global block registry, and a detector failure is isolated to the affected page.
