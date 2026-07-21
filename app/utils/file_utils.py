from pathlib import Path


UPLOAD_TYPE_GROUPS: dict[str, tuple[str, ...]] = {
    "pdf": ("pdf",),
    "image": ("png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"),
    "office": ("docx", "xlsx", "pptx"),
    "text": ("txt", "md", "markdown", "log"),
    "structured": ("csv", "json", "yaml", "yml", "html", "htm", "xml"),
}

SUPPORTED_UPLOAD_TYPES = {
    extension
    for extensions in UPLOAD_TYPE_GROUPS.values()
    for extension in extensions
}


def infer_file_type(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def is_supported_upload(filename: str) -> bool:
    return infer_file_type(filename) in SUPPORTED_UPLOAD_TYPES


def upload_type_category(file_type: str) -> str | None:
    normalized = file_type.lower().lstrip(".")
    for category, extensions in UPLOAD_TYPE_GROUPS.items():
        if normalized in extensions:
            return category
    return None


def supported_upload_formats() -> list[dict[str, str]]:
    return [
        {
            "extension": f".{extension}",
            "file_type": extension,
            "category": category,
        }
        for category, extensions in UPLOAD_TYPE_GROUPS.items()
        for extension in extensions
    ]
