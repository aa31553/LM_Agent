import re


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clean_db_text(text: str | None) -> str | None:
    if text is None:
        return None
    return "".join(
        character
        for character in text
        if character == "\n" or character == "\t" or ord(character) >= 32
    )
