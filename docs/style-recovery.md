# Style recovery

The current implementation maps trusted PDF vector style evidence from Scene IR to the Flowchart
family's node fill, border color, dashed/thick borders, vector-backed bold labels, link stroke
color and dashed/thick style, and flat-group fill/stroke/dashed/thick style backed by a trusted
vector container.

Stroke color from an open PDF vector path is retained in `SceneRelation.line_color` and is combined
into the corresponding `linkStyle` declaration only when relation endpoints map exactly to Mermaid
edge order. Colors are restricted to `#RGB(A)`, `#RRGGBB(AA)`, or a small named-color allowlist.
The implementation never emits `url()`, functional colors, or arbitrary CSS properties.

Only bold bit `16` is interpreted from the integer `flags` of a PyMuPDF text span. An element gets
`SceneElement.font_weight="bold"` only when every text span assigned to that node is bold; emphasis
is omitted when normal and unknown spans are mixed. If duplicate spans with identical text and bbox
claim different weights, the label is retained once and the weight is discarded. Because the
`font_weight` model is a `normal | bold` enum, it cannot carry a font family, font size, arbitrary
weight, or arbitrary CSS.

A style statement may be added to the code only when all of these conditions hold:

- style recovery is enabled
- the compatibility profile is `portable-rich`, `style-rich`, or `trusted-local`
- the security profile is not `strict`
- the emitted grammar is Flowchart and the source element maps unambiguously to a generated
  candidate node
- every Mermaid edge line, including the edge being styled, can be mapped exactly in independent
  line order

Source-to-candidate node mapping attempts, in order, content-consistent exact IDs, evidence overlap
with the trusted registry, and unique normalized labels that preserve punctuation. Multiple
matches, normalized-ID collisions, and target collisions fail closed. The evidence index uses
bounded lookup instead of a full source-by-candidate comparison. A bucket in which two or more
generated nodes reference the same evidence is marked ambiguous immediately, without iterating the
candidate. Source style is not guessed for candidates, such as Direct Mermaid, that cannot produce
a generated candidate Scene.

Node fill/border/dashed/thick styles use a vector element only when the actual
`VectorPrimitiveEngine` freshly registered a collision-free contour in the current source block and
the source element bbox has IoU of at least 0.8 with it. A color merely declared by Scene/VLM is not
an authority. If multiple source elements reference the same contour ID, or another engine reuses
the ID, all affected styles are omitted. Edge color/dashed/thick styles likewise accept only a
freshly registered vector line. The two source-relation endpoint bboxes and the corresponding
vector-relation endpoint bboxes must each have IoU of at least 0.8, and arrow flags across the
source, trusted vector, generated Scene, and Mermaid operator must all agree, together with relation
evidence ownership and source-to-Mermaid endpoint mapping. Parallel endpoint pairs, reused line
evidence, non-pixel Scenes, and incomplete Mermaid edge ordering fail closed. Applied source/node
relations, Mermaid link indexes, evidence IDs, and match methods are recorded in `recover_style`
history.

Bold output requires bold `vector_text` evidence freshly registered by the real
`VectorPrimitiveEngine`. A VLM or fixture that self-identifies as the same kind, or collides with an
existing evidence ID, is not trusted. The center of every cited bold span must fall inside the
source-node bbox, and the source label, candidate label, and position-ordered span text must all
match.

Group style is considered only when a generated `SceneGroup` emitted by a typed
Flowchart/Swimlane maps exactly one-to-one to the member set of a source group. A styled contour bbox
freshly registered by the actual `VectorPrimitiveEngine` must have IoU of at least 0.8 with the
source-group bbox, contain every member center, and contain no independent node center outside the
group. If fusion retains the same node as both vector and VLM elements, only a geometry duplicate
whose bbox has IoU of at least 0.8 with the member bbox is ignored. Evidence-ID collisions, multiple
container matches, a missing normalized subgraph declaration, and a non-pixel source group fail
closed. A portable-ID normalization collision is not treated as an exact member mapping, and a
member's own evidence ID or a contour with IoU of at least 0.8 with a member bbox is not promoted to
an outer container. Style matching is skipped when the deterministic work budget calculated from
group, member, node, and vector counts would be exceeded. The applied source/emitted group IDs,
contour evidence ID, and match method are recorded in `recover_style` history. Contours, lines, or
colors self-declared by a VLM or fixture authorize no node, group, or edge style.

The default configuration is `portable-rich + strict`, so style evidence remains in Scene IR but
does not alter automatically published Markdown code. An explicitly permitted combination such as
`style-rich + style-only` appends `style`/`linkStyle` and records a `recover_style` event in
pre-validation repair history. The result must still pass the security scan, `mermaid.parse()`,
rendering, and SVG inspection. The source/emitted IDs, evidence IDs, and match method for every
applied node style are stored in `recover_style` repair history.

An external Markdown consumer may display colors and lines differently depending on its Mermaid
version and theme, so a compatibility warning is recorded. Styles that force normal weight,
raster-only group/lane color, and chart-series color remain follow-up work because common evidence
is not yet connected to safe style attribution. Flat Flowchart groups and Swimlane/BPMN lanes with
a vector container can use the same trusted-group path described above.
