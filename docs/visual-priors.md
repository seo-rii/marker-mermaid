# Type-aware visual priors

`build_visual_priors()` creates bounded auxiliary views without changing the meaning of the
source. The original remains an EXIF-oriented RGB image, while only the copy sent to the VLM is
downscaled. Tiles for a large diagram are cropped directly from the source-resolution image—not
from the downscaled copy—to retain small text and arrowheads. Tile names include source
coordinates as `x1/y1/x2/y2`.

## View budget

The original is always first within `max_views`, followed by a global thumbnail when possible.
When tiling is enabled for a large source, one or two slots are reserved for tiles. Remaining
slots are filled by the profile priority for the highest-ranked diagram type.

Configuration and provider adapters enforce the same hard boundary. `max_views` is at most 16;
an original, overlay, or `tile_size` edge is at most 4,096 pixels; one view is at most 16,777,216
pixels; and the full image list is at most 33,554,432 pixels. The builder always retains the
original and omits only optional tiles/views that do not fit, recording warnings. Before calling
a provider, the adapter confirms that the first view is an `original` RGB Pillow image and
rechecks all limits.

To detect overflow, the input dictionary snapshots only `max_views + 1` entries rather than
materializing the entire list. Caller-owned objects are not reused: every view is separated into
an independent plain Pillow snapshot, the limits are checked again, and the same canonical order
is used for both the prompt manifest and provider image list. The adapter does not invoke caller
property/load/copy hooks; a lazy `ImageFile` subclass must be loaded before entry so the exact
Pillow pixel core can be captured.

Independently of these bounded provider views, the pipeline retains a full-resolution canonical
RGB source within `max_virtual_source_dimension` and `max_virtual_source_pixels`. Every engine
receives its own source copy, preventing mutations from leaking between engines. The full source
dimensions are also used when rebuilding overlays after new evidence and when checking the fusion
canvas. Downscaling the `original` preview with `max_image_dimension` therefore never changes
Geometry/Vector bounding boxes or the source-mapping coordinate system.

| Profile | Prior order |
| --- | --- |
| flow/BPMN/state | edge, arrow, OCR, contour, vector |
| architecture/C4 | contour, OCR, vector, color cluster |
| chart | OCR, Hough axis/line, threshold, grayscale |
| mindmap/tree | contour, edge, OCR |
| timeline/Gantt/planning | OCR, Hough line, threshold |
| packet | OCR, grid-like Hough line, threshold |

The initial pass uses the general profile. As Geometry, vector, and classifier engines add
evidence and top-k types, the pipeline rebuilds the views so the later Structured VLM receives a
more specific prior order. The prompt's view manifest records the actual image order and each
view's width and height.

## Generation rules

- `grayscale` and local adaptive thresholding support low-contrast text and lines.
- Canny edges and Hough lines are used when OpenCV is available. Edge-generation failure falls
  back to Pillow; Hough failure records a warning and omits only that view.
- OCR, vector, arrow, and contour overlays consume a slot only when corresponding evidence exists.
- Color clustering uses a bounded eight-color quantization and never replaces the original.
- An empty white view is not sent when Hough detects no lines.

`tile_size` must be between 64 and 4,096, and `tile_overlap` must satisfy
`0 <= overlap < tile_size`. Validation at construction time prevents infinite loops and
meaningless tile geometry. Any view failure remains separate from candidate failure, allowing
original-only reconstruction to continue.
