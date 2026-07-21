import pytest

from app.services.text_document_parser_service import TextDocumentParserService
from app.utils.file_utils import is_supported_upload, supported_upload_formats


@pytest.mark.asyncio
async def test_text_structured_and_traditional_chinese_formats(tmp_path) -> None:
    parser = TextDocumentParserService()
    csv_path = tmp_path / "data.csv"
    json_path = tmp_path / "data.json"
    html_path = tmp_path / "page.html"
    cp950_path = tmp_path / "report.txt"
    csv_path.write_text("name,value\nAOI,10", encoding="utf-8")
    json_path.write_text('{"machine": "AOI-01", "ok": true}', encoding="utf-8")
    html_path.write_text(
        "<h1>Report</h1><script>ignore()</script><p>Result OK</p>",
        encoding="utf-8",
    )
    cp950_path.write_bytes("繁體中文報告".encode("cp950"))

    csv_result = await parser.parse(csv_path)
    json_result = await parser.parse(json_path)
    html_result = await parser.parse(html_path)
    cp950_result = await parser.parse(cp950_path)

    assert "| name | value |" in csv_result.markdown
    assert '"machine": "AOI-01"' in json_result.markdown
    assert "Result OK" in html_result.text
    assert "ignore" not in html_result.text
    assert "繁體中文報告" in cp950_result.text


def test_supported_upload_formats_are_centralized() -> None:
    for filename in (
        "manual.pdf", "photo.webp", "policy.docx", "table.xlsx", "slides.pptx",
        "notes.md", "events.log", "data.csv", "config.yaml", "page.html", "data.xml",
    ):
        assert is_supported_upload(filename)
    formats = supported_upload_formats()
    assert any(
        item["extension"] == ".json" and item["category"] == "structured"
        for item in formats
    )
