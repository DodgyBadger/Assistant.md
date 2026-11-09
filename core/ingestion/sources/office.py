"""Binary Word and PowerPoint sources for provider-side document extraction."""

from pathlib import Path

from core.ingestion.models import RawDocument, SourceKind
from core.ingestion.registry import importer_registry

OFFICE_FILE_TYPES = {
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx_ocr",
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx_ocr",
    ),
}


def load_office_from_path(path: Path) -> RawDocument:
    file_type = OFFICE_FILE_TYPES.get(path.suffix.lower())
    if file_type is None:
        raise ValueError(f"Unsupported Office document extension: {path.suffix}")
    return RawDocument(
        source_uri=str(path),
        kind=SourceKind.FILE,
        mime=file_type[0],
        payload=path.read_bytes(),
        suggested_title=path.stem,
        meta={"filename": path.name},
    )


for extension, (mime_type, _) in OFFICE_FILE_TYPES.items():
    importer_registry.register(extension, load_office_from_path)
    importer_registry.register(mime_type, load_office_from_path)
