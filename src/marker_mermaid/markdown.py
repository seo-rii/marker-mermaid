"""Markdown fragments and Marker renderer integration helpers."""

from __future__ import annotations

from marker_mermaid.models import ReconstructionResult


def reconstruction_markdown(result: ReconstructionResult, *, show_score: bool = False) -> str:
    selected = result.selected
    if not result.publish or selected is None or not selected.mermaid_code:
        return ""
    warning = ""
    if result.grade in {"B", "C"}:
        message = "일부 요소는 검토가 필요합니다."
        if result.grade == "C":
            message = "의미 정확도가 낮을 수 있으므로 반드시 원본과 대조해 주세요."
        warning = f"> **Experimental reconstruction:** {message}\n"
        if show_score and selected.aggregate_score is not None:
            warning += f"> Quality score: {selected.aggregate_score:.2f}\n"
        warning += "\n"
    return f"{warning}```mermaid\n{selected.mermaid_code.rstrip()}\n```"


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
