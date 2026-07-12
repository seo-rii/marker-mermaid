"""Save Markdown, original images, metadata, and diagram bundles."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from marker_mermaid.config import MermaidConfig
from marker_mermaid.models import ReconstructionResult
from marker_mermaid.sidecars import SidecarStore, safe_artifact_component


def _preflight_output(
    root: Path,
    images: dict[str, Image.Image],
    metadata: dict,
    reconstructions: list[ReconstructionResult],
) -> None:
    image_names: set[str] = set()
    for name, image in images.items():
        if Path(name).name != name or name in {"", ".", ".."}:
            raise ValueError(f"image name must be a single safe component: {name!r}")
        if name in image_names:
            raise ValueError(f"duplicate image basename: {name}")
        if not isinstance(image, Image.Image):
            raise TypeError(f"image payload for {name!r} is not a Pillow image")
        image_names.add(name)

    source_ids: set[str] = set()
    sidecar_names: set[str] = set()
    for result in reconstructions:
        if result.source_id in source_ids:
            raise ValueError(f"duplicate reconstruction source id: {result.source_id}")
        source_ids.add(result.source_id)
        sidecar_name = safe_artifact_component(result.source_id)
        if sidecar_name in sidecar_names:
            raise ValueError(f"colliding sidecar directory name: {sidecar_name}")
        sidecar_names.add(sidecar_name)
        if result.source_image_name not in image_names:
            raise ValueError(
                f"missing source image {result.source_image_name!r} for {result.source_id!r}"
            )
        alternative_names: set[str] = set()
        for alternative in result.alternatives:
            name = safe_artifact_component(alternative.candidate_id)
            if name in alternative_names:
                raise ValueError(
                    f"colliding alternative artifact name for {result.source_id!r}: {name}"
                )
            alternative_names.add(name)
        if (root / "diagrams" / sidecar_name).exists():
            raise FileExistsError(f"sidecar bundle already exists: {sidecar_name}")

    metadata_ids = [
        row.get("source_id")
        for row in metadata.get("mermaid", [])
        if isinstance(row, dict) and row.get("source_id") is not None
    ]
    if len(metadata_ids) != len(set(metadata_ids)):
        raise ValueError("metadata contains duplicate Mermaid source rows")
    json.dumps(metadata, ensure_ascii=False, indent=2)


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
    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise ValueError("filename must be a single safe path component")
    _preflight_output(root, images, metadata, reconstructions)
    root.mkdir(parents=True, exist_ok=True)
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
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return document_path
