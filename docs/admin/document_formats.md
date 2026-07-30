# Document Format Administration

## Supported formats

| Category | Extensions | Processing path |
| --- | --- | --- |
| PDF | `.pdf` | Isolated `pypdf` text extraction; full OCR only when no text is found. Embedded image extraction/OCR is opt-in and bounded. |
| Image | `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`, `.webp` | Pillow + local OCR |
| Office OpenXML | `.docx`, `.xlsx`, `.pptx` | Windows Office COM read-only open/save → temporary OpenXML → MarkItDown + local parser |
| Text | `.txt`, `.md`, `.markdown`, `.log` | Encoding-aware local parser |
| Structured | `.csv`, `.json`, `.yaml`, `.yml`, `.html`, `.htm`, `.xml` | Safe local parser to Markdown |

Text decoding supports UTF-8, BOM-marked UTF-16, and Traditional Chinese CP950. Structured
parsers validate malformed input before indexing. HTML ignores script/style content; YAML uses
safe loading; XML is parsed locally. The original file and normalized Markdown remain separate.

PDF work is never performed by the API/Uvicorn process. Start
`python -m app.workers.document_tasks` independently. The defaults reject files above 50 MB or
200 pages, record a 20-second per-page ceiling, and terminate the whole PDF child after 10
minutes. `PDF_IMAGE_EXTRACTION_ENABLED` and `PDF_IMAGE_OCR_ENABLED` default to `false`; when
enabled, `PDF_IMAGE_MAX_PAGES`, `PDF_IMAGE_MAX_COUNT`, and `PDF_IMAGE_OCR_MAX_COUNT` bound work.

Office processing requires Windows, `pywin32`, installed Microsoft Word/Excel/PowerPoint, and a
worker identity authorized to decrypt the organization's protected files. API/Uvicorn only saves
the original and queues work. Both `document_tasks` and `analysis_tasks` must run under an
interactive or service identity whose Office sign-in and Purview/AIP rights are valid.

Legacy binary Office files (`.doc`, `.xls`, `.ppt`) remain intentionally rejected so the public
upload contract and downstream parsers receive only OpenXML. Open and save them as `.docx`,
`.xlsx`, or `.pptx` before upload.

Validate the deployed identity with a protected sample:

```powershell
.\.venv\Scripts\python -m app.scripts.validate_office_com E:\samples\protected.xlsx
```

The command must print `OFFICE_COM_OK`. A successful import of `win32com` alone does not prove
that the account can decrypt or export a sensitivity-labeled file.

## Single source of truth

The authoritative registry is:

```text
app/utils/file_utils.py
└── UPLOAD_TYPE_GROUPS
```

It drives backend validation and `GET /api/v1/documents/formats`. The frontend reads that API to
set the file input `accept` value and render its format hint, so extensions are not duplicated in
JavaScript.

To add another extension to an existing category:

1. Add it to the matching tuple in `UPLOAD_TYPE_GROUPS`.
2. Confirm the category parser can read it.
3. Add a parser test and upload/processing test.
4. Update this compatibility table.

To add a new category:

1. Add the category and extensions to `UPLOAD_TYPE_GROUPS`.
2. Implement a parser under `app/services/`.
3. Register it in `DocumentIngestionService._process_document()`.
4. Normalize output to UTF-8 Markdown, then use the existing chunk/embed/index stages.
5. Add failure tests for malformed and empty files.

Do not only add an extension to `SUPPORTED_UPLOAD_TYPES`: without a processing dispatch it may be
accepted at upload and fail later. The category registry and processing dispatch must remain in
sync.

## Runtime API

```http
GET /api/v1/documents/formats
Authorization: Bearer <token>
```

The response contains each extension/category pair plus a browser-ready `accept` string. API
clients should read this endpoint instead of maintaining a private hard-coded allowlist.
