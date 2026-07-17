# Candidate discovery

`discovery.py` is a deterministic proposal layer with no dependency on Marker objects. Marker
blocks, page-level detections, and user-selected regions reuse the same functions by passing only
Pillow images and plain bounding boxes.

## Composite panels

`propose_composite_panels()` combines these signals:

- A sufficiently wide vertical or horizontal whitespace gutter with low foreground density
- A long, thin separator line
- Independent connected-component groups on both sides of the split
- A minimum panel area relative to the full source

It selects at most one strongest vertical and one strongest horizontal split near the center, so
no more than four panels are proposed. Every output region must contain a meaningful component,
and the unsplit source is never deleted. `split_composite_figure()` returns both original-coordinate
bounding boxes and crops. A bounded eight-neighbor Python connected-component implementation is
used when OpenCV is unavailable.

## Fragment merging

`propose_fragment_merges()` requires both a spatial signal—adjacent bounding boxes or compatible
boundary contact—and a semantic signal—shared caption or `continued`. Merely placing two figures
near one another is not enough. A cross-page proposal requires boundary contact between the
bottom of the earlier page and the top of the next page as well as a semantic signal.

Proposals never modify source blocks. `DiscoveredSource.fragments` preserves each page bounding
box, block mapping, crop bounding box, image size, and virtual-canvas offset independently, so a
multi-page continuation is never flattened into a single bounding box.

## Full-page coverage

`assess_full_page_coverage()` measures width, height, and area ratios of the page/candidate
intersection together with distance from all four page edges. Overscan outside the page is
clipped for the calculation. The default requires at least 90% area coverage with every edge
within 4% of the corresponding page dimension.

## Current boundaries

`MarkerSourceDiscovery` traverses both structured Marker blocks and loose objects/references in
`current_children` through the same iterator. Separate registries map `source_id` to
`DiscoveredSource` and `fragment_id` to the pre-crop Pillow image. Nested `Figure`/`Picture`
objects with the same page, bounding box, image size, and pixel digest collapse into one
canonical source.

`assemble_discovered_source()` deterministically places panel crops and same-page/cross-page
fragments on a white RGB virtual canvas. Each placement records its source crop, canvas bounding
box, source-to-canvas and page-to-canvas affine transforms, and page/block mapping. Dimension and
pixel budgets are checked before assembly; one panel or merge failure does not stop the original
or other sources.

The [page-level detector](page-detector.md) runs on full-page images. Non-overlapping
edge/component clusters become `page_proposal` crops, and only proposal pixels are retained. An
existing diagram block on the page is used only as a Markdown anchor and is not attributed as
source evidence. An anchorless proposal enters the internal `PageGroup` metadata queue for
reconstruction and sidecar output, but is not automatically inserted into Marker Markdown
because no original image block exists at the insertion point.
