# Review Workspace

`marker-mermaid review <output-directory>` opens conversion sidecars as an editable local workspace. The
default address is `http://127.0.0.1:8765/`. It places the original, latest Mermaid render, provenance bboxes,
issues, and alternative candidates in one comparison view.

```bash
marker-mermaid review output/document
marker-mermaid review output/document --port 9000 --no-open
marker-mermaid review output/document --host 0.0.0.0 --allowed-host review.example.com --no-open
```

## Editing and Validation

The Mermaid editor and Scene IR JSON editor are saved together. A save request includes the current
revision version and SHA-256; if another tab changed it first, the server returns `409 Conflict` and the
latest state must be reloaded. Mermaid code must pass the same strict security scan, `mermaid.parse()`,
`mermaid.render()`, and SVG inspection as an automatically generated candidate. Scene IR must also pass the
`DiagramSceneIR` schema, including bboxes, unique IDs, and group/relation endpoint references. SVG/PNG from a
successful render is saved in the same revision as the code. Provenance is schema-checked for unique
evidence IDs, then its content-addressed snapshot and root manifest hash are saved in the same revision.
Advisory layout hints are revisioned under a closed normalized schema and content-addressed snapshot,
separate from source bboxes. A revision that changes code, IR, provenance, or layout does not inherit
source-based scores automatically and is marked `unscored_user_revision`.

Review reapplies the shared aggregate evidence budget whenever it reads root or content-addressed revision
provenance, accepts a trusted replacement, or creates digest and commit payloads. Raw JSON dictionaries and
existing models become detached canonical snapshots one record at a time. The entire replacement/operation
is rejected if any of these limits is exceeded: 20,000 source-block references including duplicates,
8,000,000 Python characters in source-block IDs, or 8,000,000 full-evidence characters. Exact boundaries are
accepted. Server-created `user_edit` evidence from `add_node`/`add_edge` is checked together with the existing
collection before any IR/Mermaid patch. On failure, no code, IR, history, or provenance file is written, and
the `mmx-review-0.4.1` schema remains unchanged. The manifest digest and JSON parse for root
`provenance.json` use the same bounded byte snapshot rather than separate file reads, so replacing the file
between checks cannot approve different provenance.

Both editors retain their local drafts after a save fails with `422` or another error. On `409`, the latest
server revision's version and digest are loaded without overwriting local drafts, and saving the conflicting
draft is locked. After the user approves discarding the draft with `Reload latest`, the next save uses the
updated version/digest. `Reload latest` replaces the local draft only after another server query returns a
detailed response with the matching ID. A failed conflict refresh preserves both the draft and save lock.
For an ordinary dirty draft, `Discard draft` restores the currently loaded revision. Actions outside the
editors—alternative selection, approval/rejection, undo/redo/restore, and diagram switching—ask for user
confirmation before discarding a dirty draft. A late response from a previous diagram during a switch is
ignored through request-generation and diagram-ID checks.

Initial bootstrap data and the diagram list are summaries, not editing authority. Editors, commands,
candidate selection, structural changes, approval, and rejection remain locked until a detailed bundle has
loaded successfully. A failed detail request can be retried with `Retry load`; mutations are enabled only
after a successful response.

The workspace supports alternative-candidate selection, approval, rejection with a required reason, and
undo/redo. Approval does not trust the success state recorded at save time: it reparses and rerenders current
code with the strict validator and saves a new SVG/PNG in the same revision. An embedding without a validator
rejects approval itself. The first edit creates original revision `versions/r000000.*`; each change records
the user operation and reason in `review-history.json`. Natural-language patches are not reduced to generic
edits: the target and structured before/after values for `reverse_edge`, `relabel_node`, and `group_nodes`
remain in commit history.

`Recent audit history` displays the latest 100 valid entries in this append-only record, newest first. Each
entry summarizes operation, target, source, timestamp, and optional reason; collapsible details show the
before/after structures. Invalid entries are neither executed nor partially inferred; the view reports how
many were ignored. If the history payload itself is invalid, the view marks it unavailable. Every value is
rendered as text, never interpreted as HTML. This bounded display does not truncate or rewrite canonical
`review-history.json`; the complete append-only record remains in the sidecar.

`Restore revision`, next to Undo/Redo, lists only server-validated active-timeline IDs. Selecting one
atomically restores the working copy's code, IR, SVG/PNG, provenance, layout, decision, candidate, and
manifest hashes from that snapshot, and appends a `checkout_revision` audit entry. This is not a read-only
preview: the version increments once. Requests cannot name an arbitrary path, cursor index, or a snapshot
already removed from the active branch. Optimistic version/digest checks run before target-membership checks.
The revision selector is therefore the active timeline that restores the working copy, whereas Recent audit
history reads operation records that remain after branching; the two serve different purposes.

A new edit after restore/undo branches the active timeline after the selected cursor, but existing immutable
snapshots and audit entries remain on disk. The UI warns explicitly before restore. A bundle with no
`final.mmd` because every automatic candidate failed still loads the first bounded alternative as its
editing baseline. The same pre-save validation applies, and `final.mmd` is published only on the first
successful edit.

## Natural-Language Patches

Natural-language input uses neither an external model nor arbitrary code execution. It uses a small
bilingual Korean/English command grammar that accepts explicit node IDs and supports these operations:

- `Reverse the arrow from DB to API.`
- `reverse edge DB -> API`
- `Change the label of node API to Payment approval.`
- `relabel node API to Payment approval`
- `Group the User, API, and DB nodes into one subgraph.`
- `group nodes User, API as Services`

Commands requiring interpretation of the original image—such as spatial, ordinal, or pronoun references—are
rejected without partial modification. Changing diagram type also lacks enough information to regenerate
corresponding code safely, so code and IR must be edited explicitly together or an alternative candidate of
that type must be selected.

## Validated Structure Operations

The workspace's `Validated structure operations` provide a smaller synchronization boundary than free-form
JSON editing. An entity can be selected by clicking a node bbox in the original overlay or by using an ID
selector. The following ten operations are currently supported.

The original image and provenance SVG share the shrink-to-fit canvas actually occupied by the image, not the
entire stage. A normalized Scene uses `0 0 1 1`; a pixel Scene uses a valid `canvas_size`; only a sizeless
pixel Scene uses the decoded image's natural size as its viewBox, and only when the current source URL
matches. The SVG maps the entire Scene canvas affinely to the full source crop without creating a separate
aspect-ratio letterbox. Evidence and node bboxes follow the same Scene-coordinate contract, and an
out-of-canvas bbox is not made clickable. When the source URL changes, the old image/overlay is hidden
immediately and a new image element and request identity are used, preventing a late previous load from
redisplaying current bboxes. A failed or empty source also removes focusable overlay elements.

- `add_node`: requires a safe ID/label, a positive source bbox inside the Scene canvas, and an evidence note.
  The server generates a revision-based `user_edit` evidence ID and adds node `evidence_ids`, a provenance
  snapshot, and a quoted rectangular Mermaid declaration in one transaction. The UI's four bbox fields use
  the original overlay's Scene coordinates, not a Mermaid render position.
- `relabel_node_from_evidence`: accepts a stable node ID and only a provenance evidence ID already attached
  to that node. The evidence must be a unique `ocr_token` or `vector_text` in provenance and attached once to
  exactly one Scene node. The client sends no label, kind, score, or bbox. From evidence text bound to the
  current digest, the server derives a single-line label of at most 200 characters without linguistic
  editing beyond trimming. It changes Scene text and exactly one quoted rectangular Mermaid declaration
  together, retains existing provenance and `evidence_ids`, and records the selected evidence ID in the
  structured audit entry.
- `reconnect_edge`: specifies a stable relation ID and new source/target node IDs. The connector is preserved
  and both representations change together only when the Scene IR relation and an independent Mermaid edge
  line correspond exactly 1:1.
- `set_edge_label`: accepts a stable relation ID and required `label` field. A nonempty single-line string of
  at most 200 characters adds or replaces the label; `null` removes it. Only the Scene relation's `label` and
  the independent Mermaid edge's quoted `|"..."|` segment change together. Endpoints, connector,
  provenance, and `evidence_ids` remain unchanged. If Mermaid would reinterpret characters as syntax, the
  original IR is retained while only the output label is neutralized with explicit compatibility glyphs or
  inactive separators; remaining active URL/directive-like strings are rejected.
- `add_edge`: accepts two explicit node IDs and a required evidence note. The client cannot choose the
  relation/evidence ID, label, type, style, polyline, or confidence. The server creates IDs based on the next
  revision, bbox-free `user_edit` evidence, a `user_edge`/`unknown` unlabeled relation with an arrow at target,
  and a plain `source --> target` line together. Self-loops, parallel ordered pairs, and implicit/duplicate
  endpoint declarations are rejected.
- `delete_edge`: removes the line index and relation together only when the stable relation ID is unique in
  IR, its ordered pair occurs once, and it maps to exactly one plain Mermaid edge line. Evidence is preserved
  so undo can reconnect the relation. Labeled, chained, or non-plain edges and code with `linkStyle` are
  rejected.
- `delete_node`: deletes the node and edges together only when its quoted rectangular node declaration and
  every incident relation/edge correspond exactly. Any additional group/style/class/click/bare-membership
  reference, parallel edge, or chained/labeled edge rejects the entire operation.
- `move_node`: accepts a stable node ID present in the current Scene and a normalized center `[x, y]`. It
  revisions only `layout-hints.json`, leaving source `bbox`, Mermaid code, and provenance unchanged. A
  layout-only revision with the same code digest still increments the version, so a stale drag returns
  `409`.
- `group_nodes`: accepts two or more ungrouped stable node IDs chosen by native multi-select and a required
  label. Order is canonicalized by Scene element order, not client input. The server makes a readable
  `group_<members>` ID when short, otherwise a deterministic hash ID for the same member set. Every member
  must have exactly one quoted rectangular Mermaid declaration and a finite, ordered, in-canvas Scene bbox.
  Only when existing Scene groups correspond 1:1 by ID/members to bounded flat Mermaid subgraphs and all
  memberships are disjoint does the operation add both a bbox union and bare-membership subgraph. Nested or
  unbalanced subgraphs, implicit/duplicate declarations, existing membership, client group IDs, and extra
  fields are rejected.
- `delete_group`: accepts only a stable group ID and removes one flat Mermaid `subgraph ... end` block that
  corresponds exactly to the Scene group, retaining member nodes and edges. It first validates complete
  Scene-group-to-Mermaid membership and grouped-member declarations, then removes only the parser-confirmed
  header/end line slice. Nested, unbalanced, duplicate, or mismatched groups and references to the group ID
  outside the block are rejected.

Structure-operation payloads use closed schemas with operation-specific required fields; unknown fields are
also rejected. Version/digest is checked before interpreting a request against current IR and checked again
under the save lock. A successful result is stored as one revision only after passing the full Scene schema
and strict Mermaid parse/render. On failure, none of IR, code, render, or history changes.

The `Use source-backed label` form lists only OCR/vector evidence uniquely attached to the selected node
under the contract above, in original `evidence_ids` order. A provenance bbox can be selected by pointer or
Enter/Space, and form selection stays synchronized with overlay highlighting. Text evidence without a bbox
is still available in the selector. Shared evidence, an invalid kind, empty text, or evidence equal to the
current label is not a mutation candidate. The server rechecks the same linkage and text contract regardless
of browser filtering.

Before edge add/delete, the entire ordered-endpoint multiset of existing Scene relations must correspond 1:1
to the supported plain-edge multiset in Mermaid. `set_edge_label` rechecks the complete Scene-to-Mermaid
mapping, including endpoints and corresponding labels, for independent edges with no label or exactly one
quoted/bare pipe label. Parallel or chained edges, unsupported connectors, active-looking syntax, and label
mismatches are not partially guessed and edited. An add operation's evidence note is a trimmed single-line
string of 1–4096 characters and is stored in provenance with source-block mapping. Undoing an add removes
relation, line, and evidence together; redo restores the same server ID. Deletion and relabeling do not
change provenance or layout. Relabeling creates no evidence and does not claim existing evidence as the
label's source. All three operations preserve the source image, element bboxes, groups, and unrelated
relations.

The Group form disables and explains already-grouped options and announces selected count through live
status. It cannot be submitted without two nodes and a nonempty label. Success changes only Scene/Code and
the audit record; source element bboxes, relations, provenance, source image, and advisory layout hints stay
unchanged. A Mermaid subgraph can change renderer auto-layout, but this is not source geometry or precise
coordinate editing. The group-deletion form shows stable ID, label, and member count, and repeats in its
confirmation that nodes/edges remain. After successful deletion, elements, relations, provenance, layout,
and source remain unchanged; undo restores the same block and group.

The advisory canvas on the right starts with a deterministic grid based on stable IDs, then overlays only
saved partial hints. It never reuses source bboxes as initial layout. Pointer movement updates only browser
preview and commits once at pointerup; failure restores the saved position. Arrow keys can also move a node
in increments of 0.025. Mermaid Flowchart syntax cannot express arbitrary fixed coordinates, so the hint
does not claim to change exact positions in `final.svg` and is not injected into automatic
`layout_similarity` scoring. Selecting an alternative candidate clears hints; node deletion or direct Scene
editing retains only the intersection with remaining node IDs. Undo/redo restores both layout-artifact
presence and contents.

Selecting a relation line on the canvas reveals source/target endpoint handles. Dropping a handle on another
node makes the browser find the unique nearest node within a fixed radius in screen CSS pixels, then calls
the existing `reconnect_edge` once with only relation ID and both endpoint node IDs. The source handle keeps
the target unchanged; the target handle keeps the source unchanged. Coordinates, polyline, bbox, provenance,
and layout hints are not included. A distance tie, out-of-radius drop, self-loop, existing endpoint, no
movement, pointer cancellation/capture loss, or changed bundle/version/digest/relation endpoint discards only
the preview. The selector-based reconnect form remains available for keyboard and assistive-technology use.
Regardless of input method, an actual save must pass the same 1:1 edge mapping, strict render, optimistic
concurrency, and revision/audit transaction.

### Read-Only Visual Difference

`Difference blend` places the current source and revision's validated `final.png` into the same viewport,
each with aspect ratio preserved through `contain + center`, then applies CSS `difference` blending. The
control is disabled when the PNG is absent or the source URL is not a safe output-image path. It is also
disabled before browser decode if either PNG IHDR axis exceeds 8,192 or total pixels exceed 50 million. It
is off by default, and the slider changes only source-layer intensity in 10% increments. Only when enabled
does it load the actual displayed source image and digest-bound PNG layer while hidden, verify both layers'
URLs and decoded sizes, and reveal those same elements. A stale load event arriving after a bundle/revision
change is discarded when its descriptor key differs. Load failure switches the toggle off and reports the
reason through live status. URLs retain the existing same-origin static allowlist.

Under `bounds-contain-center-v1`, for viewport `(Vw, Vh)` and decoded image `(Iw, Ih)`, each layer is scaled
independently by `scale = min(Vw/Iw, Vh/Ih)` and centered with half the remaining space on each side. Display
size is therefore `(Iw*scale, Ih*scale)`, and offset is
`((Vw-Iw*scale)/2, (Vh-Ih*scale)/2)`.

This is a bounds-normalized aid for manual review. It neither performs nor claims crop, rotation,
translation, feature registration, semantic alignment, or pixel alignment. It creates no server artifact
and changes no quality score, approval, revision, history, provenance, or layout hint. Visual overlap must
therefore not be interpreted as automatic `EdgeAgreement` or `LayoutSimilarity` output.

## HTTP and File Safety

Browser mutations require the CSRF token embedded in page bootstrap and same-origin requests. The server
limits JSON bodies to 1 MB and rejects bundle-ID/path traversal and symlink artifacts. Allowed static
artifacts are opened relative to a directory descriptor with `O_NOFOLLOW`, and the opened file descriptor is
streamed directly, also blocking a symlink-replacement race after validation. A `Host` different from the
listener is rejected before bootstrap/API handling; an additional hostname for wildcard binding must be
specified exactly with `--allowed-host`. A connection that does not complete its HTTP headers closes after
10 seconds by default, returning one of eight worker slots. A validator render exceeding the 16 MB artifact
budget fails before changing any bundle file.

Only `images/*` and each bundle's `final.svg/png` are served over HTTP. Review state, history, and immutable
version files are not exposed directly through API responses or static routes. The diagram list returns at
most 1,000 summaries and deeply validates at most 5,000 bundle candidates. Listing does not read SVG/PNG,
Scene IR, or review history; full digests are checked only when an individual bundle opens. Undo/redo also
removes optional Scene IR/SVG/PNG/provenance/layout files that did not exist in the target revision and
cleans their manifest hashes. Static provenance in a `0.3` review timeline is pinned to a validated legacy
digest on first mutation/undo; immutable historical snapshots are not rewritten. Bundle summaries and
detail reads take the same descriptor lock as writers, so a reader cannot observe a partially replaced set
of files. Before the first rename, each multi-file commit persists a bounded roll-forward journal containing
the staged paths and SHA-256 digests. The next locked read completes an interrupted transaction, verifies
every staged or already-replaced artifact, fsyncs the directories, and removes the journal. A journal that
cannot be verified is surfaced in the diagram list as an errored bundle instead of silently hiding it.

The review server is not an authentication system. Loopback binding is recommended. Binding to a
non-loopback host lets other users on the same network view the workspace; never expose it to a public
network without a separate authenticated reverse proxy.

The review workspace currently supports Linux and macOS. Startup fails with an explicit platform error on
systems without the required POSIX directory-descriptor, no-follow, flock, and process-group semantics.
HTTP `HEAD` is rejected after the same Host validation as other requests; it cannot bypass the static
artifact allowlist to probe private review files.

## Current Limitations

The workspace provides a source-sized provenance/node overlay, bounds-normalized read-only difference blend,
active-timeline revision restore, JSON editor, source-anchored node addition, advisory node drag-and-drop,
ID-based edge reconnection/node deletion, and canvas endpoint dragging. It does not yet force actual
coordinates in the Mermaid render. Version restore is available through the undo/redo active timeline;
audit browsing is a bounded newest-first view of the canonical log. Previewing snapshots removed by a branch
and free-form VLM-based commands remain future work.
