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
        "click": re.compile(r"^\s*click\s+", re.IGNORECASE),
        "callback": re.compile(r"\b(?:call|callback)\s*\(", re.IGNORECASE),
        "external_url": re.compile(r"(?:https?|ftp|file|data|javascript):", re.IGNORECASE),
        "protocol_relative_url": re.compile(r"[\"'(=]\s*//[^/\s]", re.IGNORECASE),
        "html": re.compile(r"<\s*/?\s*[A-Za-z][^>]*>", re.IGNORECASE),
        "frontmatter": re.compile(r"^\s*(?:---|config\s*:)", re.IGNORECASE),
        "css_import": re.compile(r"@import\b", re.IGNORECASE),
        "remote_icon": re.compile(r"iconify|fa:|logos:", re.IGNORECASE),
    }
    _style_syntax = re.compile(r"^\s*(?:style|classDef|linkStyle)\b", re.IGNORECASE)

    def __init__(self, profile: SecurityProfile = SecurityProfile.STRICT):
        self.profile = profile

    def scan(self, code: str) -> SecurityReport:
        findings: list[SecurityFinding] = []
        for line_number, line in enumerate(code.splitlines(), start=1):
            for rule, pattern in self._always_forbidden.items():
                if pattern.search(line):
                    findings.append(
                        SecurityFinding(
                            rule=rule, line=line_number, message=f"forbidden {rule} syntax"
                        )
                    )
            if self.profile == SecurityProfile.STRICT and self._style_syntax.search(line):
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
