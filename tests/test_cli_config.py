import json

import pytest
from pydantic import ValidationError

from marker_mermaid.cli import _load_config
from marker_mermaid.config import MermaidConfig


@pytest.mark.parametrize(
    "settings",
    [
        {"render_timeot_seconds": 1},
        {"MermaidDiagramProcessor_render_timeot_seconds": 1},
        {"score_weights": {"ocr_recal": 1}},
    ],
)
def test_standalone_config_rejects_unknown_settings(tmp_path, settings):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(settings), encoding="utf-8")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _load_config(str(path))


def test_convert_config_retains_unrelated_marker_settings(tmp_path):
    settings = {"pdftext_workers": 2, "MermaidDiagramProcessor_render_timeout_seconds": 1}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(settings), encoding="utf-8")

    raw, config = _load_config(str(path), marker=True)

    assert raw == settings
    assert config.render_timeout_seconds == 1
    with pytest.raises(ValidationError, match="pdftext_workers"):
        _load_config(str(path))


@pytest.mark.parametrize(
    "loader", [MermaidConfig.from_cli_config, MermaidConfig.from_marker_config]
)
@pytest.mark.parametrize("reverse", [False, True])
def test_prefixed_settings_cannot_silently_override_conflicting_plain_settings(loader, reverse):
    pairs = [("render_timeout_seconds", 1), ("MermaidDiagramProcessor_render_timeout_seconds", 2)]
    with pytest.raises(ValueError, match="conflicting settings for render_timeout_seconds"):
        loader(dict(reversed(pairs) if reverse else pairs))


@pytest.mark.parametrize(
    "loader", [MermaidConfig.from_cli_config, MermaidConfig.from_marker_config]
)
def test_equivalent_prefixed_and_plain_settings_are_accepted(loader):
    config = loader(
        {"render_timeout_seconds": 1, "MermaidDiagramProcessor_render_timeout_seconds": 1}
    )
    assert config.render_timeout_seconds == 1


@pytest.mark.parametrize("value", [[], None, "strict", 1])
def test_cli_config_requires_a_json_object(tmp_path, value):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        _load_config(str(path))
