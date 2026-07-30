import argparse
import asyncio
from pathlib import Path

from app.services.office_file_preparation_service import (
    OFFICE_OPENXML_TYPES,
    OfficeFilePreparationService,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that the current Windows worker account can open an Office "
            "file through pywin32 and create a parser-safe temporary OpenXML copy."
        )
    )
    parser.add_argument("file", type=Path, help="Path to a .docx, .xlsx, or .pptx sample")
    args = parser.parse_args()

    source = args.file.resolve()
    file_type = source.suffix.lower().lstrip(".")
    if file_type not in OFFICE_OPENXML_TYPES:
        parser.error("The sample must be a .docx, .xlsx, or .pptx file.")

    prepared = asyncio.run(OfficeFilePreparationService().prepare(source, file_type))
    with prepared:
        size_bytes = prepared.path.stat().st_size
        print(f"OFFICE_COM_OK type={prepared.file_type} normalized_size_bytes={size_bytes}")


if __name__ == "__main__":
    main()
