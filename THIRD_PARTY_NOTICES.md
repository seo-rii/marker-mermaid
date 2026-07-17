# Third-Party Notices

marker-mermaid depends on third-party software that remains governed by its own license terms.
The lists below summarize the direct dependency constraints in `pyproject.toml` and the pinned
browser runtime in `src/marker_mermaid/runtime/package-lock.json`. They are provided for
convenience and are not a substitute for the license files shipped by each dependency.

## Direct Python dependencies

| Dependency | Constraint | License identified by upstream package metadata |
| --- | --- | --- |
| [Pillow](https://github.com/python-pillow/Pillow) | `>=10.0` | HPND |
| [Pydantic](https://github.com/pydantic/pydantic) | `>=2.8,<3` | MIT |

## Optional Python dependencies

| Extra | Dependency | Constraint | License identified by upstream package metadata |
| --- | --- | --- | --- |
| `marker` | [marker-pdf](https://github.com/datalab-to/marker) | `==1.10.2` | GPL-3.0-or-later |
| `marker` | [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | `>=1.24,<2` | AGPL-3.0 or Artifex commercial license |
| `vision` | [NumPy](https://github.com/numpy/numpy) | `>=1.26` | BSD-3-Clause and bundled-component licenses |
| `vision` | [opencv-python-headless](https://github.com/opencv/opencv-python) | `>=4.8` | Apache-2.0 |

The `dev` extra also installs pytest, pytest-cov, and Ruff under their respective permissive
licenses. Build front ends and transitive packages are resolved by the user's package manager;
inspect the installed environment for the complete dependency graph and exact license texts.

The Marker 1.10.2 dependency graph constrains Pillow below currently fixed release lines. This is
a known compatibility/security limitation, documented with operating guidance in
[SECURITY.md](SECURITY.md#known-upstream-dependency-constraint); it is not resolved by the license
summary in this file.

## Browser runtime

| Dependency | Pinned version | License |
| --- | --- | --- |
| [Mermaid](https://github.com/mermaid-js/mermaid) | `11.16.0` | MIT |
| [Playwright](https://github.com/microsoft/playwright) | `1.61.1` | Apache-2.0 |

`marker-mermaid install-runtime` installs the locked npm dependency graph and Playwright's
matching Chromium build into a local cache. Chromium and transitive npm packages carry their own
notices in the installed runtime. They are not relicensed by this repository.

## Marker model weights and remote services

The Marker source package and Marker model weights do not use identical terms. Upstream
currently describes its source code as GPL-licensed and its model weights under a modified AI
Pubs Open Rail-M license. LLM providers selected by the operator also impose separate service,
privacy, and data-processing terms. Review those terms before downloading weights or submitting
documents to a remote service.

## No legal advice

This notice records the project's dependency metadata and upstream references; it is not legal
advice. When redistributing an application or container, preserve the dependency license files
and perform a license review for the exact resolved versions and artifacts you ship.
