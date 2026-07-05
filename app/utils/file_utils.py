from pathlib import Path


SUPPORTED_UPLOAD_TYPES = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "tiff",
    "bmp",
    "docx",
    "xlsx",
    "pptx",
}


def infer_file_type(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def is_supported_upload(filename: str) -> bool:
    return infer_file_type(filename) in SUPPORTED_UPLOAD_TYPES
