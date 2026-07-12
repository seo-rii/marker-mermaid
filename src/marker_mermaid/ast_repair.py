"""Bounded, security-preserving Mermaid source repair.

This module deliberately implements only a small lexical/structural subset.  It
does **not** pretend to be a Mermaid parser and never invents nodes, edges,
labels, or diagram types.  A real AST implementation can be supplied through
``MermaidAstAdapter``; the adapter is used only to check that parse/render is
stable and never to replace the repaired source.  Runtime parse, render, and
security validation remain mandatory after this module runs.

Automatic structural edits are limited to generated-style flowcharts whose
node declarations and edges can be recognized without guessing.  Anything
ambiguous is returned as a diagnostic for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from marker_mermaid.security import MermaidSecurityScanner


class MermaidAstAdapter(Protocol):
    """Optional seam for a mermaid-ast (or equivalent) implementation."""

    def parse(self, source: str) -> Any: ...

    def render(self, ast: Any) -> str: ...


@dataclass(frozen=True, slots=True)
class SourceRepairEvent:
    """One accepted, semantics-preserving source edit."""

    operation: str
    line: int | None
    before: str
    after: str
    reason: str


@dataclass(frozen=True, slots=True)
class SourceIssue:
    """A condition that was reported but not guessed at or silently removed."""

    code: str
    message: str
    line: int | None = None
    identifiers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoundTripCheck:
    attempted: bool
    parsed: bool
    stable: bool
    error: str | None = None


@dataclass(slots=True)
class SourceRepairResult:
    source: str
    events: list[SourceRepairEvent] = field(default_factory=list)
    issues: list[SourceIssue] = field(default_factory=list)
    budget: int = 0
    budget_used: int = 0
    budget_exhausted: bool = False
    idempotent: bool = False
    security_preserved: bool = True
    round_trip: RoundTripCheck = field(
        default_factory=lambda: RoundTripCheck(attempted=False, parsed=False, stable=False)
    )

    @property
    def changed(self) -> bool:
        return bool(self.events)


_FENCE = re.compile(
    r"\A[ \t\r\n]*```(?:mermaid)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t\r\n]*\Z",
    re.IGNORECASE | re.DOTALL,
)
_FLOW_HEADER = re.compile(r"^\s*(?:flowchart|graph)\b", re.IGNORECASE)
_NODE = re.compile(
    r'^(?P<indent>\s*)(?P<id>[^\s\[\](){}]+)(?P<body>\s*\[\s*"(?:[^"\\]|\\.)*"\s*\])(?P<trail>\s*(?:%%.*)?)$'
)
_MISSING_QUOTE = re.compile(
    r'^(?P<prefix>\s*[^\s\[\](){}]+\s*\[\s*")(?P<label>[^"\]\n]*)\](?P<trail>\s*(?:%%.*)?)$'
)
_EDGE = re.compile(
    r"^(?P<indent>\s*)(?P<source>[^\s\[\](){}]+)\s+"
    r"(?P<connector><-->|-->|---|-.->|==>|--x|--o|--?>\|[^|\n]*\|)\s+"
    r"(?P<target>[^\s\[\](){}]+)(?P<trail>\s*(?:%%.*)?)$"
)
_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_identifier(value: str, fallback: str = "node") -> str:
    """Return the same portable identifier form used by deterministic serializers."""

    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = f"n_{normalized}"
    return normalized


class DeterministicMermaidRepair:
    """Apply a finite set of conservative repairs and produce review diagnostics."""

    def __init__(
        self,
        *,
        event_budget: int = 8,
        max_source_chars: int = 1_000_000,
        max_lines: int = 20_000,
        security_scanner: MermaidSecurityScanner | None = None,
        ast_adapter: MermaidAstAdapter | None = None,
    ) -> None:
        if event_budget < 0:
            raise ValueError("event_budget must be non-negative")
        if max_source_chars < 1 or max_lines < 1:
            raise ValueError("source limits must be positive")
        self.event_budget = event_budget
        self.max_source_chars = max_source_chars
        self.max_lines = max_lines
        self.security_scanner = security_scanner or MermaidSecurityScanner()
        self.ast_adapter = ast_adapter

    def repair(self, source: str) -> SourceRepairResult:
        """Repair ``source`` without adding semantics or relaxing the security profile."""

        if len(source) > self.max_source_chars or source.count("\n") + 1 > self.max_lines:
            return SourceRepairResult(
                source=source,
                issues=[
                    SourceIssue(
                        code="source_limit_exceeded",
                        message="source exceeds the bounded repair input limits",
                    )
                ],
                budget=self.event_budget,
                idempotent=True,
            )

        repaired, events, exhausted = self._repair_once(source, self.event_budget)
        post_report = self.security_scanner.scan(repaired)
        if not post_report.safe:
            findings = ", ".join(
                f"{finding.rule}@{finding.line}" for finding in post_report.findings
            )
            return SourceRepairResult(
                source=source,
                issues=[
                    SourceIssue(
                        code="security_guard_rejected",
                        message=f"repaired source failed the configured security guard: {findings}",
                    )
                ],
                budget=self.event_budget,
                budget_exhausted=exhausted,
                idempotent=True,
                security_preserved=False,
            )

        second, _, _ = self._repair_once(repaired, self.event_budget)
        issues = self._diagnose(repaired)
        if exhausted:
            issues.append(
                SourceIssue(
                    code="repair_budget_exhausted",
                    message="additional deterministic repairs require another bounded pass",
                )
            )
        return SourceRepairResult(
            source=repaired,
            events=events,
            issues=issues,
            budget=self.event_budget,
            budget_used=len(events),
            budget_exhausted=exhausted,
            idempotent=second == repaired,
            security_preserved=True,
            round_trip=self._round_trip(repaired),
        )

    def _repair_once(self, source: str, budget: int) -> tuple[str, list[SourceRepairEvent], bool]:
        events: list[SourceRepairEvent] = []
        exhausted = False

        def accept(event: SourceRepairEvent) -> bool:
            nonlocal exhausted
            if len(events) >= budget:
                exhausted = True
                return False
            events.append(event)
            return True

        current = source
        if current.startswith("\ufeff"):
            event = SourceRepairEvent(
                operation="remove_bom",
                line=1,
                before="\ufeff",
                after="",
                reason="a leading Unicode BOM is not Mermaid syntax",
            )
            if accept(event):
                current = current[1:]

        fenced = _FENCE.fullmatch(current)
        if fenced and not any(
            line.lstrip().startswith("```") for line in fenced.group("body").splitlines()
        ):
            body = fenced.group("body")
            event = SourceRepairEvent(
                operation="unwrap_markdown_fence",
                line=1,
                before=current,
                after=body,
                reason="the entire input is a Mermaid Markdown code fence",
            )
            if accept(event):
                current = body

        lines = current.splitlines(keepends=True)
        if self._is_flowchart(current):
            for index, line in enumerate(lines):
                content, ending = self._line_parts(line)
                match = _MISSING_QUOTE.fullmatch(content)
                if not match:
                    continue
                fixed = f'{match.group("prefix")}{match.group("label")}"]{match.group("trail")}'
                event = SourceRepairEvent(
                    operation="close_unambiguous_label_quote",
                    line=index + 1,
                    before=content,
                    after=fixed,
                    reason="a standalone bracket node has one unclosed double quote",
                )
                if accept(event):
                    lines[index] = fixed + ending
                else:
                    break
            current = "".join(lines)
            current, identifier_events, identifier_exhausted = self._normalize_identifiers(
                current, budget - len(events)
            )
            events.extend(identifier_events)
            exhausted = exhausted or identifier_exhausted
            current, duplicate_events, duplicate_exhausted = self._remove_exact_duplicates(
                current, budget - len(events)
            )
            events.extend(duplicate_events)
            exhausted = exhausted or duplicate_exhausted

        if current and not current.endswith("\n"):
            event = SourceRepairEvent(
                operation="add_terminal_newline",
                line=current.count("\n") + 1,
                before="",
                after="\n",
                reason="deterministic Mermaid sidecars end with one line terminator",
            )
            if accept(event):
                current += "\n"

        return current, events, exhausted

    @staticmethod
    def _line_parts(line: str) -> tuple[str, str]:
        if line.endswith("\r\n"):
            return line[:-2], "\r\n"
        if line.endswith("\n"):
            return line[:-1], "\n"
        return line, ""

    @staticmethod
    def _is_flowchart(source: str) -> bool:
        return any(
            _FLOW_HEADER.match(line)
            for line in source.splitlines()
            if line.strip() and not line.lstrip().startswith("%%")
        )

    def _normalize_identifiers(
        self, source: str, budget: int
    ) -> tuple[str, list[SourceRepairEvent], bool]:
        lines = source.splitlines(keepends=True)
        declarations: dict[str, list[tuple[int, re.Match[str]]]] = {}
        edge_matches: list[tuple[int, re.Match[str]]] = []
        for index, line in enumerate(lines):
            content, _ = self._line_parts(line)
            if node := _NODE.fullmatch(content):
                declarations.setdefault(node.group("id"), []).append((index, node))
            if edge := _EDGE.fullmatch(content):
                edge_matches.append((index, edge))

        occupied = set(declarations)
        rename: dict[str, str] = {}
        for raw in declarations:
            if _VALID_IDENTIFIER.fullmatch(raw):
                continue
            normalized = normalize_identifier(raw)
            if normalized in occupied or normalized in rename.values():
                continue
            rename[raw] = normalized

        events: list[SourceRepairEvent] = []
        exhausted = False
        for raw, normalized in rename.items():
            if len(events) >= budget:
                exhausted = True
                continue
            # Only rewrite identifiers whose every exact appearance is a recognized
            # declaration or simple edge endpoint.  Labels/comments stay untouched.
            recognized_count = len(declarations[raw]) + sum(
                int(edge.group("source") == raw) + int(edge.group("target") == raw)
                for _, edge in edge_matches
            )
            exact_count = len(
                re.findall(rf"(?<![A-Za-z0-9_]){re.escape(raw)}(?![A-Za-z0-9_])", source)
            )
            if recognized_count != exact_count:
                continue

            for index, node in declarations[raw]:
                content, ending = self._line_parts(lines[index])
                lines[index] = (
                    f"{node.group('indent')}{normalized}{node.group('body')}"
                    f"{node.group('trail')}{ending}"
                )
            for index, original_edge in edge_matches:
                content, ending = self._line_parts(lines[index])
                edge = _EDGE.fullmatch(content)
                if edge is None:
                    continue
                original_source = original_edge.group("source")
                original_target = original_edge.group("target")
                source_id = normalized if original_source == raw else edge.group("source")
                target_id = normalized if original_target == raw else edge.group("target")
                if source_id == edge.group("source") and target_id == edge.group("target"):
                    continue
                lines[index] = (
                    f"{edge.group('indent')}{source_id} {edge.group('connector')} "
                    f"{target_id}{edge.group('trail')}{ending}"
                )
            events.append(
                SourceRepairEvent(
                    operation="normalize_identifier",
                    line=declarations[raw][0][0] + 1,
                    before=raw,
                    after=normalized,
                    reason="all uses are structural tokens and the visible label is explicit",
                )
            )
            source = "".join(lines)
        return "".join(lines), events, exhausted

    def _remove_exact_duplicates(
        self, source: str, budget: int
    ) -> tuple[str, list[SourceRepairEvent], bool]:
        lines = source.splitlines(keepends=True)
        first_by_id: dict[str, tuple[str, int]] = {}
        remove: set[int] = set()
        events: list[SourceRepairEvent] = []
        exhausted = False
        for index, line in enumerate(lines):
            content, _ = self._line_parts(line)
            match = _NODE.fullmatch(content)
            if not match:
                continue
            node_id = match.group("id")
            signature = f"{match.group('body')}{match.group('trail').strip()}"
            previous = first_by_id.get(node_id)
            if previous is None:
                first_by_id[node_id] = (signature, index)
                continue
            if previous[0] != signature:
                continue
            if len(events) >= budget:
                exhausted = True
                continue
            remove.add(index)
            events.append(
                SourceRepairEvent(
                    operation="remove_exact_duplicate_node",
                    line=index + 1,
                    before=content,
                    after="",
                    reason=f"identical declaration already appears on line {previous[1] + 1}",
                )
            )
        repaired = "".join(line for index, line in enumerate(lines) if index not in remove)
        return repaired, events, exhausted

    def _diagnose(self, source: str) -> list[SourceIssue]:
        if not self._is_flowchart(source):
            return []
        declared: dict[str, tuple[str, int]] = {}
        issues: list[SourceIssue] = []
        edges: list[tuple[int, str, str]] = []
        for index, line in enumerate(source.splitlines(), start=1):
            if node := _NODE.fullmatch(line):
                node_id = node.group("id")
                if not _VALID_IDENTIFIER.fullmatch(node_id):
                    issues.append(
                        SourceIssue(
                            code="nonportable_identifier",
                            message=(
                                f"node {node_id!r} was not normalized because one or more "
                                "uses were ambiguous or collided"
                            ),
                            line=index,
                            identifiers=(node_id,),
                        )
                    )
                previous = declared.get(node_id)
                if previous is not None:
                    code = (
                        "duplicate_node_declaration"
                        if previous[0] == node.group("body")
                        else "conflicting_node_declaration"
                    )
                    issues.append(
                        SourceIssue(
                            code=code,
                            message=f"node {node_id!r} was already declared on line {previous[1]}",
                            line=index,
                            identifiers=(node_id,),
                        )
                    )
                else:
                    declared[node_id] = (node.group("body"), index)
            if edge := _EDGE.fullmatch(line):
                edges.append((index, edge.group("source"), edge.group("target")))

        if declared:
            for line, source_id, target_id in edges:
                missing = tuple(
                    node_id for node_id in (source_id, target_id) if node_id not in declared
                )
                if missing:
                    issues.append(
                        SourceIssue(
                            code="unresolved_edge_endpoint",
                            message="edge endpoint has no explicit node declaration",
                            line=line,
                            identifiers=missing,
                        )
                    )
        return issues

    def _round_trip(self, source: str) -> RoundTripCheck:
        if self.ast_adapter is None:
            return RoundTripCheck(attempted=False, parsed=False, stable=False)
        try:
            first_ast = self.ast_adapter.parse(source)
            first_render = self.ast_adapter.render(first_ast)
            second_ast = self.ast_adapter.parse(first_render)
            second_render = self.ast_adapter.render(second_ast)
        except Exception as exc:  # adapters are an untrusted optional boundary
            return RoundTripCheck(
                attempted=True,
                parsed=False,
                stable=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        return RoundTripCheck(
            attempted=True,
            parsed=True,
            stable=first_render == second_render,
        )
