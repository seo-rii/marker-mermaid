# Deterministic Mermaid source repair

`ast_repair.py` is a small, conservative repair layer that runs before validation. It does not
attempt to imitate the Mermaid parser or ask an LLM to rewrite the entire source. It changes only
cases that add no semantics:

- remove a leading Unicode BOM
- remove one Mermaid Markdown fence that wraps the entire input
- add one obviously missing closing quote in an independent Flowchart node declaration
- normalize a Flowchart identifier only when every use is confirmed to be a structural token
- remove completely identical duplicate node declarations
- add a final newline

Each change is stored in the candidate's `repair_history` as an iteration 0 event and includes the
line, before/after values, and reason. If the event budget is exceeded, or if a second pass would
change the result again and therefore show that the repair is not idempotent, no partial result is
applied. Dangling edges, conflicting duplicates, and identifier collisions remain issue warnings;
the repair layer does not guess nodes or edges. If the security scanner rejects the repaired
source, every change is discarded and the original source is left for the normal hard gate to
reject.

The `MermaidAstAdapter` protocol provides a seam for checking parse-to-render round-trip stability
with an external parser such as `mermaid-ast`. The default distribution does not currently include
a separate `mermaid-ast` package adapter. An adapter result never replaces the source or bypasses
security validation or real rendering.
