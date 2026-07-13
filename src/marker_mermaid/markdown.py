"""Markdown fragments and Marker renderer integration helpers."""

from __future__ import annotations

import re

from marker_mermaid.models import AuthorizedPublicationSnapshot, ReconstructionResult


def reconstruction_markdown_from_snapshot(
    snapshot: AuthorizedPublicationSnapshot | None,
    *,
    show_score: bool = False,
    show_warning: bool = True,
) -> str:
    """Render one already-authorized immutable publication snapshot."""

    if snapshot is None or not snapshot.has_trusted_values():
        return ""
    code = snapshot.mermaid_code
    if not code.endswith("\n"):
        return ""
    longest_backtick_run = max(
        (len(match.group(0)) for match in re.finditer(r"`+", code)),
        default=0,
    )
    fence = "`" * max(3, longest_backtick_run + 1)
    warning = ""
    if show_warning and snapshot.grade != "A":
        message = "일부 요소는 검토가 필요합니다."
        if snapshot.grade == "C":
            message = "의미 정확도가 낮을 수 있으므로 반드시 원본과 대조해 주세요."
        elif snapshot.grade in {"D", "U"}:
            message = "자동 게시 대상이 아닌 낮은 품질 결과입니다. 원본과 반드시 대조해 주세요."
        warning = f"> **Experimental reconstruction:** {message}\n"
        if show_score and snapshot.aggregate_score is not None:
            warning += f"> Quality score: {snapshot.aggregate_score:.2f}\n"
        warning += "\n"
    return f"{warning}{fence}mermaid\n{code}{fence}"


def reconstruction_markdown(
    result: ReconstructionResult,
    *,
    show_score: bool = False,
    show_warning: bool = True,
) -> str:
    return reconstruction_markdown_from_snapshot(
        result.authorized_publication_snapshot(),
        show_score=show_score,
        show_warning=show_warning,
    )


def standalone_document_markdown(
    result: ReconstructionResult,
    *,
    image_path: str,
    alt_text: str = "원본 다이어그램",
    show_score: bool = True,
) -> str:
    original = f"![{alt_text}]({image_path})"
    reconstruction = reconstruction_markdown(result, show_score=show_score)
    return original + (f"\n\n{reconstruction}" if reconstruction else "") + "\n"
