from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy import delete, text

from app.core.constants import ConfidentialLevel
from app.db.session import SessionLocal
from app.integrations.openai_compatible_client import OpenAICompatibleClient
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_image import DocumentImage
from app.models.knowledge_base import KnowledgeBase
from app.services.document_ingestion_service import DocumentIngestionService
from app.services.markdown_conversion_service import MarkdownConversionService
from app.storage.local_storage import LocalStorage


class FakeOCRResult:
    text = "direct image upload OCR text with guardrail chart"
    confidence = 0.93


class FakeOCRService:
    async def run_ocr(self, file_path: str) -> FakeOCRResult:
        return FakeOCRResult()


class FakeEmbeddingService:
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1024 for _text in texts]


class FakeMarkItDown:
    def __init__(self) -> None:
        self.converted_paths: list[str] = []

    def convert_local(self, file_path):
        self.converted_paths.append(str(file_path))
        return type("ConversionResult", (), {"text_content": "Image metadata"})()


def _create_kb() -> KnowledgeBase:
    with SessionLocal() as db:
        kb = KnowledgeBase(
            name=f"image-upload-test-{uuid4()}",
            default_confidential_level=ConfidentialLevel.INTERNAL.value,
            is_active=True,
        )
        db.add(kb)
        db.commit()
        db.refresh(kb)
        return kb


def _cleanup_document(document_id) -> None:
    with SessionLocal() as db:
        db.execute(delete(DocumentImage).where(DocumentImage.document_id == document_id))
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        db.execute(delete(Document).where(Document.id == document_id))
        db.commit()


@pytest.mark.asyncio
async def test_direct_image_upload_creates_ocr_and_image_chunks(tmp_path) -> None:
    kb = _create_kb()
    image_path = tmp_path / "direct-image.png"
    Image.new("RGB", (80, 40), "white").save(image_path)
    document_id = None
    try:
        with SessionLocal() as db:
            fake_markitdown = FakeMarkItDown()
            service = DocumentIngestionService(
                db=db,
                storage=LocalStorage(str(tmp_path / "uploads")),
                ocr_service=FakeOCRService(),
                embedding_service=FakeEmbeddingService(),
                markdown_converter=MarkdownConversionService(fake_markitdown),
            )
            document = await service.ingest_file_path(
                image_path,
                knowledge_base_id=kb.id,
                confidential_level=ConfidentialLevel.INTERNAL,
                department="qa",
                document_type="image_upload_test",
            )
            document_id = document.id
            assert document.status == "ready"
            assert document.file_type == "png"
            assert document.ocr_required is True
            assert document.ocr_confidence == 0.93
            assert document.chunk_count == 1
            assert Path(document.file_path).parent.name == "originals"
            assert document.markdown_path is not None
            assert Path(document.markdown_path).parent.name == "markdown"
            assert "direct image upload OCR text" in Path(document.markdown_path).read_text(
                encoding="utf-8"
            )
            assert fake_markitdown.converted_paths == [document.file_path]
            source_types = {
                row[0]
                for row in db.execute(
                    text("select source_type from document_chunks where document_id=:id"),
                    {"id": str(document.id)},
                )
            }
            assert source_types == {"markdown"}
            image_count = db.execute(
                text("select count(*) from document_images where document_id=:id"),
                {"id": str(document.id)},
            ).scalar_one()
            assert image_count == 1
    finally:
        if document_id is not None:
            _cleanup_document(document_id)


@pytest.mark.asyncio
async def test_openai_compatible_streaming_client_parses_sse_chunks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert b'"stream":true' in request.read()
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        chunks = [
            chunk
            async for chunk in OpenAICompatibleClient(http_client=http_client).stream_chat_completion(
                system_prompt="system",
                user_prompt="user",
            )
        ]

    assert chunks == ["hello", " world"]
