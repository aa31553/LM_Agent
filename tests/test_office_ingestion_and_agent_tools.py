import json
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.integrations.openai_compatible_client import (
    ChatCompletionResult,
    ChatToolCall,
    OpenAICompatibleClient,
)
from app.core.constants import ConfidentialLevel
from app.core.security import Principal
from app.services.agent_tool_service import AgentToolService
from app.services.chunking_service import ChunkingService
from app.services.office_parser_service import OfficeParserService
from app.utils.file_utils import is_supported_upload


def _write_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _write_docx(path: Path) -> None:
    _write_zip(
        path,
        {
            "word/document.xml": """
                <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
                  <w:body>
                    <w:p><w:r><w:t>Quarterly policy overview</w:t></w:r></w:p>
                    <w:p><w:r><w:t>Retention rules apply to internal data.</w:t></w:r></w:p>
                  </w:body>
                </w:document>
            """,
        },
    )


def _write_xlsx(path: Path) -> None:
    _write_zip(
        path,
        {
            "xl/workbook.xml": """
                <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                  <sheets><sheet name="Risks" sheetId="1" r:id="rId1"/></sheets>
                </workbook>
            """,
            "xl/_rels/workbook.xml.rels": """
                <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                  <Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>
                </Relationships>
            """,
            "xl/sharedStrings.xml": """
                <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <si><t>Metric</t></si><si><t>Score</t></si><si><t>Data leakage</t></si>
                </sst>
            """,
            "xl/worksheets/sheet1.xml": """
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <sheetData>
                    <row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>
                    <row><c t="s"><v>2</v></c><c><v>8</v></c></row>
                  </sheetData>
                </worksheet>
            """,
        },
    )


def _write_pptx(path: Path) -> None:
    _write_zip(
        path,
        {
            "ppt/slides/slide1.xml": """
                <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
                  <p:cSld><p:spTree>
                    <p:sp><p:txBody><a:p><a:r><a:t>Security roadmap</a:t></a:r></a:p></p:txBody></p:sp>
                    <p:sp><p:txBody><a:p><a:r><a:t>Enable agent tools carefully.</a:t></a:r></a:p></p:txBody></p:sp>
                  </p:spTree></p:cSld>
                </p:sld>
            """,
        },
    )


@pytest.mark.asyncio
async def test_office_parser_extracts_word_excel_and_powerpoint_text(tmp_path: Path) -> None:
    docx_path = tmp_path / "policy.docx"
    xlsx_path = tmp_path / "risks.xlsx"
    pptx_path = tmp_path / "roadmap.pptx"
    _write_docx(docx_path)
    _write_xlsx(xlsx_path)
    _write_pptx(pptx_path)

    parser = OfficeParserService()
    docx = await parser.parse(docx_path)
    xlsx = await parser.parse(xlsx_path)
    pptx = await parser.parse(pptx_path)

    assert docx.page_count == 1
    assert "Quarterly policy overview" in docx.text
    assert xlsx.page_count == 1
    assert "[Sheet: Risks]" in xlsx.text
    assert "Data leakage" in xlsx.text
    assert pptx.page_count == 1
    assert "Security roadmap" in pptx.text


def test_office_upload_types_and_chunk_metadata() -> None:
    assert is_supported_upload("policy.docx")
    assert is_supported_upload("risks.xlsx")
    assert is_supported_upload("roadmap.pptx")
    chunks = ChunkingService().chunk_pages(
        pages=[type("Page", (), {"page_number": 1, "text": "alpha beta gamma"})()],
        source_type="docx_text",
    )

    assert chunks[0].metadata["source_type"] == "docx_text"


@pytest.mark.asyncio
async def test_openai_client_sends_and_parses_tool_calls() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.read())
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "search_documents",
                                        "arguments": "{\"query\":\"retention\"}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        result = await OpenAICompatibleClient(http_client=http_client).chat_completion_messages(
            messages=[{"role": "user", "content": "Find retention policy"}],
            tools=[{"type": "function", "function": {"name": "search_documents"}}],
            tool_choice="auto",
        )

    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["payload"]["tools"][0]["function"]["name"] == "search_documents"
    assert result.tool_calls == [
        ChatToolCall(id="call_1", name="search_documents", arguments="{\"query\":\"retention\"}")
    ]


@pytest.mark.asyncio
async def test_agent_tool_service_runs_tool_loop() -> None:
    class FakeLLMService:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_messages(self, messages, tools=None, tool_choice=None):
            self.calls += 1
            if self.calls == 1:
                return ChatCompletionResult(
                    content="",
                    tool_calls=[
                        ChatToolCall(
                            id="call_1",
                            name="search_documents",
                            arguments=json.dumps({"query": "retention"}),
                        )
                    ],
                )
            assert messages[-1]["role"] == "tool"
            assert "retention policy" in messages[-1]["content"]
            return ChatCompletionResult(content="Use the retention policy.", tool_calls=[])

    class FakeAgentToolService(AgentToolService):
        async def execute_tool(self, **kwargs):
            return {"results": [{"content": "retention policy", "document_id": str(uuid4())}]}

    principal = Principal(
        external_user_id="admin",
        username="admin",
        roles={"admin"},
        department="IT",
        clearance_level=ConfidentialLevel.RESTRICTED,
    )
    result = await FakeAgentToolService(llm_service=FakeLLMService()).answer_with_tools(
        system_prompt="system",
        user_prompt="user",
        knowledge_base_ids=[uuid4()],
        top_k=3,
        use_rerank=False,
        principal=principal,
    )

    assert result.answer == "Use the retention policy."
    assert result.tool_calls[0].tool_name == "search_documents"
    assert result.tool_calls[0].result["results"][0]["content"] == "retention policy"
