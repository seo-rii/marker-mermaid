# Contributing to marker-mermaid

Thank you for helping improve marker-mermaid. Bug reports, documentation corrections, test
fixtures, and focused pull requests are welcome.

This project is an experimental reconstruction extension with strict publication boundaries.
Changes that produce more Mermaid output must preserve the original image and may not weaken
security, validation, evidence, or resource-budget gates.

## Before you start

- Search existing issues and pull requests for related work.
- Open an issue before a large architectural change or a new external dependency.
- Report vulnerabilities privately according to [SECURITY.md](SECURITY.md).
- Follow the [Code of Conduct](CODE_OF_CONDUCT.md).
- Do not contribute confidential PDFs, proprietary diagrams, credentials, or personal data.

## Development setup

Requirements are Python 3.11+ and Node.js 20+.

```bash
git clone https://github.com/seo-rii/marker-mermaid.git
cd marker-mermaid
python -m venv .venv
. .venv/bin/activate
pip install -e '.[vision,dev]'
marker-mermaid install-runtime
marker-mermaid doctor
```

For work that does not need OpenCV, `pip install -e '.[dev]'` is sufficient. The real Mermaid
integration tests need the pinned browser runtime installed by `install-runtime`.

Install `pip install -e '.[marker,vision,dev]'` only in an isolated environment when working on
the Marker 1.10.2 adapter. That compatibility graph currently constrains Pillow below releases
containing public security fixes; see [SECURITY.md](SECURITY.md#known-upstream-dependency-constraint).
Do not use private or untrusted PDFs as development fixtures.

## Making a change

1. Keep the patch focused and update documentation with behavior changes.
2. Add tests that reproduce a bug or define the new contract.
3. Preserve typed IR, scene IR, provenance, and sidecar compatibility unless the change clearly
   documents a migration.
4. Never bypass the security scanner, real parse/render checks, SVG inspection, publication
   receipts, or configured budgets to make a fixture pass.
5. Treat experimental Mermaid syntax as untrusted input and keep its fallback explicit.
6. Use synthetic, redistributable, and privacy-safe fixtures.

When adding a serializer:

- Register its semantic type and emitted grammar deliberately.
- Define the typed extraction contract and safe fallback.
- Add syntax, semantic, terminal-text, malformed-input, and security tests.
- Document loss disclosure and accessibility behavior.
- Verify the result with the real Mermaid runtime when the grammar reaches publication.

See [docs/development.md](docs/development.md),
[docs/serialization.md](docs/serialization.md), and
[docs/typed-extraction.md](docs/typed-extraction.md) for the detailed contracts.

## Tests and checks

Run the focused tests for the code you changed, then the full suite before requesting review:

```bash
pytest -q
ruff check .
```

Run browser-backed Mermaid checks when validation, serialization, runtime, Markdown publication,
or review behavior changes:

```bash
pytest -q -m integration
```

If Marker is installed, compatibility coverage can be exercised with the Marker integration
tests. Tests that need optional dependencies should skip clearly when those dependencies are not
available rather than silently weakening an assertion.

## Documentation style

- Write user-facing documentation in English.
- Prefer precise statements about current behavior over roadmap promises.
- Mark incomplete behavior as partial, experimental, or planned.
- Keep commands copy-pasteable and relative links valid.
- Explain the security or evidence boundary when documenting a fallback.
- Do not add badges for services or releases that do not exist.

## Pull requests

A pull request should include:

- A concise problem statement and solution summary
- Tests performed and their results
- Compatibility, security, output-schema, and migration notes when applicable
- Screenshots only when they materially explain review-workspace behavior
- No unrelated generated files, virtual environments, caches, model weights, or output bundles

By submitting a contribution, you agree that it is your original work (or that you have the right
to submit it) and that it will be distributed under this repository's
[GPL-3.0-only license](LICENSE).
