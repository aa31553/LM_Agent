from pathlib import Path


def infer_file_type(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def is_supported_upload(filename: str) -> bool:
    return infer_file_type(filename) in {"pdf", "png", "jpg", "jpeg", "tiff", "bmp"}

