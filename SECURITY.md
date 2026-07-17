# Security Policy

## Supported versions

marker-mermaid is currently an alpha project. Security fixes are made on the latest commit of
the default branch and included in the next release. Older commits and unpublished development
snapshots do not receive backported fixes.

| Version | Supported |
| --- | --- |
| Latest `main` / current `0.1.x` source | Yes |
| Older development snapshots | No |

## Reporting a vulnerability

Please do not disclose suspected vulnerabilities in a public issue, discussion, pull request,
or other public channel.

Email `me@seorii.page` with the subject **marker-mermaid security report**. Include, when
possible:

- The affected version or commit
- A minimal reproduction or proof of concept
- The expected and observed behavior
- The security impact and any known prerequisites
- Whether the issue is already public or under active exploitation
- A safe way to contact you for follow-up

Do not include real credentials, confidential documents, or personal data in a report. Use a
synthetic PDF or the smallest redacted artifact that reproduces the issue.

The maintainer will acknowledge receipt, investigate, coordinate remediation and disclosure,
and credit reporters who want public attribution. Response time is best effort while the project
is in alpha; no service-level agreement is offered.

## Security scope

Reports are especially useful for:

- Mermaid or SVG content escaping the configured security profile
- External network access during validation or rendering
- Path traversal, unsafe sidecar publication, or overwrite races
- Review workspace request forgery, cross-origin access, or unsafe file serving
- Chromium process leaks or sandbox boundary bypasses
- Integrity failures between validated Mermaid, rendered artifacts, scores, and publication
- Secrets or private source data written to logs or unintended output files
- Resource-limit bypasses that cause unbounded CPU, memory, storage, or model calls

Reconstruction inaccuracies without a security impact should be filed as ordinary bugs. The
implementation's threat model, trust boundaries, and validation stages are documented in
[docs/security.md](docs/security.md).

## Data handling reminder

marker-mermaid can pass source images, OCR text, and visual priors to the Marker LLM service
selected by the operator. Data handling therefore depends on that provider and its configuration.
Never submit confidential documents to a remote service unless its terms and deployment meet
your requirements. Offline fixture reconstruction does not require an external model service.

## Known upstream dependency constraint

As of 2026-07-17, the compatibility baseline `marker-pdf==1.10.2` requires
`Pillow>=10.1.0,<11.0.0`. Public Pillow advisories include fixes available only in newer release
lines; for example, [GHSA-wjx4-4jcj-g98j](https://github.com/python-pillow/Pillow/security/advisories/GHSA-wjx4-4jcj-g98j)
is fixed in Pillow 12.2.0. No dependency version can simultaneously satisfy that fix and the
Marker 1.10.2 constraint.

The Marker compatibility environment may also include other transitive advisories. Until the
Marker baseline is upgraded and requalified, treat it as a compatibility/test environment:

- Do not process untrusted or attacker-controlled documents.
- Run conversion in a disposable, least-privilege process or container without secrets or network
  access beyond the explicitly selected model service.
- Keep fixture-only reconstruction and development on the base/vision installation when Marker is
  not required.
- Run a current dependency audit for the exact environment before deployment.

Publishing this source repository does not distribute Marker, its model weights, Pillow, or the
other optional packages. This limitation nevertheless applies to users who install the `marker`
extra, so it is tracked openly rather than treated as a resolved security posture.
