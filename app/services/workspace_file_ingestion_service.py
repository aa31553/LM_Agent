from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path
from typing import Any, ClassVar
from xml.etree import ElementTree

import duckdb
import yaml

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.services.image_ocr_service import ImageOCRService, OCRUnavailableError
from app.services.markdown_conversion_service import MarkdownConversionService
from app.services.pdf_markdown_conversion_service import convert_pdf_with_markitdown
from app.services.spreadsheet_ingestion_service import SpreadsheetIngestionService
from app.services.text_document_parser_service import TextDocumentParserService
from app.storage.workspace_storage import WorkspaceStorage
from app.utils.file_utils import upload_type_category


class WorkspaceFileIngestionService:
    """Build non-embedded, cursor-readable LLM representations for workspace files."""

    spreadsheet_types: ClassVar[set[str]] = {"xlsx", "csv"}
    tabular_structured_types: ClassVar[set[str]] = {"json", "yaml", "yml", "xml"}
    markitdown_structured_types: ClassVar[set[str]] = {"html", "htm"}

    def __init__(self, storage: WorkspaceStorage | None = None) -> None:
        self.storage = storage or WorkspaceStorage()

    def process(
        self,
        *,
        workspace_id,
        file_id,
        file_path: str,
        file_type: str,
    ) -> dict[str, Any]:
        if file_type in self.spreadsheet_types:
            result = SpreadsheetIngestionService(self.storage).profile_and_convert(
                workspace_id=workspace_id,
                file_id=file_id,
                file_path=file_path,
                file_type=file_type,
            )
            content_path = self._write_dataset_markdown(
                workspace_id=workspace_id,
                file_id=file_id,
                filename=Path(file_path).name,
                datasets=result["dataset_manifest"]["datasets"],
            )
            result["dataset_manifest"].update(
                self._representation_metadata(content_path, "markdown_table")
            )
            return result

        content, representation_format = self._document_markdown(file_path, file_type)
        if not content.strip():
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Workspace file did not contain readable content.",
                400,
            )
        content_path = self.storage.save_llm_content(workspace_id, file_id, content)
        return {
            "profile_path": None,
            "profile": None,
            "dataset_manifest": {
                "schema_version": "1.0",
                "kind": "workspace_document",
                "datasets": [],
                **self._representation_metadata(content_path, representation_format),
            },
        }

    def _document_markdown(self, file_path: str, file_type: str) -> tuple[str, str]:
        category = upload_type_category(file_type)
        title = Path(file_path).name
        if category == "pdf":
            result = convert_pdf_with_markitdown(
                file_path=file_path,
                max_file_bytes=settings.pdf_max_file_bytes,
                max_pages=settings.pdf_max_pages,
            )
            if result.markdown.strip():
                return result.markdown, "markdown"
            if not settings.pdf_ocr_enabled:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    "MarkItDown found no PDF text and PDF OCR is disabled.",
                    400,
                )
            parsed = ImageOCRService().run_pdf_ocr_sync(
                file_path,
                max_pages=settings.pdf_max_pages,
            )
            return (
                MarkdownConversionService().from_extracted_text(title, parsed.text),
                "markdown_ocr",
            )

        if category == "image":
            converter = MarkdownConversionService()
            converted = asyncio.run(converter.convert_local(file_path))
            try:
                ocr = ImageOCRService().run_ocr_sync(file_path)
            except OCRUnavailableError as exc:
                raise APIError(
                    ErrorCode.INVALID_REQUEST,
                    f"Image OCR runtime is unavailable: {exc}",
                    400,
                ) from exc
            return converter.combine_image_ocr(title, ocr.text, converted), "markdown_ocr"

        if file_type in self.tabular_structured_types:
            return self._structured_markdown(file_path, file_type), "markdown_table"

        if file_type in self.markitdown_structured_types:
            return asyncio.run(MarkdownConversionService().convert_local(file_path)), (
                "markdown_table"
            )

        if category in {"text", "structured", "code"}:
            parsed = asyncio.run(TextDocumentParserService().parse(file_path, file_type))
            return parsed.markdown, "markdown_table" if file_type == "csv" else "markdown"

        if category == "office":
            return asyncio.run(MarkdownConversionService().convert_local(file_path)), "markdown"

        raise APIError(
            ErrorCode.INVALID_REQUEST,
            f"Unsupported workspace file type: .{file_type}",
            400,
        )

    def _structured_markdown(self, file_path: str, file_type: str) -> str:
        path = Path(file_path)
        decoded = TextDocumentParserService()._decode(path.read_bytes())
        if file_type == "json":
            value = json.loads(decoded)
        elif file_type in {"yaml", "yml"}:
            value = yaml.safe_load(decoded)
        else:
            value = self._xml_value(ElementTree.fromstring(decoded))
        return self._value_as_markdown(path.stem, value)

    def _value_as_markdown(self, title: str, value: Any) -> str:
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            columns: list[str] = []
            for item in value:
                for key in item:
                    if str(key) not in columns:
                        columns.append(str(key))
            rows = [[item.get(column) for column in columns] for item in value]
            return self._markdown_table(title, columns, rows)
        if isinstance(value, dict):
            rows = [[key, item] for key, item in value.items()]
            return self._markdown_table(title, ["key", "value"], rows)
        if isinstance(value, list):
            return self._markdown_table(
                title,
                ["index", "value"],
                [[index, item] for index, item in enumerate(value)],
            )
        return f"# {title}\n\n{self._cell(value)}\n"

    def _write_dataset_markdown(
        self,
        *,
        workspace_id,
        file_id,
        filename: str,
        datasets: list[dict[str, Any]],
    ) -> str:
        destination = self.storage.llm_content_path(workspace_id, file_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".md.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="") as output:
                output.write(f"# {self._cell(filename)}\n")
                for dataset in datasets:
                    output.write(f"\n## {self._cell(dataset.get('sheet') or dataset['key'])}\n\n")
                    csv_path = destination.parent / f"{dataset['key']}.csv.tmp"
                    connection = duckdb.connect()
                    try:
                        source = self._sql_literal(str(Path(dataset["path"]).resolve()))
                        target = self._sql_literal(str(csv_path.resolve()))
                        connection.execute(
                            f"COPY (SELECT * FROM read_parquet({source})) TO {target} "
                            "(FORMAT CSV, HEADER TRUE)"
                        )
                    finally:
                        connection.close()
                    try:
                        with csv_path.open("r", encoding="utf-8", newline="") as rows_file:
                            rows = csv.reader(rows_file)
                            header = next(rows, [])
                            if not header:
                                output.write("_Empty dataset._\n")
                                continue
                            output.write("| " + " | ".join(self._cell(v) for v in header) + " |\n")
                            output.write("| " + " | ".join("---" for _ in header) + " |\n")
                            for row in rows:
                                padded = row + [""] * (len(header) - len(row))
                                output.write(
                                    "| "
                                    + " | ".join(self._cell(v) for v in padded[: len(header)])
                                    + " |\n"
                                )
                    finally:
                        csv_path.unlink(missing_ok=True)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return str(destination)

    def _markdown_table(self, title: str, columns: list[str], rows: list[list[Any]]) -> str:
        lines = [
            f"# {self._cell(title)}",
            "",
            "| " + " | ".join(self._cell(column) for column in columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
        ]
        lines.extend(
            "| " + " | ".join(self._cell(item) for item in row) + " |" for row in rows
        )
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _cell(value: Any) -> str:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, default=str)
        return str("" if value is None else value).replace("|", "\\|").replace("\n", "<br>")

    @classmethod
    def _xml_value(cls, element: ElementTree.Element) -> Any:
        children = list(element)
        if not children:
            return (element.text or "").strip()
        names = [child.tag for child in children]
        if len(set(names)) == 1:
            return [cls._xml_value(child) for child in children]
        return {child.tag: cls._xml_value(child) for child in children}

    @staticmethod
    def _representation_metadata(path: str, representation_format: str) -> dict[str, Any]:
        return {
            "representation_format": representation_format,
            "representation_path": path,
            "representation_size_bytes": Path(path).stat().st_size,
        }

    @staticmethod
    def _sql_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"


def process_workspace_file(
    *,
    workspace_id,
    file_id,
    file_path: str,
    file_type: str,
    storage_root: str | None = None,
) -> dict[str, Any]:
    storage = WorkspaceStorage(root=storage_root) if storage_root is not None else None
    return WorkspaceFileIngestionService(storage).process(
        workspace_id=workspace_id,
        file_id=file_id,
        file_path=file_path,
        file_type=file_type,
    )
