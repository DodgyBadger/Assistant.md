"""
Render extracted chunks to markdown artifacts.
"""

import base64
import binascii
import json
import os
import re
from datetime import datetime
from pathlib import Path

from core.ingestion.models import ExtractedDocument, RenderOptions
from core.ingestion.output_paths import resolve_import_output_paths
from core.runtime.paths import get_data_root


def default_renderer(doc: ExtractedDocument, options: RenderOptions) -> list[dict]:
    """
    Render a single markdown artifact for the extracted document.
    """
    paths = resolve_import_output_paths(
        path_pattern=options.path_pattern,
        relative_dir=options.relative_dir,
        source_filename=options.source_filename,
        title=options.title,
        output_name=options.output_name,
    )
    rel_path = paths.markdown_path

    display_source_path = None
    if options.source_filename:
        try:
            data_root = Path(get_data_root()).resolve()
            source_path = Path(options.source_filename).resolve()
            if options.vault:
                vault_root = (data_root / options.vault).resolve()
                if str(source_path).startswith(str(vault_root)):
                    display_source_path = str(source_path.relative_to(vault_root))
            if display_source_path is None and str(source_path).startswith(
                str(data_root)
            ):
                display_source_path = str(source_path.relative_to(data_root))
        except Exception:
            display_source_path = options.source_filename
        else:
            if display_source_path is None:
                display_source_path = options.source_filename

    is_remote_source = bool(
        options.source_uri
        and options.source_uri.lower().startswith(("http://", "https://"))
    )
    source_value = (
        options.source_uri
        if is_remote_source
        else os.path.basename(options.source_filename or "")
    )
    effective_source = options.effective_source_uri
    frontmatter: dict[str, object | None] = {
        "source": source_value,
        "source_path": None if is_remote_source else display_source_path,
        "final_url": (
            effective_source
            if is_remote_source and effective_source != options.source_uri
            else None
        ),
        "mime": doc.mime,
        "strategy": doc.strategy_id,
        "fetched_at": datetime.utcnow().isoformat(),
    }

    # Record any attachments the extractor saw but intentionally dropped.
    attachments = doc.meta.get("attachments") if isinstance(doc.meta, dict) else None
    if attachments:
        # Only store the names/identifiers to avoid embedding binary references
        frontmatter["dropped_attachments"] = [str(att) for att in attachments]
    warnings = doc.meta.get("warnings") if isinstance(doc.meta, dict) else None
    if warnings:
        frontmatter["warnings"] = [str(w) for w in warnings]

    image_artifacts, image_count, image_link_map = _render_ocr_image_artifacts(
        doc=doc,
        asset_dir=paths.asset_dir,
        markdown_dir=paths.rel_dir,
    )
    if image_count:
        frontmatter["ocr_images_saved"] = image_count
    metadata_artifact = _render_ocr_metadata_artifact(doc, paths.asset_dir)
    if metadata_artifact is not None:
        frontmatter["ocr_metadata"] = metadata_artifact["path"]

    content = "---\n"
    for key, val in frontmatter.items():
        if val is not None:
            content += f"{key}: {val}\n"
    content += "---\n\n"
    rewritten_text = _rewrite_ocr_image_links(doc.plain_text or "", image_link_map)
    content += rewritten_text

    artifacts: list[dict[str, object]] = [
        {
            "path": rel_path,
            "content": content,
            "meta": frontmatter,
        }
    ]
    artifacts.extend(image_artifacts)
    if metadata_artifact is not None:
        artifacts.append(metadata_artifact)
    return artifacts


def _render_ocr_metadata_artifact(
    doc: ExtractedDocument,
    asset_dir: str,
) -> dict[str, object] | None:
    pages = doc.meta.get("ocr_pages") if isinstance(doc.meta, dict) else None
    if not isinstance(pages, list) or not any(bool(page) for page in pages):
        return None
    payload = {
        "provider": doc.meta.get("provider"),
        "model": doc.meta.get("model"),
        "requested_model": doc.meta.get("requested_model"),
        "transport": doc.meta.get("transport"),
        "usage_info": doc.meta.get("usage_info"),
        "pages": pages,
    }
    return {
        "path": f"{asset_dir.rstrip('/')}/ocr.json",
        "content": json.dumps(payload, ensure_ascii=False, indent=2),
        "meta": {"kind": "ocr_metadata", "source_strategy": doc.strategy_id},
    }


def _render_ocr_image_artifacts(
    doc: ExtractedDocument,
    asset_dir: str,
    markdown_dir: str,
) -> tuple[list[dict], int, dict[str, str]]:
    if not isinstance(doc.meta, dict):
        return [], 0, {}

    raw_items = doc.meta.get("ocr_images")
    if not isinstance(raw_items, list) or not raw_items:
        return [], 0, {}

    artifacts: list[dict] = []
    link_map: dict[str, str] = {}
    image_count = 0
    link_dir = os.path.relpath(asset_dir, start=markdown_dir)
    used_filenames: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        data_base64 = item.get("data_base64")
        if not isinstance(data_base64, str) or not data_base64.strip():
            continue
        try:
            image_bytes = base64.b64decode(data_base64, validate=False)
        except (ValueError, binascii.Error):
            continue
        if not image_bytes:
            continue

        page_number = _as_positive_int(item.get("page_number"), fallback=1)
        image_index = _as_positive_int(
            item.get("image_index"), fallback=image_count + 1
        )
        media_type = str(item.get("media_type") or "image/png").lower()
        ext = _extension_for_media_type(media_type)
        source_name = item.get("source_name")
        if isinstance(source_name, str) and source_name.strip():
            filename = _safe_asset_filename(source_name.strip(), ext)
        else:
            filename = f"page_{page_number:04d}_img_{image_index:02d}{ext}"
        filename = _dedupe_filename(filename, used_filenames)
        rel_path = os.path.join(asset_dir, filename).lstrip("/")
        link_target = os.path.join(link_dir, filename).replace(os.sep, "/")

        if isinstance(source_name, str) and source_name.strip():
            source_basename = os.path.basename(source_name.strip())
            link_map[source_basename] = link_target
            link_map[source_name.strip()] = link_target
        link_map[f"img-{image_count}.jpeg"] = link_target
        link_map[f"img-{image_count}.jpg"] = link_target
        link_map[f"img-{image_count}.png"] = link_target
        link_map[f"img-{image_count}.webp"] = link_target
        link_map[f"img-{image_count}.gif"] = link_target

        artifacts.append(
            {
                "path": rel_path,
                "content_bytes": image_bytes,
                "meta": {
                    "kind": "ocr_image",
                    "source_strategy": doc.strategy_id,
                    "media_type": media_type,
                    "page_number": page_number,
                    "image_index": image_index,
                },
            }
        )
        image_count += 1

    return artifacts, image_count, link_map


def _as_positive_int(value: object, fallback: int) -> int:
    try:
        parsed = int(str(value))
    except Exception:
        return fallback
    return parsed if parsed > 0 else fallback


def _extension_for_media_type(media_type: str) -> str:
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/tiff": ".tiff",
        "image/tif": ".tif",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
    }
    return mapping.get(media_type, ".png")


def _safe_asset_filename(name: str, default_ext: str) -> str:
    base = os.path.basename(name).replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    if not cleaned:
        cleaned = "image"
    root, ext = os.path.splitext(cleaned)
    if not ext:
        cleaned = f"{root}{default_ext}"
    return cleaned


def _dedupe_filename(filename: str, used_filenames: set[str]) -> str:
    if filename not in used_filenames:
        used_filenames.add(filename)
        return filename

    stem, ext = os.path.splitext(filename)
    counter = 1
    while True:
        candidate = f"{stem}_{counter}{ext}"
        if candidate not in used_filenames:
            used_filenames.add(candidate)
            return candidate
        counter += 1


def _rewrite_ocr_image_links(markdown: str, link_map: dict[str, str]) -> str:
    if not markdown or not link_map:
        return markdown

    def replace_md_image(match: re.Match[str]) -> str:
        alt_text = match.group(1)
        destination = match.group(2)
        suffix = match.group(3) or ""
        normalized_destination = destination.strip()
        replacement = link_map.get(normalized_destination) or link_map.get(
            os.path.basename(normalized_destination)
        )
        if not replacement:
            return match.group(0)
        return f"![{alt_text}]({replacement}{suffix})"

    rewritten = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)([^)]*)\)", replace_md_image, markdown)
    rewritten = re.sub(
        r"!\[\[([^\]]+)\]\]",
        lambda m: f"![[{link_map.get(m.group(1).strip(), m.group(1).strip())}]]",
        rewritten,
    )
    return rewritten
