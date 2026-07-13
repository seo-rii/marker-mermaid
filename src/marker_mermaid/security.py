"""Fail-closed Mermaid source scanner."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from marker_mermaid.config import SecurityProfile


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    rule: str
    line: int
    message: str


@dataclass(slots=True)
class SecurityReport:
    safe: bool
    findings: list[SecurityFinding] = field(default_factory=list)


class MermaidSecurityScanner:
    """Reject Mermaid features that can trigger external or active behavior."""

    _always_forbidden = {
        "directive": re.compile(r"%%\s*\{", re.IGNORECASE),
        "callback": re.compile(r"\b(?:call|callback)\s*\(", re.IGNORECASE),
        "external_url": re.compile(r"(?:https?|ftp|file|data|javascript):", re.IGNORECASE),
        "protocol_relative_url": re.compile(r"[\"'(=]\s*//[^/\s]", re.IGNORECASE),
        "html": re.compile(r"<\s*/?\s*[A-Za-z][^>]*>", re.IGNORECASE),
        "frontmatter": re.compile(r"^\s*(?:---|config\s*:)", re.IGNORECASE),
        "css_import": re.compile(r"@import\b", re.IGNORECASE),
        "remote_icon": re.compile(r"iconify|fa:|logos:", re.IGNORECASE),
    }
    _click_syntax = re.compile(r"^\s*click\s+", re.IGNORECASE)
    _flowchart_header = re.compile(r"^\s*(?:flowchart|graph)\b", re.IGNORECASE)
    _line_comment = re.compile(r"^\s*%%")
    _accessibility_text_prefix = re.compile(r"\s*acc(?:Title|Descr)\s*:\s*", re.IGNORECASE)
    _accessibility_block_prefix = re.compile(r"\s*accDescr\s*\{\s*", re.IGNORECASE)
    _quoted_flowchart_label_prefix = re.compile(
        r"""
        ^\s*
        (?:subgraph\s+[\w-]+\s*
          (?:\[{1,2}|\({1,3}|\{{1,2}|>|\(\[|\[\(|\[/|\[\\)
          |(?!(?:flowchart|graph|subgraph|direction|class|classDef|style|linkStyle|click|end)\b)
           (?:[\w-]+\s*(?:\[{1,2}|\({1,3}|\{{1,2}|>|\(\[|\[\(|\[/|\[\\)
             |.*(?:[-=.~ox<>]{2,}|&)\s*(?:\|[^|\r\n]*\|\s*)?[\w-]+\s*
              (?:\[{1,2}|\({1,3}|\{{1,2}|>|\(\[|\[\(|\[/|\[\\)
             |(?:.*(?:[-=.~ox<>]{2,}|&)\s*)?[\w-]+\s*@\{
              [^{};\r\n]*\blabel\s*:\s*))
        $
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    # Longer one-line prefixes fail closed instead of making quote-dense input quadratic.
    _max_quoted_label_prefix_chars = 1_024
    _style_syntax = re.compile(r"^\s*(?:style|classDef|linkStyle)\b", re.IGNORECASE)

    def __init__(self, profile: SecurityProfile = SecurityProfile.STRICT):
        self.profile = profile

    @classmethod
    def _statement_segments(cls, code: str) -> list[tuple[int, str]]:
        """Split statements outside recognized Flowchart quoted-label text."""

        segments: list[tuple[int, str]] = []
        first_statement = next(
            (
                line
                for line in code.splitlines()
                if line.strip() and not cls._line_comment.match(line)
            ),
            "",
        )
        flowchart_mode = bool(cls._flowchart_header.match(first_statement))
        current: list[str] = []
        segment_line = 1
        in_double_quote = False
        in_accessibility_text = False
        in_accessibility_block = False
        lines = code.splitlines(keepends=True)
        for line_number, raw_line in enumerate(lines, start=1):
            content = raw_line.rstrip("\r\n")
            line_ending = raw_line[len(content) :]
            if (
                not in_double_quote
                and not in_accessibility_block
                and cls._line_comment.match(content)
            ):
                segment_line = line_number + 1
                continue
            for character in content:
                if in_accessibility_block:
                    current.append(character)
                    if character == "}":
                        in_accessibility_block = False
                elif in_accessibility_text:
                    current.append(character)
                elif (
                    flowchart_mode
                    and character == '"'
                    and (
                        in_double_quote
                        or (
                            len(current) <= cls._max_quoted_label_prefix_chars
                            and next(
                                (
                                    prefix_character
                                    for prefix_character in reversed(current[-32:])
                                    if not prefix_character.isspace()
                                ),
                                "",
                            )
                            in "[({>/\\:"
                            and cls._quoted_flowchart_label_prefix.fullmatch("".join(current))
                        )
                    )
                ):
                    in_double_quote = not in_double_quote
                    current.append(character)
                elif character == ";" and not in_double_quote:
                    segments.append((segment_line, "".join(current)))
                    current = []
                    segment_line = line_number
                else:
                    current.append(character)
                    if character == ":" and cls._accessibility_text_prefix.fullmatch(
                        "".join(current)
                    ):
                        in_accessibility_text = True
                    elif character == "{" and cls._accessibility_block_prefix.fullmatch(
                        "".join(current)
                    ):
                        in_accessibility_block = True
            if in_double_quote or in_accessibility_block:
                current.append(line_ending)
            else:
                segments.append((segment_line, "".join(current)))
                current = []
                segment_line = line_number + 1
            in_accessibility_text = False
        if current:
            segments.append((segment_line, "".join(current)))
        return segments

    def scan(self, code: str) -> SecurityReport:
        findings: list[SecurityFinding] = []
        statements_by_line: dict[int, list[str]] = {}
        for line_number, statement in self._statement_segments(code):
            statements_by_line.setdefault(line_number, []).append(statement)
        for line_number, line in enumerate(code.splitlines(), start=1):
            for rule, pattern in self._always_forbidden.items():
                if pattern.search(line):
                    findings.append(
                        SecurityFinding(
                            rule=rule, line=line_number, message=f"forbidden {rule} syntax"
                        )
                    )
            statements = statements_by_line.get(line_number, [])
            if any(self._click_syntax.search(statement) for statement in statements):
                findings.append(
                    SecurityFinding(
                        rule="click", line=line_number, message="forbidden click syntax"
                    )
                )
            if self.profile == SecurityProfile.STRICT and any(
                self._style_syntax.search(statement) for statement in statements
            ):
                findings.append(
                    SecurityFinding(
                        rule="style_syntax",
                        line=line_number,
                        message="style syntax is disabled by the strict security profile",
                    )
                )
        if "\x00" in code:
            findings.append(
                SecurityFinding(rule="nul_byte", line=0, message="NUL byte is forbidden")
            )
        return SecurityReport(safe=not findings, findings=findings)
