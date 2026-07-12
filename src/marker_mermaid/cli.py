"""Command-line interface for conversion, validation, fixtures, and review."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from marker_mermaid.config import MermaidConfig
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.markdown import standalone_document_markdown
from marker_mermaid.output import save_document_output
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.review import serve_review
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime, default_runtime_dir


def _load_config(path: str | None) -> tuple[dict, MermaidConfig]:
    raw = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    return raw, MermaidConfig.from_marker_config(raw)


def _runtime(config: MermaidConfig) -> NodeMermaidRuntime:
    return NodeMermaidRuntime(config.runtime_dir)


def _validator(runtime: NodeMermaidRuntime, config: MermaidConfig) -> CandidateValidator:
    return CandidateValidator(
        runtime,
        config.security_profile,
        max_chars=config.max_mermaid_chars,
        max_lines=config.max_mermaid_lines,
    )


def command_validate(args) -> int:
    _, config = _load_config(args.config)
    runtime = _runtime(config)
    try:
        code = Path(args.file).read_text(encoding="utf-8")
        outcome = _validator(runtime, config).validate(code, config.render_timeout_seconds)
    finally:
        runtime.close()
    print(
        json.dumps(
            {
                "syntax_valid": outcome.runtime.syntax_valid,
                "render_valid": outcome.runtime.render_valid,
                "diagram_type": outcome.runtime.diagram_type,
                "warnings": outcome.warnings,
                "error": outcome.runtime.error,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if outcome.runtime.render_valid else 1


def command_reconstruct(args) -> int:
    _, config = _load_config(args.config)
    image = Image.open(args.image)
    engine = JsonFixtureEngine.from_path(args.fixture)
    runtime = _runtime(config)
    try:
        pipeline = ReconstructionPipeline(
            config,
            [engine],
            _validator(runtime, config),
        )
        source_id = args.source_id or Path(args.image).stem
        image_name = Path(args.image).name
        result = pipeline.reconstruct(source_id, image_name, image)
    finally:
        runtime.close()
    markdown = standalone_document_markdown(
        result, image_path=f"images/{image_name}", show_score=True
    )
    document = save_document_output(
        output_dir=args.output,
        filename=args.name,
        markdown=markdown,
        images={image_name: image},
        metadata={
            "mermaid": [
                {
                    "source_id": result.source_id,
                    "status": result.status,
                    "grade": result.grade,
                    "publish": result.publish,
                    "selected_candidate_id": (
                        result.selected.candidate_id if result.selected else None
                    ),
                }
            ]
        },
        reconstructions=[result],
        config=config,
    )
    print(document)
    return 0 if result.selected else 1


def command_convert(args) -> int:
    try:
        from marker.models import create_model_dict

        from marker_mermaid.marker_integration import (
            MarkerMermaidPdfConverter,
            MermaidMarkdownOutput,
        )
    except ImportError as exc:
        raise SystemExit("Install the marker extra: pip install 'marker-mermaid[marker]'") from exc
    raw, config = _load_config(args.config)
    raw["extract_images"] = True
    raw["use_llm"] = True
    models = create_model_dict()
    converter = MarkerMermaidPdfConverter(
        artifact_dict=models,
        llm_service=args.llm_service,
        config=raw,
    )
    rendered = converter(args.pdf)
    if not isinstance(rendered, MermaidMarkdownOutput):
        raise RuntimeError("unexpected Marker renderer output")
    filename = args.name or Path(args.pdf).stem
    document = save_document_output(
        output_dir=args.output,
        filename=filename,
        markdown=rendered.markdown,
        images=rendered.images,
        metadata=rendered.metadata,
        reconstructions=rendered.reconstructions,
        config=config,
    )
    print(document)
    return 0


def command_doctor(args) -> int:
    runtime_dir = default_runtime_dir()
    checks = {
        "python": sys.version.split()[0],
        "node": shutil.which("node"),
        "runtime_dir": str(runtime_dir),
        "runtime_worker": (runtime_dir / "worker.mjs").is_file(),
        "runtime_node_modules": (runtime_dir / "node_modules" / "mermaid").is_dir(),
        "runtime_playwright": (runtime_dir / "node_modules" / "playwright").is_dir(),
    }
    browser_path = os.environ.get("MARKER_MERMAID_CHROMIUM_EXECUTABLE")
    if not browser_path and checks["node"] and checks["runtime_playwright"]:
        probe = subprocess.run(
            [
                "node",
                "--input-type=module",
                "--eval",
                "import {chromium} from 'playwright'; console.log(chromium.executablePath())",
            ],
            cwd=runtime_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0:
            browser_path = probe.stdout.strip()
    checks["chromium_executable"] = browser_path
    checks["chromium_installed"] = bool(browser_path and Path(browser_path).is_file())
    try:
        checks["marker_pdf"] = importlib.metadata.version("marker-pdf")
    except importlib.metadata.PackageNotFoundError:
        checks["marker_pdf"] = None
    print(json.dumps(checks, indent=2))
    required = (
        checks["node"],
        checks["runtime_worker"],
        checks["runtime_node_modules"],
        checks["runtime_playwright"],
        checks["chromium_installed"],
    )
    return 0 if all(required) else 1


def command_install_runtime(args) -> int:
    packaged = Path(__file__).resolve().parent / "runtime"
    runtime_dir = default_runtime_dir()
    if runtime_dir != packaged:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        for name in ("package.json", "package-lock.json", "worker.mjs"):
            shutil.copy2(packaged / name, runtime_dir / name)
    subprocess.run(["npm", "ci", "--ignore-scripts"], cwd=runtime_dir, check=True)
    subprocess.run(["npx", "playwright", "install", "chromium"], cwd=runtime_dir, check=True)
    return 0


def command_review(args) -> int:
    serve_review(args.output, host=args.host, port=args.port, open_browser=not args.no_open)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marker-mermaid")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="security-scan, parse, and render Mermaid")
    validate.add_argument("file")
    validate.add_argument("--config")
    validate.set_defaults(handler=command_validate)

    reconstruct = subparsers.add_parser(
        "reconstruct", help="reconstruct one image from a JSON fixture"
    )
    reconstruct.add_argument("image")
    reconstruct.add_argument("--fixture", required=True)
    reconstruct.add_argument("--output", required=True)
    reconstruct.add_argument("--config")
    reconstruct.add_argument("--name", default="document")
    reconstruct.add_argument("--source-id")
    reconstruct.set_defaults(handler=command_reconstruct)

    convert = subparsers.add_parser("convert", help="convert a PDF through Marker 1.10.2")
    convert.add_argument("pdf")
    convert.add_argument("--output", required=True)
    convert.add_argument("--config")
    convert.add_argument("--name")
    convert.add_argument("--llm-service", default="marker.services.gemini.GoogleGeminiService")
    convert.set_defaults(handler=command_convert)

    review = subparsers.add_parser("review", help="serve the local read-only review workspace")
    review.add_argument("output")
    review.add_argument("--host", default="127.0.0.1")
    review.add_argument("--port", type=int, default=8765)
    review.add_argument("--no-open", action="store_true")
    review.set_defaults(handler=command_review)

    doctor = subparsers.add_parser("doctor", help="check optional Marker and Mermaid runtimes")
    doctor.set_defaults(handler=command_doctor)
    install = subparsers.add_parser(
        "install-runtime", help="install pinned Node and Chromium runtime"
    )
    install.set_defaults(handler=command_install_runtime)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
