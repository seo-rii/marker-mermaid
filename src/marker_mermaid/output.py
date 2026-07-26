"""Save Markdown, original images, metadata, and diagram bundles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from marker_mermaid.config import MermaidConfig
from marker_mermaid.models import (
    ReconstructionResult,
    canonical_evidence_collection_snapshot,
)
from marker_mermaid.output_transaction import OutputTransaction
from marker_mermaid.sidecars import SidecarStore, safe_artifact_component


@dataclass(frozen=True)
class _OutputSnapshot:
    reconstruction_pairs: tuple[tuple[ReconstructionResult, ReconstructionResult], ...]
    images: tuple[tuple[str, Image.Image, str], ...]
    metadata: dict[str, Any]
    metadata_bindings: tuple[tuple[dict, str | None], ...]
    markdown: bytes


def _preflight_output(
    root: Path,
    filename: str,
    markdown: str,
    images: dict[str, Image.Image],
    metadata: dict,
    reconstructions: list[ReconstructionResult],
) -> _OutputSnapshot:
    if type(markdown) is not str:
        raise TypeError("output Markdown must be a string")
    markdown_payload = markdown.encode("utf-8")
    if not isinstance(metadata, dict):
        raise TypeError("output metadata must be a dictionary")
    metadata_rows = metadata.get("mermaid", [])
    if not isinstance(metadata_rows, list):
        raise TypeError("metadata Mermaid rows must be a list")
    if any(not isinstance(row, dict) for row in metadata_rows):
        raise TypeError("metadata Mermaid rows must contain dictionaries")

    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise FileExistsError("output path already exists and is not a safe directory")
    document_path = root / f"{filename}.md"
    metadata_path = root / f"{filename}_meta.json"
    if root.exists():
        for target in (document_path, metadata_path):
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"output artifact already exists: {target.name}")
    image_dir = root / "images"
    if image_dir.is_symlink() or (image_dir.exists() and not image_dir.is_dir()):
        raise FileExistsError("output images path is not a safe directory")

    registered_extensions = Image.registered_extensions()
    image_names: set[str] = set()
    image_snapshots: list[tuple[str, Image.Image, str]] = []
    for name, image in images.items():
        if Path(name).name != name or name in {"", ".", ".."}:
            raise ValueError(f"image name must be a single safe component: {name!r}")
        if name in image_names:
            raise ValueError(f"duplicate image basename: {name}")
        if not isinstance(image, Image.Image):
            raise TypeError(f"image payload for {name!r} is not a Pillow image")
        image_format = registered_extensions.get(Path(name).suffix.lower())
        if image_format is None or image_format not in Image.SAVE:
            raise ValueError(f"image name has no supported writable extension: {name!r}")
        image_target = image_dir / name
        if image_target.exists() or image_target.is_symlink():
            raise FileExistsError(f"output image already exists: {name}")
        image_names.add(name)
        image_snapshots.append((name, image, image_format))

    source_ids: set[str] = set()
    sidecar_names: set[str] = set()
    reconstruction_pairs: list[tuple[ReconstructionResult, ReconstructionResult]] = []
    for result in reconstructions:
        if type(result) is not ReconstructionResult:
            raise TypeError("output reconstructions must contain ReconstructionResult records")
        try:
            evidence_snapshot = canonical_evidence_collection_snapshot(result.evidence)
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
            raise ValueError(f"output evidence preflight failed: {exc}") from exc
        result_snapshot = ReconstructionResult.model_copy(result, deep=False)
        result_snapshot.evidence = list(evidence_snapshot.evidence)
        reconstruction_pairs.append((result, result_snapshot))
        if result_snapshot.source_id in source_ids:
            raise ValueError(f"duplicate reconstruction source id: {result_snapshot.source_id}")
        source_ids.add(result_snapshot.source_id)
        sidecar_name = safe_artifact_component(result_snapshot.source_id)
        if sidecar_name in sidecar_names:
            raise ValueError(f"colliding sidecar directory name: {sidecar_name}")
        sidecar_names.add(sidecar_name)
        if result_snapshot.source_image_name not in image_names:
            raise ValueError(
                "missing source image "
                f"{result_snapshot.source_image_name!r} for {result_snapshot.source_id!r}"
            )
        alternative_names: set[str] = set()
        for alternative in result_snapshot.alternatives:
            name = safe_artifact_component(alternative.candidate_id)
            if name in alternative_names:
                raise ValueError(
                    f"colliding alternative artifact name for {result_snapshot.source_id!r}: {name}"
                )
            alternative_names.add(name)
        if root.exists() and (root / "diagrams" / sidecar_name).exists():
            raise FileExistsError(f"sidecar bundle already exists: {sidecar_name}")

    metadata_ids = [
        row.get("source_id")
        for row in metadata_rows
        if row.get("source_id") is not None
    ]
    if len(metadata_ids) != len(set(metadata_ids)):
        raise ValueError("metadata contains duplicate Mermaid source rows")
    metadata_payload = json.dumps(
        metadata,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )
    metadata_snapshot = json.loads(metadata_payload)
    metadata_bindings = tuple(
        (row, row.get("source_id"))
        for row in metadata_rows
    )
    return _OutputSnapshot(
        reconstruction_pairs=tuple(reconstruction_pairs),
        images=tuple(image_snapshots),
        metadata=metadata_snapshot,
        metadata_bindings=metadata_bindings,
        markdown=markdown_payload,
    )


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
    snapshot = _preflight_output(
        root,
        filename,
        markdown,
        images,
        metadata,
        reconstructions,
    )
    options = config or MermaidConfig()
    transaction = OutputTransaction(root)
    try:
        with transaction:
            for image_name, image, image_format in snapshot.images:
                converted = image.convert("RGB") if image.mode != "RGB" else image
                with transaction.open_binary(f"images/{image_name}") as target:
                    converted.save(target, format=image_format)
            store = SidecarStore(
                root,
                write_ir=options.write_ir,
                write_svg=options.write_svg,
                write_png=options.write_png,
                write_alternatives=options.write_alternatives,
                write_provenance=options.write_provenance,
            )
            for _, result_snapshot in snapshot.reconstruction_pairs:
                store.write(
                    result_snapshot,
                    output_root_fd=transaction.directory_fd,
                )
            rows = {
                row.get("source_id"): row
                for row in snapshot.metadata.get("mermaid", [])
            }
            for _, result_snapshot in snapshot.reconstruction_pairs:
                row = rows.get(result_snapshot.source_id)
                if row is not None:
                    row["sidecar_dir"] = result_snapshot.sidecar_dir
            transaction.write_bytes(f"{filename}.md", snapshot.markdown)
            transaction.write_bytes(
                f"{filename}_meta.json",
                (
                    json.dumps(
                        snapshot.metadata,
                        ensure_ascii=False,
                        indent=2,
                        allow_nan=False,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            transaction.commit()
    finally:
        if transaction.published:
            sidecars = {
                result_snapshot.source_id: result_snapshot.sidecar_dir
                for _, result_snapshot in snapshot.reconstruction_pairs
            }
            for live_result, result_snapshot in snapshot.reconstruction_pairs:
                live_result.sidecar_dir = result_snapshot.sidecar_dir
            for row, source_id in snapshot.metadata_bindings:
                sidecar_dir = sidecars.get(source_id)
                if sidecar_dir is not None:
                    dict.__setitem__(row, "sidecar_dir", sidecar_dir)
    return root / f"{filename}.md"
