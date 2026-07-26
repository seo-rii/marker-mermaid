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
        "frontmatter": re.compile(r"^\s*(?:---|config\s*:)", re.IGNORECASE),
        "css_import": re.compile(r"@import\b", re.IGNORECASE),
        "remote_icon": re.compile(r"iconify|fa:|logos:", re.IGNORECASE),
    }
    _click_syntax = re.compile(r"^\s*click\s+", re.IGNORECASE)
    _flowchart_header = re.compile(r"^\s*(?:flowchart|graph)\b", re.IGNORECASE)
    _state_header = re.compile(r"^\s*stateDiagram(?:-v2)?\s*$", re.IGNORECASE)
    _state_pseudostate_declaration = re.compile(
        r"^\s*state\s+[A-Za-z_][A-Za-z0-9_]*\s+<<(?:choice|fork|join)>>\s*$",
        re.IGNORECASE,
    )
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
    _max_accessibility_prefix_chars = 128
    _style_syntax = re.compile(r"^\s*(?:style|classDef|linkStyle)\b", re.IGNORECASE)

    def __init__(self, profile: SecurityProfile = SecurityProfile.STRICT):
        self.profile = profile

    @staticmethod
    def _contains_html_tag(line: str) -> bool:
        """Detect complete HTML-like tokens without borrowing a later Mermaid arrow."""

        length = len(line)
        search_from = 0
        while (opening := line.find("<", search_from)) >= 0:
            cursor = opening + 1
            if cursor >= length:
                return False

            # These prefixes are unambiguously markup even when malformed or split
            # across lines. Rejecting them early also keeps comment/PI inspection
            # bounded.
            if line.startswith("<!--", opening) or line.startswith("<?", opening):
                return True
            if line[cursor] == "!":
                declaration_end = cursor + len("!doctype")
                if (
                    line[cursor:declaration_end].casefold() == "!doctype"
                    and (
                        declaration_end == length
                        or line[declaration_end].isspace()
                        or line[declaration_end] == ">"
                    )
                ):
                    return True
                search_from = cursor + 1
                continue

            closing = line[cursor] == "/"
            if closing:
                cursor += 1
            if cursor >= length or not (
                "A" <= line[cursor] <= "Z" or "a" <= line[cursor] <= "z"
            ):
                search_from = cursor if cursor < length and line[cursor] == "<" else cursor + 1
                continue

            # Parse one complete tag candidate. Every cursor advance is retained by
            # the outer scan, so quote-dense or repeated '<name' input stays linear.
            cursor += 1
            while cursor < length and (
                "A" <= line[cursor] <= "Z"
                or "a" <= line[cursor] <= "z"
                or "0" <= line[cursor] <= "9"
                or line[cursor] in "_:-"
            ):
                cursor += 1

            if closing:
                while cursor < length and line[cursor].isspace():
                    cursor += 1
                if cursor < length and line[cursor] == ">":
                    return True
                search_from = cursor if cursor < length and line[cursor] == "<" else cursor + 1
                continue

            if cursor < length and line[cursor] == ">":
                return True
            if cursor < length and line[cursor] == "/":
                cursor += 1
                while cursor < length and line[cursor].isspace():
                    cursor += 1
                if cursor < length and line[cursor] == ">":
                    return True
                search_from = cursor if cursor < length and line[cursor] == "<" else cursor + 1
                continue
            if cursor >= length or not line[cursor].isspace():
                search_from = cursor if cursor < length and line[cursor] == "<" else cursor + 1
                continue

            complete = False
            while cursor < length:
                while cursor < length and line[cursor].isspace():
                    cursor += 1
                if cursor >= length:
                    break
                if line[cursor] == ">":
                    complete = True
                    break
                if line[cursor] == "/":
                    cursor += 1
                    while cursor < length and line[cursor].isspace():
                        cursor += 1
                    if cursor < length and line[cursor] == ">":
                        complete = True
                    break
                if line[cursor].isspace() or line[cursor] in "\x00\"'><=/":
                    break

                cursor += 1
                while (
                    cursor < length
                    and not line[cursor].isspace()
                    and line[cursor] not in "\x00\"'><=/"
                ):
                    cursor += 1
                while cursor < length and line[cursor].isspace():
                    cursor += 1
                if cursor >= length or line[cursor] != "=":
                    continue

                cursor += 1
                while cursor < length and line[cursor].isspace():
                    cursor += 1
                if cursor >= length:
                    break
                if line[cursor] in "\"'":
                    quote = line[cursor]
                    cursor += 1
                    while cursor < length and line[cursor] != quote:
                        cursor += 1
                    if cursor >= length:
                        break
                    cursor += 1
                    continue
                value_start = cursor
                while (
                    cursor < length
                    and not line[cursor].isspace()
                    and line[cursor] != ">"
                    and line[cursor] not in "\"'<="
                ):
                    cursor += 1
                if cursor == value_start or (
                    cursor < length and line[cursor] in "\"'<="
                ):
                    break

            if complete:
                return True
            search_from = cursor if cursor < length and line[cursor] == "<" else cursor + 1
        return False

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
                    if (
                        character == ":"
                        and len(current) <= cls._max_accessibility_prefix_chars
                        and cls._accessibility_text_prefix.fullmatch("".join(current))
                    ):
                        in_accessibility_text = True
                    elif (
                        character == "{"
                        and len(current) <= cls._max_accessibility_prefix_chars
                        and cls._accessibility_block_prefix.fullmatch("".join(current))
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
        first_statement = next(
            (
                line
                for line in code.splitlines()
                if line.strip() and not self._line_comment.match(line)
            ),
            "",
        )
        state_mode = bool(self._state_header.fullmatch(first_statement))
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
            if self._contains_html_tag(line) and not (
                state_mode and self._state_pseudostate_declaration.fullmatch(line)
            ):
                findings.append(
                    SecurityFinding(
                        rule="html",
                        line=line_number,
                        message="forbidden html syntax",
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
