from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

from nekotodo.storage import detect_image_mime

# 上限:防止超长文档撑爆上下文或超大文件拖垮抽取
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PAGES = 60
MAX_IMAGES = 30
MAX_TEXT_CHARS = 40_000

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".yaml", ".yml"}

SUPPORTED_HINT = (
    "支持的类型:图片(png/jpeg/webp/gif)、"
    ".docx / .pptx / .pdf、纯文本(.txt/.md/.csv/.json 等)。"
    "老的 .doc / .ppt / .xls 请先另存为对应的新格式。"
)


@dataclass
class ExtractedImage:
    data: bytes
    mime: str
    label: str


@dataclass
class ExtractedDocument:
    """Ordered digest of a document: text spans and embedded images interleaved."""

    blocks: list[tuple[str, object]] = field(default_factory=list)
    truncated: bool = False

    def add_text(self, text: str) -> None:
        text = text.strip()
        if text:
            self.blocks.append(("text", text))

    def add_image(self, image: ExtractedImage) -> None:
        self.blocks.append(("image", image))


def extension_of(filename: str) -> str:
    name = (filename or "").lower()
    return name[name.rfind(".") :] if "." in name else ""


def detect_kind(data: bytes, filename: str) -> tuple[str, str] | None:
    """Return ``(kind, mime)`` for a supported upload, else None.

    Images are sniffed from magic bytes; documents from magic bytes plus the
    filename extension (text formats have no magic of their own).
    """
    mime = detect_image_mime(data)
    if mime is not None:
        return "image", mime
    if data.startswith(b"%PDF-"):
        return "document", "application/pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return None
        if any(name.startswith("word/") for name in names):
            return "document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if any(name.startswith("ppt/") for name in names):
            return "document", "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        return None
    if extension_of(filename) in TEXT_EXTENSIONS:
        return "document", "text/plain"
    return None


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_document(data: bytes, filename: str, mime: str) -> ExtractedDocument:
    """Extract a document into an ordered list of text/image blocks."""
    if mime == "application/pdf":
        return _extract_pdf(data, filename)
    if mime.endswith("wordprocessingml.document"):
        return _extract_docx(data, filename)
    if mime.endswith("presentationml.presentation"):
        return _extract_pptx(data, filename)
    document = ExtractedDocument()
    document.add_text(_decode_text(data))
    return document


# ---------------------------------------------------------------------------
# pdf / docx / pptx
# ---------------------------------------------------------------------------


def _extract_pdf(data: bytes, filename: str) -> ExtractedDocument:
    import pymupdf

    document = ExtractedDocument()
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        for index, page in enumerate(pdf):
            if index >= MAX_PAGES:
                document.truncated = True
                break
            document.add_text(f"[第 {index + 1} 页文本]\n{page.get_text()}")
            if index < MAX_IMAGES:
                pixmap = page.get_pixmap(dpi=110)
                document.add_image(
                    ExtractedImage(pixmap.tobytes("png"), "image/png", f"{filename}#第 {index + 1} 页")
                )
    return document


def _extract_docx(data: bytes, filename: str) -> ExtractedDocument:
    import docx
    from docx.oxml.ns import qn

    document = ExtractedDocument()
    parsed = docx.Document(io.BytesIO(data))
    body = parsed.element.body
    image_count = 0
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            text = "".join(node.text or "" for node in child.iter(qn("w:t"))).strip()
            document.add_text(text)
            for blip in child.iter(qn("a:blip")):
                embed = blip.get(qn("r:embed"))
                if not embed:
                    continue
                part = parsed.part.related_parts.get(embed)
                if part is None or image_count >= MAX_IMAGES:
                    continue
                mime = detect_image_mime(part.blob) or "application/octet-stream"
                image_count += 1
                document.add_image(
                    ExtractedImage(part.blob, mime, f"{filename}#图 {image_count}")
                )
        elif child.tag == qn("w:tbl"):
            rows = []
            for row in child.iter(qn("w:tr")):
                cells = [
                    "".join(node.text or "" for node in cell.iter(qn("w:t"))).strip()
                    for cell in row.iter(qn("w:tc"))
                ]
                rows.append(" | ".join(cells))
            document.add_text("\n".join(rows))
    return document


def _extract_pptx(data: bytes, filename: str) -> ExtractedDocument:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    document = ExtractedDocument()
    presentation = Presentation(io.BytesIO(data))
    image_count = 0
    for index, slide in enumerate(presentation.slides):
        if index >= MAX_PAGES:
            document.truncated = True
            break
        document.add_text(f"[第 {index + 1} 页]")
        for shape in slide.shapes:  # XML 顺序 = 阅读顺序
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                if image_count >= MAX_IMAGES:
                    continue
                blob = shape.image.blob
                mime = detect_image_mime(blob) or "image/octet-stream"
                image_count += 1
                document.add_image(
                    ExtractedImage(blob, mime, f"{filename}#第 {index + 1} 页-图 {image_count}")
                )
            elif shape.has_text_frame:
                document.add_text(shape.text_frame.text)
            elif shape.has_table:
                rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in shape.table.rows]
                document.add_text("\n".join(rows))
    return document


def render_digest(document: ExtractedDocument, image_ids: list[int | None]) -> str:
    """Serialize blocks in order, replacing images with their SourceFile markers.

    ``image_ids`` holds the persisted row id for each image block (by index),
    supplied by the caller after the rows are created.
    """
    lines: list[str] = []
    image_index = 0
    for kind, payload in document.blocks:
        if kind == "text":
            lines.append(str(payload))
        else:
            row_id = image_ids[image_index] if image_index < len(image_ids) else None
            image_index += 1
            marker = f"id {row_id}" if row_id is not None else "未保存"
            lines.append(f"[图片 {marker}]: {payload.label}")
    if document.truncated:
        lines.append("(内容过长,已截断)")
    text = "\n".join(lines)
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n(内容过长,已截断)"
    return text


def dump_images(document: ExtractedDocument) -> list[ExtractedImage]:
    return [payload for kind, payload in document.blocks if kind == "image"]
