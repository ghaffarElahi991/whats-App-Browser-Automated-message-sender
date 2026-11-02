from __future__ import annotations

import re
from pathlib import Path


class TemplateFileError(ValueError):
    pass


def load_docx_template(path: str | Path) -> str:
    source = Path(path).expanduser()
    if not source.exists():
        raise TemplateFileError(f"File does not exist: {source}")
    if source.suffix.lower() != ".docx":
        raise TemplateFileError("Choose a .docx message template")

    try:
        from docx import Document
    except ImportError as exc:
        raise TemplateFileError(
            "Word support is not installed. Run: pip install -r requirements.txt"
        ) from exc

    try:
        document = Document(source)
    except Exception as exc:
        raise TemplateFileError(f"Could not read Word document: {exc}") from exc

    paragraphs = [
        paragraph.text.strip()
        for paragraph in document.paragraphs
        if paragraph.text.strip()
    ]
    if not paragraphs:
        raise TemplateFileError("The Word document does not contain a message")
    return "\n\n".join(paragraphs)


_PLACEHOLDER = re.compile(r"\{(name|phone)\}", flags=re.IGNORECASE)


def render_message(template: str, *, name: str = "", phone: str = "") -> str:
    """Replace supported placeholders while leaving all other braces untouched."""

    values = {"name": name, "phone": phone}
    return _PLACEHOLDER.sub(lambda match: values[match.group(1).lower()], template)
