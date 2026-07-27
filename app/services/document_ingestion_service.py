import logging
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.constants import ConfidentialLevel, DocumentScope, DocumentStatus, ErrorCode
from app.core.exceptions import APIError
from app.core.security import Principal
from app.models.audit import AuditEvent
from app.models.chat import ChatSession
from app.models.document import Document, DocumentProcessingJob
from app.models.document_chunk import DocumentChunk
from app.models.document_image import DocumentImage
from app.models.knowledge_base import KnowledgeBase
from app.models.permission import DocumentPermission
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_image_repository import DocumentImageRepository
from app.repositories.document_repository import DocumentRepository
from app.schemas.document import (
    DocumentArchiveResponse,
    DocumentDetail,
    DocumentListItem,
    DocumentStatusResponse,
    DocumentUpdate,
    DocumentUploadResponse,
    ReindexResponse,
)
from app.services.audit_service import AuditService
from app.services.chunking_service import ChunkingService, TextChunk
from app.services.embedding_service import EmbeddingService
from app.services.image_ocr_service import ImageOCRService, OCRUnavailableError
from app.services.markdown_conversion_service import MarkdownConversionService
from app.services.office_parser_service import OfficeParserService
from app.services.pdf_image_extraction_service import PDFImageExtractionService
from app.services.pdf_parser_service import PDFParserService
from app.services.permission_service import PermissionService
from app.services.processing_queue_service import ProcessingQueueService
from app.services.text_document_parser_service import TextDocumentParserService
from app.storage.local_storage import LocalStorage
from app.utils.file_utils import (
    SUPPORTED_UPLOAD_TYPES,
    infer_file_type,
    is_supported_upload,
    upload_type_category,
)
from app.utils.language_detector import detect_language
from app.utils.text_utils import clean_db_text

logger = logging.getLogger(__name__)


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
        office_parser: OfficeParserService | None = None,
        markdown_converter: MarkdownConversionService | None = None,
        text_parser: TextDocumentParserService | None = None,
    ) -> None:
        self.db = db
        self.storage = storage or LocalStorage()
        self.pdf_parser = pdf_parser or PDFParserService()
        self.ocr_service = ocr_service or ImageOCRService()
        self.chunking_service = chunking_service or ChunkingService()
        self.embedding_service = embedding_service or EmbeddingService()
        self.image_extraction_service = image_extraction_service or PDFImageExtractionService()
        self.office_parser = office_parser or OfficeParserService()
        self.markdown_converter = markdown_converter or MarkdownConversionService()
        self.text_parser = text_parser or TextDocumentParserService()

    async def queue_upload(
        self,
        file: UploadFile,
        scope: DocumentScope,
        knowledge_base_id: UUID | None,
        session_id: UUID | None,
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
                "Unsupported document type. Call GET /api/v1/documents/formats for the supported extensions.",
                400,
            )

        PermissionService(self.db).ensure_level_access(principal, confidential_level)

        knowledge_base_id, session_id, created_by = self._resolve_upload_scope(
            scope=scope,
            knowledge_base_id=knowledge_base_id,
            session_id=session_id,
            principal=principal,
            title_seed=file.filename,
        )
        content = await file.read()
        document = await self.create_uploaded_document(
            content=content,
            original_filename=file.filename,
            knowledge_base_id=knowledge_base_id,
            session_id=session_id,
            confidential_level=confidential_level,
            department=department,
            document_type=document_type,
            version=version,
            principal=principal,
            created_by=created_by,
        )
        job = ProcessingQueueService(self.db).enqueue(document.id, "document")
        logger.info(
            "document_upload_queued",
            extra={"document_id": str(document.id), "job_id": str(job.id), "request_id": request_id},
        )
        return DocumentUploadResponse(
            request_id=request_id,
            document_id=document.id,
            status=DocumentStatus(document.status),
            scope=scope,
            knowledge_base_id=document.knowledge_base_id,
            session_id=document.session_id,
            message="Document uploaded and queued for background processing.",
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
        process: bool = True,
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
            process=process,
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
        process: bool = True,
    ) -> Document:
        document = await self.create_uploaded_document(
            content=content,
            original_filename=original_filename,
            knowledge_base_id=knowledge_base_id,
            confidential_level=confidential_level,
            department=department,
            document_type=document_type,
            version=version,
            principal=principal,
        )
        if process:
            await self._process_with_failure_capture(document, raise_on_failure=False)
        return document

    async def create_uploaded_document(
        self,
        content: bytes,
        original_filename: str,
        knowledge_base_id: UUID | None,
        confidential_level: ConfidentialLevel,
        session_id: UUID | None = None,
        department: str | None = None,
        document_type: str | None = None,
        version: str | None = None,
        principal: Principal | None = None,
        created_by: UUID | None = None,
    ) -> Document:
        if self.db is None:
            raise APIError(ErrorCode.INTERNAL_ERROR, "Database session is not configured.", 500)

        if (knowledge_base_id is None) == (session_id is None):
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Exactly one of knowledge_base_id or session_id is required.",
                400,
            )
        if knowledge_base_id is not None:
            self._ensure_knowledge_base_exists(knowledge_base_id)
        file_type = infer_file_type(original_filename)
        if file_type not in SUPPORTED_UPLOAD_TYPES:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Unsupported document type. Call GET /api/v1/documents/formats for the supported extensions.",
                400,
            )

        filename = f"{uuid4()}-{Path(original_filename).name}"
        file_path = await self.storage.save_original(filename, content)
        document = Document(
            knowledge_base_id=knowledge_base_id,
            session_id=session_id,
            filename=filename,
            original_filename=original_filename,
            title=Path(original_filename).stem,
            file_type=file_type,
            file_path=file_path,
            markdown_path=None,
            source_type=document_type or "manual_upload",
            language=None,
            confidential_level=confidential_level.value,
            department=department,
            version=version,
            status=DocumentStatus.UPLOADED.value,
            created_by=created_by,
        )
        document_repo = DocumentRepository(self.db)
        document_repo.add(document)
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
            scope=self._document_scope(document),
            knowledge_base_id=document.knowledge_base_id,
            session_id=document.session_id,
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
        return await self.process_document(document_id, request_id=request_id, reset=True)

    async def process_document(
        self,
        document_id: UUID,
        request_id: str,
        reset: bool = True,
    ) -> ReindexResponse:
        document = self._get_document(document_id)
        if document.status == DocumentStatus.ARCHIVED.value:
            raise APIError(ErrorCode.INVALID_REQUEST, "Archived documents cannot be reindexed.", 400)
        if not Path(document.file_path).exists():
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Original document file is missing and cannot be reindexed.",
                400,
            )

        if reset:
            self._reset_document_for_processing(document)

        await self._process_with_failure_capture(document, raise_on_failure=True)
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

    def update_metadata(self, document_id: UUID, payload: DocumentUpdate) -> Document:
        document = self._get_document(document_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            if field == "confidential_level" and value is not None:
                value = value.value
            setattr(document, field, value)
        document.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(document)
        return document

    def delete(self, document_id: UUID) -> int:
        """Permanently remove one document, its derived data, jobs and stored artifacts."""
        document = self._get_document(document_id)
        image_paths = list(
            self.db.scalars(
                select(DocumentImage.image_path).where(DocumentImage.document_id == document.id)
            )
        )
        artifact_paths = [
            path for path in (document.file_path, document.markdown_path, *image_paths) if path
        ]
        self.db.execute(delete(AuditEvent).where(
            AuditEvent.target_type == "document", AuditEvent.target_id == document.id
        ))
        self.db.execute(delete(DocumentProcessingJob).where(DocumentProcessingJob.document_id == document.id))
        self.db.execute(delete(DocumentPermission).where(DocumentPermission.document_id == document.id))
        self.db.execute(delete(DocumentImage).where(DocumentImage.document_id == document.id))
        self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        self.db.delete(document)
        self.db.commit()
        return sum(self._delete_artifact(path) for path in artifact_paths)

    def _delete_artifact(self, raw_path: str) -> int:
        path = Path(raw_path).resolve()
        allowed_roots = [
            self.storage.root.resolve(),
            Path("data/extracted_images").resolve(),
        ]
        if not any(path.is_relative_to(root) for root in allowed_roots) or not path.is_file():
            return 0
        try:
            path.unlink()
        except OSError:
            return 0
        for root in allowed_roots:
            if path.is_relative_to(root):
                parent = path.parent
                while parent != root and parent.is_dir():
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
                break
        return 1

    def list_item(self, document: Document) -> DocumentListItem:
        return DocumentListItem(
            document_id=document.id,
            filename=document.original_filename or document.filename,
            title=document.title,
            status=DocumentStatus(document.status),
            confidential_level=ConfidentialLevel(document.confidential_level),
            scope=self._document_scope(document),
            knowledge_base_id=document.knowledge_base_id,
            session_id=document.session_id,
            department=document.department,
            page_count=document.page_count,
            chunk_count=document.chunk_count,
            ocr_required=document.ocr_required,
            ocr_confidence=document.ocr_confidence,
            error_message=document.error_message,
            created_at=document.created_at,
        )

    async def _process_document(self, document: Document) -> None:
        category = upload_type_category(document.file_type)
        if category == "pdf":
            await self._process_pdf_document(document)
            return
        if category == "office":
            await self._process_office_document(document)
            return
        if category in {"text", "structured"}:
            await self._process_text_document(document)
            return
        if category == "image":
            await self._process_image_document(document)
            return
        raise APIError(
            ErrorCode.INVALID_REQUEST,
            f"Unsupported document type: .{document.file_type}",
            400,
        )

    async def _process_with_failure_capture(
        self,
        document: Document,
        *,
        raise_on_failure: bool,
    ) -> None:
        try:
            await self._process_document(document)
        except Exception as exc:
            document.status = DocumentStatus.FAILED.value
            document.error_message = exc.message if isinstance(exc, APIError) else str(exc)
            document.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(document)
            logger.exception("document_processing_failed", extra={"document_id": str(document.id)})
            if raise_on_failure:
                raise
            return

        if document.status == DocumentStatus.FAILED.value and raise_on_failure:
            raise APIError(
                ErrorCode.INTERNAL_ERROR,
                document.error_message or "Document processing failed.",
                500,
            )

    def _reset_document_for_processing(self, document: Document) -> None:
        DocumentImageRepository(self.db).delete_by_document_id(document.id)
        ChunkRepository(self.db).delete_by_document_id(document.id)
        document.chunk_count = 0
        document.markdown_path = None
        document.error_message = None
        document.ocr_required = False
        document.ocr_confidence = None
        document.updated_at = datetime.utcnow()
        self.db.commit()

    async def _process_pdf_document(self, document: Document) -> None:
        document.status = DocumentStatus.PARSING.value
        document.updated_at = datetime.utcnow()
        self.db.commit()

        converted_markdown = await self.markdown_converter.convert_local(document.file_path)
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
            converted_markdown = self.markdown_converter.from_extracted_text(
                document.title,
                parsed.text,
            )

        image_models = await self.image_extraction_service.extract(document, parsed)
        if image_models:
            DocumentImageRepository(self.db).add_many(image_models)
            self.db.flush()

        if not converted_markdown.strip() and parsed.text.strip():
            converted_markdown = self.markdown_converter.from_extracted_text(
                document.title,
                parsed.text,
            )
        converted_markdown = self._append_image_markdown(converted_markdown, image_models)
        if not converted_markdown.strip():
            document.status = DocumentStatus.FAILED.value
            document.error_message = "Document did not contain extractable text."
            document.updated_at = datetime.utcnow()
            self.db.commit()
            return

        markdown_path = await self._save_markdown_artifact(document, converted_markdown)
        document.status = DocumentStatus.CHUNKING.value
        document.language = detect_language(converted_markdown)
        self.db.commit()

        chunks = self.chunking_service.chunk_markdown(
            converted_markdown,
            markdown_path=markdown_path,
        )
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
        document.chunk_count = len(chunk_models)
        document.status = DocumentStatus.READY.value
        document.error_message = None
        document.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(document)

    async def _process_office_document(self, document: Document) -> None:
        document.status = DocumentStatus.PARSING.value
        document.updated_at = datetime.utcnow()
        self.db.commit()

        converted_markdown = await self.markdown_converter.convert_local(document.file_path)
        parsed = await self.office_parser.parse(document.file_path, file_type=document.file_type)
        document.page_count = parsed.page_count
        document.ocr_required = False
        document.ocr_confidence = None

        if not converted_markdown.strip() and parsed.text.strip():
            converted_markdown = self.markdown_converter.from_extracted_text(
                document.title,
                parsed.text,
            )
        if not converted_markdown.strip():
            document.status = DocumentStatus.FAILED.value
            document.error_message = "Office document did not contain extractable text."
            document.updated_at = datetime.utcnow()
            self.db.commit()
            return

        markdown_path = await self._save_markdown_artifact(document, converted_markdown)
        document.status = DocumentStatus.CHUNKING.value
        document.language = detect_language(converted_markdown)
        self.db.commit()

        chunks = self.chunking_service.chunk_markdown(
            converted_markdown,
            markdown_path=markdown_path,
        )
        if not chunks:
            raise APIError(ErrorCode.INVALID_REQUEST, "Office document did not produce any chunks.", 400)

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
        document.chunk_count = len(chunk_models)
        document.status = DocumentStatus.READY.value
        document.error_message = None
        document.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(document)

    async def _process_text_document(self, document: Document) -> None:
        document.status = DocumentStatus.PARSING.value
        document.updated_at = datetime.utcnow()
        self.db.commit()

        parsed = await self.text_parser.parse(
            document.file_path,
            file_type=document.file_type,
        )
        converted_markdown = parsed.markdown
        document.page_count = parsed.page_count
        document.ocr_required = False
        document.ocr_confidence = None

        markdown_path = await self._save_markdown_artifact(document, converted_markdown)
        document.status = DocumentStatus.CHUNKING.value
        document.language = detect_language(parsed.text)
        self.db.commit()

        chunks = self.chunking_service.chunk_markdown(
            converted_markdown,
            markdown_path=markdown_path,
            metadata={"source_type": f"{document.file_type}_text"},
        )
        if not chunks:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Text document did not produce any chunks.",
                400,
            )

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

        converted_markdown = await self.markdown_converter.convert_local(document.file_path)
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

        converted_markdown = self.markdown_converter.combine_image_ocr(
            document.title,
            ocr_text,
            converted_markdown,
        )
        markdown_path = await self._save_markdown_artifact(document, converted_markdown)
        document.status = DocumentStatus.CHUNKING.value
        document.language = detect_language(converted_markdown)
        self.db.commit()

        chunks = self.chunking_service.chunk_markdown(
            converted_markdown,
            markdown_path=markdown_path,
            metadata={
                "page_start": 1,
                "page_end": 1,
                "image_ids": [str(image_model.id)],
            },
        )
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

    async def _save_markdown_artifact(self, document: Document, markdown: str) -> str:
        filename = f"{Path(document.filename).stem}.md"
        markdown_path = await self.storage.save_markdown(filename, markdown)
        document.markdown_path = markdown_path
        document.updated_at = datetime.utcnow()
        self.db.commit()
        return markdown_path

    def _append_image_markdown(
        self,
        markdown: str,
        images: list[DocumentImage],
    ) -> str:
        image_sections: list[str] = []
        for index, image in enumerate(images, start=1):
            details = [f"### Image {index} (page {image.page_number})"]
            if image.caption:
                details.append(f"**Caption:** {image.caption}")
            if image.ocr_text:
                details.append(f"**OCR text:**\n\n{image.ocr_text}")
            if len(details) > 1:
                image_sections.append("\n\n".join(details))
        if not image_sections:
            return markdown
        parts = [markdown.strip(), "## Extracted images", *image_sections]
        return "\n\n".join(part for part in parts if part).rstrip() + "\n"

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

    def _get_knowledge_base(self, knowledge_base_id: UUID) -> KnowledgeBase:
        knowledge_base = self.db.get(KnowledgeBase, knowledge_base_id)
        if knowledge_base is None:
            raise APIError(ErrorCode.INVALID_REQUEST, "Knowledge base not found.", 400)
        return knowledge_base

    def _ensure_knowledge_base_exists(self, knowledge_base_id: UUID) -> None:
        self._get_knowledge_base(knowledge_base_id)

    def _resolve_upload_scope(
        self,
        *,
        scope: DocumentScope,
        knowledge_base_id: UUID | None,
        session_id: UUID | None,
        principal: Principal,
        title_seed: str,
    ) -> tuple[UUID | None, UUID | None, UUID]:
        audit = AuditService(self.db)
        user = audit.ensure_user(principal)
        if scope == DocumentScope.KNOWLEDGE_BASE:
            if knowledge_base_id is None or session_id is not None:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "knowledge_base scope requires knowledge_base_id and forbids session_id.",
                    400,
                )
            knowledge_base = self._get_knowledge_base(knowledge_base_id)
            PermissionService(self.db).ensure_knowledge_base_write(principal, knowledge_base)
            return knowledge_base_id, None, user.id

        if knowledge_base_id is not None:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "session scope requires knowledge_base_id to be omitted.",
                400,
            )
        if session_id is None:
            session = audit.ensure_session(
                principal=principal,
                user=user,
                session_id=None,
                title_seed=title_seed,
            )
        else:
            session = self.db.get(ChatSession, session_id)
            if session is None:
                raise APIError(ErrorCode.INVALID_REQUEST, "Chat session not found.", 404)
            if session.user_id != user.id and "admin" not in principal.roles:
                raise APIError(
                    ErrorCode.PERMISSION_DENIED,
                    "User does not have permission to access this session.",
                    403,
                )
        return None, session.id, user.id

    def _document_scope(self, document: Document) -> DocumentScope:
        if document.session_id is not None:
            return DocumentScope.SESSION
        return DocumentScope.KNOWLEDGE_BASE

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
