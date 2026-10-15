from urllib.parse import unquote

import pytest
from markdown_it import MarkdownIt

from marker_mermaid.markdown import standalone_document_markdown
from marker_mermaid.models import ReconstructionResult


@pytest.mark.parametrize(
    "name",
    ["plain.png", "a b.png", "a)b.png", "a(b).png", "a#b.png", "a%20b.png",
     "a\nb.png", "한글 그림.png"],
)
def test_original_image_destination_round_trips_to_saved_file(tmp_path, name):
    image_path = f"images/{name}"
    saved_file = tmp_path / image_path
    saved_file.parent.mkdir()
    saved_file.write_bytes(b"image")
    result = ReconstructionResult(
        source_id="source", source_image_name=name, status="failed"
    )

    markdown = standalone_document_markdown(result, image_path=image_path)
    inline = MarkdownIt().parse(markdown)[1]

    assert inline.children is not None
    assert len(inline.children) == 1
    image = inline.children[0]
    assert image.type == "image"
    assert unquote(image.attrGet("src")) == image_path
    assert (tmp_path / unquote(image.attrGet("src"))).read_bytes() == b"image"


@pytest.mark.parametrize(
    "alt_text",
    ["[original](other)", "*bold* _italic_ `code`", r"a\b", "<tag> &copy;",
     "]\n![extra](other)\r", '한글 "원본"'],
)
def test_original_alt_text_is_literal_markdown_content(alt_text):
    result = ReconstructionResult(
        source_id="source", source_image_name="source.png", status="failed"
    )

    markdown = standalone_document_markdown(
        result, image_path="images/source.png", alt_text=alt_text
    )
    tokens = MarkdownIt().parse(markdown)

    assert [token.type for token in tokens] == ["paragraph_open", "inline", "paragraph_close"]
    assert tokens[1].children is not None
    assert len(tokens[1].children) == 1
    image = tokens[1].children[0]
    assert image.type == "image"
    assert image.children is not None
    assert all(child.type in {"text", "text_special"} for child in image.children)
    assert "".join(child.content for child in image.children) == alt_text
