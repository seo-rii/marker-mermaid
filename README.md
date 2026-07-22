# marker-mermaid

[![License: GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Project status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](docs/spec-coverage.md)

Evidence-aware Mermaid reconstruction for [Marker](https://github.com/datalab-to/marker).

`marker-mermaid` turns diagrams found in PDF figures, pictures, and complex regions into
editable Mermaid candidates while retaining the original image and traceable visual evidence.
Only Mermaid that passes the configured security checks and a real parse-and-render cycle may
be published into generated Markdown.

> [!IMPORTANT]
> This project is an experimental engineering baseline for MMX-001 v0.3 and currently targets
> `marker-pdf==1.10.2`. Reconstruction quality varies by diagram and input quality. Review
> experimental output before relying on it for operational or safety-critical decisions.

## Why marker-mermaid?

Image-to-diagram conversion is not just OCR. A useful reconstruction must recover labels,
geometry, edge direction, grouping, diagram semantics, and enough provenance to explain where
each generated element came from. This project combines multiple signals instead of trusting a
single model response:

- Marker blocks and optional page-level discovery
- PDF vector primitives, contours, lines, arrowheads, and OCR observations
- Type-aware visual priors such as edge maps and overlays
- Typed intermediate representations and deterministic serializers
- Multiple candidates with bounded repair iterations
- Real Mermaid syntax/render validation and post-render SVG inspection
- Reference-free semantic scoring and source-to-output provenance
- A local review workspace with revision history

The original source image is always preserved. A candidate failure never aborts the surrounding
document conversion.

## Project status

The default mode is `extended` and the default publication policy is
`best_effort_validated`. The implementation includes the Phase 1–5 serializer and safety
baseline, but it does not claim complete semantic extraction for every type in the MMX-001
specification.

| Area | Current behavior |
| --- | --- |
| Core structure | Native Flowchart, Sequence, State, Class, ER, Requirement, and Block serializers |
| Architecture | Native Architecture plus explicit portable fallbacks for C4, Deployment, Component, BPMN/Swimlane, and Use-case inputs |
| Planning | Mindmap, Timeline, Gantt, Journey, Kanban, and GitGraph projections |
| Charts | Pie, XY, Quadrant, Sankey, Radar, Treemap, and Venn with numeric/set safety gates |
| Specialized | Packet, Ishikawa, TreeView, Wardley, Cynefin, Railroad, Event Modeling, and ZenUML projections |
| Review | Validated Mermaid/IR edits, provenance overlays, candidate selection, bounded natural-language patches, undo/redo, approval/rejection, and immutable revisions |

See the [specification coverage matrix](docs/spec-coverage.md) for implemented, partial, and
planned behavior. Experimental serializer stability always produces an experimental warning in
automatic Markdown, even when the measured quality grade is A.

## Safety guarantees

- Keeping the original image cannot be disabled.
- Automatically published Mermaid must pass a pre-render security scan,
  `mermaid.parse()`, `mermaid.render()`, and post-render SVG inspection.
- A render result without a non-empty SVG artifact is rejected.
- Final Mermaid source, inspected SVG, and optional runtime PNG are tied together by SHA-256
  validation receipts and process-private authorization seals.
- Mutating validated source, SVG, scores, or policy state causes publication to fail closed.
- The renderer blocks external network access and cleans up the Chromium process group.
- Unknown semantic quality remains grade `U`; syntax success cannot hide missing semantic
  evidence.
- In `extended` mode, automatically generated nodes require at least 80% collision-free
  provenance coverage or the result is routed to review.
- Candidate, type, repair, input, output, and reconstruction-global resource budgets are bounded.

The process-private seal treats in-process engines and plug-ins as trusted code. Run untrusted
Python extensions in a separate process or container. Read the full [security model](docs/security.md)
and report vulnerabilities according to [SECURITY.md](SECURITY.md).

## Requirements

- Python 3.11 or newer
- Node.js 20 or newer
- Linux or macOS; the browser runtime, atomic output, and review store require POSIX process and
  descriptor semantics and fail fast on unsupported platforms
- Optional Marker and vision dependencies for end-to-end PDF reconstruction

Atomic publication uses `renameat2(RENAME_NOREPLACE)` on Linux and
`renameatx_np(RENAME_EXCL)` on macOS. On platforms without a safe no-replace primitive,
marker-mermaid refuses to overwrite an existing sidecar bundle.

The browser runtime pins `mermaid@11.16.0`, `playwright@1.61.1`, and the corresponding Chromium
revision. Its default cache directory is `$XDG_CACHE_HOME/marker-mermaid/runtime`; a development
checkout with `node_modules` or `MARKER_MERMAID_RUNTIME_DIR` can override that location.

## Installation

The project has not published a stable package release. Install a development checkout:

```bash
git clone https://github.com/seo-rii/marker-mermaid.git
cd marker-mermaid
python -m venv .venv
. .venv/bin/activate
pip install -e '.[vision,dev]'
marker-mermaid install-runtime
marker-mermaid doctor
```

The optional `marker` extra is intentionally pinned to the compatibility baseline:

```bash
pip install -e '.[marker,vision,dev]'
```

> [!WARNING]
> As of 2026-07-17, the `marker-pdf==1.10.2` dependency graph constrains Pillow to a release line
> below versions containing current public security fixes. Use the Marker compatibility extra
> only in an isolated environment and do not process untrusted documents with it. See the
> [known upstream dependency constraint](SECURITY.md#known-upstream-dependency-constraint).

Marker source code is GPL-licensed, while Marker model weights have separate upstream terms.
Review the [upstream Marker repository](https://github.com/datalab-to/marker) before downloading
or using those weights.

## Quick start

The CLI is the supported interface during the alpha period. Python modules and constructor-level
integration points may change without a compatibility layer until the public API is stabilized.

Convert a PDF using a Marker-supported LLM service:

The selected service may receive source images, OCR text, and generated visual priors. Review
that provider's data-handling terms and deployment configuration before processing confidential
documents. The offline fixture path below does not call a remote model service.

```bash
marker-mermaid convert input.pdf \
  --output output/document \
  --config examples/config.extended.json \
  --llm-service marker.services.gemini.GoogleGeminiService
```

Reproduce the pipeline and output contract offline with the included fixture:

```bash
marker-mermaid reconstruct examples/flowchart.pbm \
  --fixture examples/flowchart-observation.json \
  --output output/fixture
```

The fixture intentionally uses Korean diagram labels to exercise multilingual reconstruction;
the project documentation and interface guidance are English.

Validate a Mermaid source file:

```bash
marker-mermaid validate diagram.mmd
```

Open the local review workspace:

```bash
marker-mermaid review output/document
```

Run the fixed-corpus release evaluation:

```bash
marker-mermaid evaluate corpus/manifest.json --output output/evaluation
```

The evaluator pins the manifest and every source, ground-truth, and prediction artifact by
SHA-256. Exit codes are `0` for all trusted-runner gates passing, `1` for a gate failure or
missing required evidence, `2` for an invalid manifest/artifact, and `3` for report I/O failure.
See [release evaluation](docs/evaluation.md) for the trust and corpus contracts.

## Output

```text
output/document/
├── document.md
├── document_meta.json
├── images/
│   └── _page_4_Figure_2.jpeg
└── diagrams/
    └── page_4_figure_2/
        ├── manifest.json
        ├── final.mmd
        ├── final.svg
        ├── final.png
        ├── scene-ir.json
        ├── generated-scene-ir.json
        ├── typed-ir.json
        ├── node-id-map.json
        ├── provenance.json
        ├── source-map.json
        ├── scores.json
        ├── review-history.json
        ├── review-state.json
        ├── layout-hints.json
        ├── versions/
        └── alternatives/
```

`final.svg` is required for an automatically published bundle. `final.png`, review state,
layout hints, and several typed/evaluation artifacts are conditional. See the
[output format](docs/output-format.md) for schemas and atomic-write rules.

## Documentation

- [Architecture and processing flow](docs/architecture.md)
- [Candidate discovery](docs/discovery.md)
- [Geometry engine](docs/geometry.md)
- [Type-aware visual priors](docs/visual-priors.md)
- [Page-level diagram detector](docs/page-detector.md)
- [Vector extraction and fusion](docs/vector-fusion.md)
- [Quality scoring and availability](docs/quality.md)
- [Release corpus and MMX-001 gates](docs/evaluation.md)
- [Accessible titles and descriptions](docs/accessibility.md)
- [Typed serializers and fallback contracts](docs/serialization.md)
- [Typed extraction and evaluation scenes](docs/typed-extraction.md)
- [Chart serializers and numeric safety](docs/charts.md)
- [Planning and specialized serializers](docs/specialized-diagrams.md)
- [Deterministic source repair](docs/source-repair.md)
- [Evidence-backed semantic repair](docs/semantic-repair.md)
- [Style recovery](docs/style-recovery.md)
- [Configuration reference](docs/configuration.md)
- [Marker 1.10.2 integration](docs/marker-integration.md)
- [Security model](docs/security.md)
- [Output format](docs/output-format.md)
- [Review workspace](docs/review-workspace.md)
- [Specification coverage and roadmap](docs/spec-coverage.md)
- [Development and testing](docs/development.md)
- [Research references](docs/references.md)
- [Changelog](CHANGELOG.md)

## Contributing

Issues and pull requests are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), follow the
[Code of Conduct](CODE_OF_CONDUCT.md), and keep changes within the documented security and
publication boundaries. Please do not open a public issue for a suspected vulnerability.

## License and acknowledgements

marker-mermaid is distributed under the [GNU General Public License v3.0 only](LICENSE).

This is an independent extension project and is not affiliated with or endorsed by Datalab or
the Mermaid project. Marker, Mermaid, Playwright, Chromium, and other dependencies remain under
their respective licenses. Research and implementation influences are listed in
[docs/references.md](docs/references.md).
