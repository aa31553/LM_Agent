from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from app.core.constants import ConfidentialLevel, DocumentStatus, ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_image import DocumentImage
from app.models.knowledge_base import KnowledgeBase
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.document_image_repository import DocumentImageRepository
from app.schemas.document import (
    DocumentArchiveResponse,
    DocumentDetail,
    DocumentListItem,
    DocumentStatusResponse,
    DocumentUploadResponse,
    ReindexResponse,
)
from app.services.chunking_service import ChunkingService, TextChunk
from app.services.embedding_service import EmbeddingService
from app.services.image_ocr_service import ImageOCRService, OCRUnavailableError
from app.services.pdf_parser_service import PDFParserService, ParsedDocument, ParsedPage
from app.services.pdf_image_extraction_service import PDFImageExtractionService
from app.storage.local_storage import LocalStorage
from app.utils.file_utils import infer_file_type, is_supported_upload
from app.utils.language_detector import detect_language
from app.utils.text_utils import clean_db_text


class DocumentIngestionService:
    def __init__(
        self,
        db: Session | None = None,
        storage: LocalStorage | None = None,
        pdf_parser: PDFParserService | None = None,
        ocr_service: ImageOCRService | None = None,
        chunking_service: ChunkingService | None = None,
        embedding_service: EmbeddingService | None = None,
        image_extraction_service: PDFImageExtractionService | None = None,
    ) -> None:
        self.db = db
        self.storage = storage or LocalStorage()
        self.pdf_parser = pdf_parser or PDFParserService()
        self.ocr_service = ocr_service or ImageOCRService()
        self.chunking_service = chunking_service or ChunkingService()
        self.embedding_service = embedding_service or EmbeddingService()
        self.image_extraction_service = image_extraction_service or PDFImageExtractionService()

    async def queue_upload(
        self,
        file: UploadFile,
        knowledge_base_id: UUID,
        confidential_level: ConfidentialLevel,
        department: str | None,
        document_type: str | None,
        version: str | None,
        request_id: str,
        principal: Principal,
    ) -> DocumentUploadResponse:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        if not file.filename or not is_supported_upload(file.filename):
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Unsupported document type. Supported uploads are PDF and common image files.",
                400,
            )

        content = await file.read()
        document = await self.ingest_bytes(
            content=content,
            original_filename=file.filename,
            knowledge_base_id=knowledge_base_id,
            confidential_level=confidential_level,
            department=department,
            document_type=document_type,
            version=version,
            principal=principal,
        )
        return DocumentUploadResponse(
            request_id=request_id,
            document_id=document.id,
            status=DocumentStatus(document.status),
            message=self._upload_message(document),
        )

    async def ingest_file_path(
        self,
        file_path: str | Path,
        knowledge_base_id: UUID,
        confidential_level: ConfidentialLevel,
        department: str | None = None,
        document_type: str | None = None,
        version: str | None = None,
        principal: Principal | None = None,
    ) -> Document:
        path = Path(file_path)
        return await self.ingest_bytes(
            content=path.read_bytes(),
            original_filename=path.name,
            knowledge_base_id=knowledge_base_id,
            confidential_level=confidential_level,
            department=department,
            document_type=document_type,
            version=version,
            principal=principal,
        )

    async def ingest_bytes(
        self,
        content: bytes,
        original_filename: str,
        knowledge_base_id: UUID,
        confidential_level: ConfidentialLevel,
        department: str | None = None,
        document_type: str | None = None,
        version: str | None = None,
        principal: Principal | None = None,
    ) -> Document:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)

        self._ensure_knowledge_base_exists(knowledge_base_id)
        file_type = infer_file_type(original_filename)
        if file_type not in {"pdf", "png", "jpg", "jpeg", "tiff", "bmp"}:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Unsupported document type. Supported uploads are PDF and common image files.",
                400,
            )

        filename = f"{uuid4()}-{Path(original_filename).name}"
        file_path = await self.storage.save(filename, content)
        document = Document(
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            original_filename=original_filename,
            title=Path(original_filename).stem,
            file_type=file_type,
            file_path=file_path,
            source_type=document_type or "manual_upload",
            language=None,
            confidential_level=confidential_level.value,
            department=department,
            version=version,
            status=DocumentStatus.UPLOADED.value,
            created_by=None,
        )
        document_repo = DocumentRepository(self.db)
        document_repo.add(document)
        self.db.commit()
        self.db.refresh(document)

        try:
            await self._process_document(document)
        except APIError as exc:
            document.status = DocumentStatus.FAILED.value
            document.error_message = exc.message
            document.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(document)
        return document

    def status(self, document_id: UUID) -> DocumentStatusResponse:
        document = self._get_document(document_id)
        return DocumentStatusResponse(
            document_id=document_id,
            status=DocumentStatus(document.status),
            progress=self._progress_for_status(DocumentStatus(document.status)),
            message=document.error_message or self._status_message(DocumentStatus(document.status)),
        )

    def detail(self, document_id: UUID) -> DocumentDetail:
        document = self._get_document(document_id)
        return self.detail_from_document(document)

    def detail_from_document(self, document: Document) -> DocumentDetail:
        return DocumentDetail(
            document_id=document.id,
            filename=document.original_filename or document.filename,
            title=document.title,
            knowledge_base_id=document.knowledge_base_id,
            language=document.language,
            confidential_level=ConfidentialLevel(document.confidential_level),
            department=document.department,
            status=DocumentStatus(document.status),
            page_count=document.page_count,
            chunk_count=document.chunk_count,
            ocr_required=document.ocr_required,
            ocr_confidence=document.ocr_confidence,
            error_message=document.error_message,
            file_type=document.file_type,
            source_type=document.source_type,
            version=document.version,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )

    async def reindex(self, document_id: UUID, request_id: str) -> ReindexResponse:
        document = self._get_document(document_id)
        if document.status == DocumentStatus.ARCHIVED.value:
            raise APIError(ErrorCode.INVALID_REQUEST, "Archived documents cannot be reindexed.", 400)
        if not Path(document.file_path).exists():
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Original document file is missing and cannot be reindexed.",
                400,
            )

        DocumentImageRepository(self.db).delete_by_document_id(document.id)
        ChunkRepository(self.db).delete_by_document_id(document.id)
        document.chunk_count = 0
        document.error_message = None
        document.ocr_required = False
        document.ocr_confidence = None
        document.updated_at = datetime.utcnow()
        self.db.commit()

        await self._process_document(document)
        status = DocumentStatus(document.status)
        return ReindexResponse(
            request_id=request_id,
            document_id=document_id,
            status=status.value,
            message=self._status_message(status)
            if status != DocumentStatus.FAILED
            else document.error_message or "Document reindex failed.",
        )

    def archive(self, document_id: UUID) -> DocumentArchiveResponse:
        document = self._get_document(document_id)
        document.status = DocumentStatus.ARCHIVED.value
        document.updated_at = datetime.utcnow()
        self.db.commit()
        return DocumentArchiveResponse(document_id=document_id, status=DocumentStatus.ARCHIVED)

    def list_item(self, document: Document) -> DocumentListItem:
        return DocumentListItem(
            document_id=document.id,
            filename=document.original_filename or document.filename,
            title=document.title,
            status=DocumentStatus(document.status),
            confidential_level=ConfidentialLevel(document.confidential_level),
            knowledge_base_id=document.knowledge_base_id,
            department=document.department,
            page_count=document.page_count,
            chunk_count=document.chunk_count,
            ocr_required=document.ocr_required,
            ocr_confidence=document.ocr_confidence,
            error_message=document.error_message,
            created_at=document.created_at,
        )

    async def _process_document(self, document: Document) -> None:
        if document.file_type == "pdf":
            await self._process_pdf_document(document)
            return
        await self._process_image_document(document)

    async def _process_pdf_document(self, document: Document) -> None:
        document.status = DocumentStatus.PARSING.value
        document.updated_at = datetime.utcnow()
        self.db.commit()

        parsed = await self.pdf_parser.parse(document.file_path)
        document.page_count = parsed.page_count
        document.ocr_required = parsed.ocr_required

        if parsed.ocr_required:
            document.status = DocumentStatus.OCR_PROCESSING.value
            document.updated_at = datetime.utcnow()
            self.db.commit()
            try:
                parsed = await self.ocr_service.run_pdf_ocr(document.file_path)
            except OCRUnavailableError as exc:
                document.status = DocumentStatus.FAILED.value
                document.error_message = (
                    "PDF contains no extractable text and requires OCR, "
                    f"but OCR runtime is unavailable: {exc}"
                )
                document.updated_at = datetime.utcnow()
                self.db.commit()
                return
            document.page_count = parsed.page_count
            document.ocr_confidence = parsed.ocr_confidence
            document.ocr_required = parsed.ocr_required

        image_models = await self.image_extraction_service.extract(document, parsed)
        if image_models:
            DocumentImageRepository(self.db).add_many(image_models)
            self.db.flush()

        if not parsed.text.strip() and not image_models:
            document.status = DocumentStatus.FAILED.value
            document.error_message = "Document did not contain extractable text."
            document.updated_at = datetime.utcnow()
            self.db.commit()
            return

        document.status = DocumentStatus.CHUNKING.value
        language_source = parsed.text or " ".join(
            item.caption or item.ocr_text or "" for item in image_models
        )
        document.language = detect_language(language_source)
        self.db.commit()

        chunks = self.chunking_service.chunk_pages(parsed.pages)
        chunks.extend(self.chunking_service.chunk_images(image_models, start_index=len(chunks)))
        if not chunks:
            raise APIError(ErrorCode.INVALID_REQUEST, "Document did not produce any chunks.", 400)

        document.status = DocumentStatus.EMBEDDING.value
        self.db.commit()
        embeddings = await self._embed_chunks(chunks)

        document.status = DocumentStatus.INDEXING.value
        self.db.commit()
        chunk_models = [
            self._chunk_model(document, text_chunk, embedding)
            for text_chunk, embedding in zip(chunks, embeddings, strict=True)
        ]
        ChunkRepository(self.db).add_many(chunk_models)
        self.db.flush()
        self._link_images_to_chunks(image_models, chunks, chunk_models)
        document.chunk_count = len(chunk_models)
        document.status = DocumentStatus.READY.value
        document.error_message = None
        document.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(document)

    async def _process_image_document(self, document: Document) -> None:
        document.status = DocumentStatus.OCR_PROCESSING.value
        document.page_count = 1
        document.ocr_required = True
        document.updated_at = datetime.utcnow()
        self.db.commit()

        try:
            result = await self.ocr_service.run_ocr(document.file_path)
        except OCRUnavailableError as exc:
            document.status = DocumentStatus.FAILED.value
            document.error_message = f"Image OCR runtime is unavailable: {exc}"
            document.updated_at = datetime.utcnow()
            self.db.commit()
            return

        document.ocr_confidence = result.confidence
        ocr_text = clean_db_text(result.text.strip()) or ""
        width, height, mime_type = self._inspect_image(document.file_path)
        image_model = DocumentImage(
            document_id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            page_number=1,
            image_index=1,
            caption=document.title,
            image_path=document.file_path,
            mime_type=mime_type,
            width=width,
            height=height,
            ocr_text=ocr_text or None,
            extraction_method="direct_upload",
            image_metadata={"source_type": "direct_upload"},
        )
        DocumentImageRepository(self.db).add(image_model)
        self.db.flush()

        if not ocr_text:
            document.status = DocumentStatus.FAILED.value
            document.error_message = "Image OCR completed but produced no text."
            document.updated_at = datetime.utcnow()
            self.db.commit()
            return

        document.status = DocumentStatus.CHUNKING.value
        document.language = detect_language(ocr_text)
        self.db.commit()

        parsed = ParsedDocument(
            pages=[
                ParsedPage(
                    page_number=1,
                    text=ocr_text,
                    layout_text=ocr_text,
                    ocr_confidence=result.confidence,
                )
            ],
            page_count=1,
            ocr_required=True,
            ocr_confidence=result.confidence,
        )
        chunks = self.chunking_service.chunk_image_ocr_text(
            parsed.text,
            image_model,
            start_index=0,
        )
        chunks.extend(self.chunking_service.chunk_images([image_model], start_index=len(chunks)))
        if not chunks:
            raise APIError(ErrorCode.INVALID_REQUEST, "Image did not produce any chunks.", 400)

        document.status = DocumentStatus.EMBEDDING.value
        self.db.commit()
        embeddings = await self._embed_chunks(chunks)

        document.status = DocumentStatus.INDEXING.value
        self.db.commit()
        chunk_models = [
            self._chunk_model(document, text_chunk, embedding)
            for text_chunk, embedding in zip(chunks, embeddings, strict=True)
        ]
        ChunkRepository(self.db).add_many(chunk_models)
        self.db.flush()
        self._link_images_to_chunks([image_model], chunks, chunk_models)
        document.chunk_count = len(chunk_models)
        document.status = DocumentStatus.READY.value
        document.error_message = None
        document.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(document)

    async def _embed_chunks(self, chunks: list[TextChunk]) -> list[list[float]]:
        texts = [chunk.content for chunk in chunks]
        return await self.embedding_service.embed_texts(texts)

    def _chunk_model(
        self,
        document: Document,
        text_chunk: TextChunk,
        embedding: list[float],
    ) -> DocumentChunk:
        return DocumentChunk(
            document_id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            chunk_index=text_chunk.chunk_index,
            content=clean_db_text(text_chunk.content) or "",
            masked_content=None,
            section_title=None,
            page_start=text_chunk.metadata.get("page_start"),
            page_end=text_chunk.metadata.get("page_end"),
            language=document.language,
            source_type=text_chunk.metadata.get("source_type", "pdf_text"),
            token_count=text_chunk.token_count,
            confidential_level=document.confidential_level,
            chunk_metadata={
                "original_filename": document.original_filename,
                **text_chunk.metadata,
            },
            embedding=embedding,
        )

    def _link_images_to_chunks(
        self,
        images: list[DocumentImage],
        text_chunks: list[TextChunk],
        chunk_models: list[DocumentChunk],
    ) -> None:
        images_by_id = {str(image.id): image for image in images}
        for text_chunk, chunk_model in zip(text_chunks, chunk_models, strict=True):
            image_ids = text_chunk.metadata.get("image_ids") or []
            for image_id in image_ids:
                image = images_by_id.get(str(image_id))
                if image is not None:
                    image.chunk_id = chunk_model.id

    def _inspect_image(self, image_path: str) -> tuple[int | None, int | None, str | None]:
        try:
            with Image.open(image_path) as image:
                width, height = image.size
                mime_type = Image.MIME.get(image.format or "")
                return width, height, mime_type
        except OSError:
            return None, None, None

    def _get_document(self, document_id: UUID) -> Document:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)
        document = DocumentRepository(self.db).get(document_id)
        if document is None:
            raise APIError(ErrorCode.DOCUMENT_NOT_FOUND, "Document not found.", 404)
        return document

    def get_document_model(self, document_id: UUID) -> Document:
        return self._get_document(document_id)

    def _ensure_knowledge_base_exists(self, knowledge_base_id: UUID) -> None:
        if self.db.get(KnowledgeBase, knowledge_base_id) is None:
            raise APIError(ErrorCode.INVALID_REQUEST, "Knowledge base not found.", 400)

    def _progress_for_status(self, status: DocumentStatus) -> int:
        return {
            DocumentStatus.UPLOADED: 5,
            DocumentStatus.PARSING: 20,
            DocumentStatus.OCR_PROCESSING: 30,
            DocumentStatus.CHUNKING: 45,
            DocumentStatus.EMBEDDING: 65,
            DocumentStatus.INDEXING: 85,
            DocumentStatus.READY: 100,
            DocumentStatus.FAILED: 100,
            DocumentStatus.ARCHIVED: 100,
        }[status]

    def _status_message(self, status: DocumentStatus) -> str:
        return {
            DocumentStatus.UPLOADED: "Document has been uploaded.",
            DocumentStatus.PARSING: "Parser is running.",
            DocumentStatus.OCR_PROCESSING: "OCR is running.",
            DocumentStatus.CHUNKING: "Chunking is running.",
            DocumentStatus.EMBEDDING: "Embedding is running.",
            DocumentStatus.INDEXING: "Indexing is running.",
            DocumentStatus.READY: "Document is searchable.",
            DocumentStatus.FAILED: "Document processing failed.",
            DocumentStatus.ARCHIVED: "Document is archived.",
        }[status]

    def _upload_message(self, document: Document) -> str:
        if document.status == DocumentStatus.READY.value:
            return "Document uploaded and indexed successfully."
        if document.status == DocumentStatus.FAILED.value:
            return document.error_message or "Document uploaded but processing failed."
        return "Document uploaded successfully."
