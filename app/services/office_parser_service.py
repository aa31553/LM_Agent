import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from app.core.constants import ErrorCode
from app.core.exceptions import APIError
from app.services.pdf_parser_service import ParsedDocument, ParsedPage
from app.utils.text_utils import clean_db_text


NAMESPACES = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


@dataclass(frozen=True)
class ParsedOfficeDocument:
    pages: list[ParsedPage]
    page_count: int
    text: str


class OfficeParserService:
    supported_types = {"docx", "xlsx", "pptx"}

    async def parse(self, file_path: str | Path, file_type: str | None = None) -> ParsedDocument:
        path = Path(file_path)
        detected_type = (file_type or path.suffix.lower().lstrip(".")).lower()
        if detected_type not in self.supported_types:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Unsupported Office document type. Use .docx, .xlsx, or .pptx files.",
                400,
            )

        try:
            with zipfile.ZipFile(path) as archive:
                if detected_type == "docx":
                    parsed = self._parse_docx(archive)
                elif detected_type == "xlsx":
                    parsed = self._parse_xlsx(archive)
                else:
                    parsed = self._parse_pptx(archive)
        except zipfile.BadZipFile as exc:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Office document is not a valid OpenXML file.",
                400,
            ) from exc

        if not parsed.text.strip():
            raise APIError(ErrorCode.INVALID_REQUEST, "Office document did not contain extractable text.", 400)
        return ParsedDocument(
            pages=parsed.pages,
            page_count=parsed.page_count,
            ocr_required=False,
            ocr_confidence=None,
        )

    def _parse_docx(self, archive: zipfile.ZipFile) -> ParsedOfficeDocument:
        root = self._xml_root(archive, "word/document.xml")
        blocks: list[str] = []
        for paragraph in root.findall(".//w:p", NAMESPACES):
            text = self._joined_text(paragraph.findall(".//w:t", NAMESPACES))
            if text:
                blocks.append(text)
        text = clean_db_text("\n".join(blocks)) or ""
        pages = [ParsedPage(page_number=1, text=text, layout_text=text)]
        return ParsedOfficeDocument(pages=pages, page_count=1, text=text)

    def _parse_xlsx(self, archive: zipfile.ZipFile) -> ParsedOfficeDocument:
        shared_strings = self._shared_strings(archive)
        workbook = self._xml_root(archive, "xl/workbook.xml")
        relationships = self._workbook_relationships(archive)
        pages: list[ParsedPage] = []

        for index, sheet in enumerate(workbook.findall(".//s:sheet", NAMESPACES), start=1):
            sheet_name = sheet.attrib.get("name") or f"Sheet {index}"
            relationship_id = sheet.attrib.get(f"{{{NAMESPACES['r']}}}id")
            target = relationships.get(relationship_id or "")
            if target is None:
                continue
            rows = self._worksheet_rows(archive, target, shared_strings)
            text = clean_db_text("\n".join([f"[Sheet: {sheet_name}]", *rows])) or ""
            if text.strip():
                pages.append(ParsedPage(page_number=len(pages) + 1, text=text, layout_text=text))

        combined = "\n\n".join(page.text for page in pages)
        return ParsedOfficeDocument(pages=pages, page_count=len(pages), text=combined)

    def _parse_pptx(self, archive: zipfile.ZipFile) -> ParsedOfficeDocument:
        pages: list[ParsedPage] = []
        slide_names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=lambda item: int(re.search(r"slide(\d+)\.xml$", item).group(1)),  # type: ignore[union-attr]
        )
        for slide_name in slide_names:
            root = self._xml_root(archive, slide_name)
            lines = [
                self._joined_text(paragraph.findall(".//a:t", NAMESPACES))
                for paragraph in root.findall(".//a:p", NAMESPACES)
            ]
            text = clean_db_text("\n".join(line for line in lines if line)) or ""
            if text.strip():
                pages.append(ParsedPage(page_number=len(pages) + 1, text=text, layout_text=text))
        combined = "\n\n".join(page.text for page in pages)
        return ParsedOfficeDocument(pages=pages, page_count=len(pages), text=combined)

    def _shared_strings(self, archive: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = self._xml_root(archive, "xl/sharedStrings.xml")
        strings: list[str] = []
        for item in root.findall(".//s:si", NAMESPACES):
            strings.append(self._joined_text(item.findall(".//s:t", NAMESPACES)))
        return strings

    def _workbook_relationships(self, archive: zipfile.ZipFile) -> dict[str, str]:
        if "xl/_rels/workbook.xml.rels" not in archive.namelist():
            return {}
        root = self._xml_root(archive, "xl/_rels/workbook.xml.rels")
        relationships: dict[str, str] = {}
        for relationship in root.findall(".//rel:Relationship", NAMESPACES):
            relationship_id = relationship.attrib.get("Id")
            target = relationship.attrib.get("Target")
            if relationship_id and target:
                relationships[relationship_id] = self._normalise_xl_target(target)
        return relationships

    def _worksheet_rows(
        self,
        archive: zipfile.ZipFile,
        worksheet_path: str,
        shared_strings: list[str],
    ) -> list[str]:
        if worksheet_path not in archive.namelist():
            return []
        root = self._xml_root(archive, worksheet_path)
        rows: list[str] = []
        for row in root.findall(".//s:row", NAMESPACES):
            values = [
                self._cell_value(cell, shared_strings)
                for cell in row.findall("s:c", NAMESPACES)
            ]
            values = [value for value in values if value]
            if values:
                rows.append("\t".join(values))
        return rows

    def _cell_value(self, cell: ElementTree.Element, shared_strings: list[str]) -> str:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            return self._joined_text(cell.findall(".//s:t", NAMESPACES))

        raw_value = cell.findtext("s:v", default="", namespaces=NAMESPACES)
        if cell_type == "s":
            try:
                return shared_strings[int(raw_value)]
            except (ValueError, IndexError):
                return ""
        return raw_value

    def _xml_root(self, archive: zipfile.ZipFile, member: str) -> ElementTree.Element:
        try:
            return ElementTree.fromstring(archive.read(member))
        except KeyError as exc:
            raise APIError(ErrorCode.INVALID_REQUEST, f"Office document is missing {member}.", 400) from exc
        except ElementTree.ParseError as exc:
            raise APIError(ErrorCode.INVALID_REQUEST, f"Office document contains invalid XML in {member}.", 400) from exc

    def _joined_text(self, elements: list[ElementTree.Element]) -> str:
        return "".join(element.text or "" for element in elements).strip()

    def _normalise_xl_target(self, target: str) -> str:
        target = target.lstrip("/")
        if target.startswith("xl/"):
            return target
        return f"xl/{target}"
