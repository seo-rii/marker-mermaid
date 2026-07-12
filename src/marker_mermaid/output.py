"""Save Markdown, original images, metadata, and diagram bundles."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from marker_mermaid.config import MermaidConfig
from marker_mermaid.models import ReconstructionResult
from marker_mermaid.sidecars import SidecarStore


def save_document_output(
    *,
    output_dir: str | Path,
    filename: str,
    markdown: str,
    images: dict[str, Image.Image],
    metadata: dict,
    reconstructions: list[ReconstructionResult],
    config: MermaidConfig | None = None,
) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise ValueError("filename must be a single safe path component")
    image_dir = root / "images"
    image_dir.mkdir(exist_ok=True)
    for image_name, image in images.items():
        safe_name = Path(image_name).name
        target = image_dir / safe_name
        converted = image.convert("RGB") if image.mode != "RGB" else image
        converted.save(target)
    options = config or MermaidConfig()
    store = SidecarStore(
        root,
        write_ir=options.write_ir,
        write_svg=options.write_svg,
        write_png=options.write_png,
        write_alternatives=options.write_alternatives,
        write_provenance=options.write_provenance,
    )
    for result in reconstructions:
        store.write(result)
    rows = {row.get("source_id"): row for row in metadata.get("mermaid", [])}
    for result in reconstructions:
        row = rows.get(result.source_id)
        if row is not None:
            row["sidecar_dir"] = result.sidecar_dir
    document_path = root / f"{filename}.md"
    document_path.write_text(markdown, encoding="utf-8")
    (root / f"{filename}_meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return document_path
