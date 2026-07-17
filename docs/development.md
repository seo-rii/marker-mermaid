# Development and testing

## Local verification

```bash
pip install -e '.[vision,dev]'
marker-mermaid install-runtime
ruff check .
pytest
```

Default unit tests use a fake runtime and fixture engines and require no network access. Tests
marked `integration` exercise either real Mermaid in Chromium or the Marker 1.10.2 import. The
real Mermaid tests also block network routes.

Install the `marker` extra only for Marker 1.10.2 compatibility work and keep that environment
isolated from untrusted documents. Its upstream Pillow constraint currently prevents resolving a
release line with all current public fixes; see the root [security policy](../SECURITY.md#known-upstream-dependency-constraint).

## Test layers

- Configuration/models: budgets, original-image invariants, ID/reference integrity, grade boundaries
- Security: malicious `click`, directive, URL, HTML, and style corpora
- Serializers: real Mermaid parse/render and fallback contracts for core, software, chart,
  planning, and specialized serializers
- Pipeline: engine-failure isolation, candidate budgets, deterministic selection
- Sidecars/Markdown: original-first output, manifests and hashes, alternatives
- Marker compatibility: processor order between Reference and BlankPage, and the exact version

## Fixture principles

Fixture JSON uses the VLM observation schema directly. Serializer, validator, and policy
regressions can therefore be reproduced without nondeterministic model responses or API cost.
Synthetic sources use project-created shapes without copyright restrictions. When an external
research dataset is added, record its source license and split information in the fixture
manifest.

## Adding a serializer

1. Confirm that `ALL_TYPES` contains the canonical type.
2. Define required typed-IR values and the rule that unknown values are never invented.
3. Add a deterministic serializer to the relevant `serializers_*.py` family and register a
   result-aware closure in the `serializers.py` registry.
4. Add a real Mermaid parse/render fixture under the strict security profile.
5. For non-native syntax, preserve both the fallback type and original type in metadata and warnings.
6. For numeric diagrams, never invent values when OCR evidence is absent.

## Updating version pins

Upgrade Mermaid and Playwright together, regenerate `package-lock.json`, and run every diagram
grammar and malicious-input test. Treat a Marker version change as a separate compatibility
change that re-examines processor ordering, block image/metadata APIs, and renderer image naming.
