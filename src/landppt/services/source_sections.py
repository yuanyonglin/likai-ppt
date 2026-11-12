"""Split an uploaded source file into sections an explainer video can cover.

PPTX and PDF become one section per page, Word and Markdown one per heading.
Pages that only hold a picture are marked ``image_only`` and filled with local
OCR text; ``describe_section_image`` reads one such page with the configured
vision model when the caller wants a better description.
"""

from __future__ import annotations

import asyncio
import base64
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import fitz
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
# Text boxes wider than this share of the slide are titles or banners, not columns.
WIDE_SHAPE = 0.6
# Left edges closer than this share of the slide belong to the same column.
COLUMN_GAP = 0.08


IMAGE_HINT = (
    "（这一页是图片，未识别出文字。请在系统设置中配置“多模态视觉分析模型”后读取图片内容，"
    "或改用“直接描述”。）"
)


def _section(index, title, text, image_only=False):
    if image_only and not text:
        text = IMAGE_HINT
    return {"index": index, "title": title, "text": text, "image_only": image_only}


def _first_line(text, fallback):
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:60]
    return fallback


# ---------------------------------------------------------------- PPTX


def _walk(shapes):
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk(shape.shapes)
        else:
            yield shape


def _chart_text(chart):
    """Series values from the chart XML; python-pptx's plot API rejects 3D,
    stock and surface charts."""
    lines = []
    for series in chart._chartSpace.xpath(".//c:ser"):
        name = "".join(series.xpath("./c:tx//c:v/text()")) or "数据"
        categories = series.xpath("./c:cat//c:pt/c:v/text()")
        values = series.xpath("./c:val//c:pt/c:v/text()")
        pairs = zip(categories, values) if categories else enumerate(values, 1)
        lines.append(f"{name}：" + "，".join(f"{c} {v}" for c, v in pairs))
    return "图表数据\n" + "\n".join(lines) if lines else ""


def _shape_text(shape):
    if shape.has_text_frame:
        return shape.text_frame.text.strip()
    if getattr(shape, "has_table", False) and shape.has_table:
        rows = [
            " | ".join(cell.text.strip() for cell in row.cells)
            for row in shape.table.rows
        ]
        return "\n".join(rows)
    if getattr(shape, "has_chart", False) and shape.has_chart:
        return _chart_text(shape.chart)
    return ""


def _picture_blob(shape):
    """Embedded image bytes; linked pictures raise ValueError and have none."""
    try:
        return shape.image.blob
    except (AttributeError, ValueError):
        return None


def _font_size(shape):
    if not shape.has_text_frame:
        return 0
    return max(
        (run.font.size for p in shape.text_frame.paragraphs for run in p.runs if run.font.size),
        default=0,
    )


def _columns(boxes, slide_width):
    """Group boxes into columns by left edge and read them left to right.

    Exported decks often use one box per line whose width spills into the next
    column, so right edges and overlap are unreliable; left edges are not.
    """
    columns = []
    for box in sorted(boxes, key=lambda b: b["left"]):
        if columns and box["left"] - columns[-1][0]["left"] <= COLUMN_GAP * slide_width:
            columns[-1].append(box)
        else:
            columns.append([box])
    return [b for column in columns for b in sorted(column, key=lambda b: b["top"])]


def _ordered_boxes(boxes, slide_width):
    """Wide boxes (titles, banners) cut the slide into horizontal bands; the
    narrow boxes in each band are read column by column."""
    wide = sorted((b for b in boxes if b["wide"]), key=lambda b: b["top"])
    bands = [[] for _ in range(len(wide) + 1)]
    for box in boxes:
        if not box["wide"]:
            bands[sum(1 for w in wide if w["top"] <= box["top"])].append(box)
    ordered = _columns(bands[0], slide_width)
    for banner, band in zip(wide, bands[1:]):
        ordered += [banner] + _columns(band, slide_width)
    return ordered


def _parse_slide(slide, slide_width):
    boxes, pictures = [], []
    for shape in _walk(slide.shapes):
        if shape.width is None or shape.left is None:
            continue
        blob = _picture_blob(shape)
        if blob:
            pictures.append((shape.width * shape.height, blob))
        text = _shape_text(shape)
        if text:
            boxes.append(
                {
                    "text": text,
                    "left": shape.left,
                    "top": shape.top,
                    "wide": shape.width > WIDE_SHAPE * slide_width,
                    "size": _font_size(shape),
                }
            )
    ordered = _ordered_boxes(boxes, slide_width)
    title_shape = slide.shapes.title
    title = title_shape.text_frame.text.strip() if title_shape is not None else ""
    if not title and ordered:
        # No title placeholder: the largest font among the first few worded
        # boxes, so a small tag above the title loses and KPI figures never win.
        words = [b for b in ordered if re.search(r"[^\W\d_]", b["text"])][:3] or ordered
        title = _first_line(max(words, key=lambda b: b["size"])["text"], "")
    body = "\n".join(b["text"] for b in ordered)
    picture = max(pictures, key=lambda p: p[0])[1] if pictures else None
    return title, body, picture


def _parse_pptx(path):
    prs = Presentation(path)
    sections = []
    for index, slide in enumerate(prs.slides):
        title, body, picture = _parse_slide(slide, prs.slide_width)
        image_only = not body and picture is not None
        if image_only:
            body = _ocr(picture)
        notes = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
        text = body + (f"\n\n备注：{notes}" if notes else "")
        sections.append(
            _section(index, title or _first_line(body, f"第 {index + 1} 页"), text.strip(), image_only)
        )
    return sections


# ---------------------------------------------------------------- PDF


def _page_png(page):
    return page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")


def _parse_pdf(path):
    sections = []
    with fitz.open(path) as doc:
        for index, page in enumerate(doc):
            text = page.get_text("text", sort=True).strip()
            image_only = not text
            if image_only:
                text = _ocr(_page_png(page))
            sections.append(_section(index, _first_line(text, f"第 {index + 1} 页"), text, image_only))
    return sections


# ---------------------------------------------------------------- Word / Markdown / text


def _split_by_headings(blocks, fallback_title):
    """``blocks`` are (heading_level or None, text). Split at the highest level
    that yields more than one section; leading text keeps the file name."""
    levels = sorted({level for level, _ in blocks if level})
    split_level = next(
        (lv for lv in levels if sum(1 for level, _ in blocks if level and level <= lv) > 1),
        levels[-1] if levels else None,
    )
    sections, title, lines = [], fallback_title, []

    def flush():
        text = "\n".join(lines).strip()
        if text or title != fallback_title:
            sections.append(_section(len(sections), title, text))

    for level, text in blocks:
        if level and level <= split_level:
            flush()
            title, lines = text, []
        else:
            lines.append(text)
    flush()
    return sections or [_section(0, fallback_title, "")]


def _docx_heading_level(paragraph):
    name = (paragraph.style.name or "") if paragraph.style is not None else ""
    if name == "Title":
        return 1
    match = re.match(r"(?:Heading|标题)\s*(\d)", name)
    return int(match.group(1)) + 1 if match else None


def _parse_docx(path, fallback_title):
    doc = Document(path)
    blocks = []
    for element in doc.element.body.iterchildren():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(element, doc)
            text = paragraph.text.strip()
            if text:
                blocks.append((_docx_heading_level(paragraph), text))
        elif tag == "tbl":
            rows = [
                " | ".join(cell.text.strip() for cell in row.cells)
                for row in Table(element, doc).rows
            ]
            blocks.append((None, "\n".join(rows)))
    return _split_by_headings(blocks, fallback_title)


def _parse_markdown(text, fallback_title):
    blocks, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        match = None if fenced else re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            blocks.append((len(match.group(1)), match.group(2).strip()))
        else:
            blocks.append((None, line))
    return _split_by_headings(blocks, fallback_title)


# ---------------------------------------------------------------- images


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _ocr(image_bytes):
    try:
        Image.open(BytesIO(image_bytes)).verify()
    except Exception:
        return ""  # EMF/WMF and other formats OCR cannot read
    result, _ = _ocr_engine()(image_bytes)
    return "\n".join(line[1] for line in result or [])


def _parse_image(path, fallback_title):
    text = _ocr(Path(path).read_bytes())
    return [_section(0, _first_line(text, fallback_title), text, True)]


# ---------------------------------------------------------------- public API


def _parse(path, filename):
    suffix = Path(filename).suffix.lower()
    stem = Path(filename).stem
    if suffix == ".pptx":
        return _parse_pptx(path)
    if suffix == ".pdf":
        return _parse_pdf(path)
    if suffix == ".docx":
        return _parse_docx(path, stem)
    if suffix in {".md", ".txt"}:
        raw = Path(path).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gbk", errors="replace")  # files saved by Chinese Windows
        if suffix == ".txt":
            return [_section(0, stem, text.strip())]
        return _parse_markdown(text, stem)
    if suffix in IMAGE_SUFFIXES:
        return _parse_image(path, stem)
    raise ValueError(f"不支持的文件格式：{suffix}")


async def parse_source_sections(file_path: str, filename: str) -> list[dict]:
    """Return ``[{index, title, text, image_only}]``; ``index`` starts at 0."""
    return await asyncio.to_thread(_parse, file_path, filename)


def sections_to_text(sections: list[dict], unit: str = "页") -> str:
    """Join sections into one document for the existing outline flow."""
    def body(section):
        first, _, rest = section["text"].partition("\n")
        return rest.strip() if first.strip() == section["title"] else section["text"]

    return "\n\n".join(
        f"## 第 {s['index'] + 1} {unit}：{s['title']}\n{body(s)}".strip() for s in sections
    )


def section_image(file_path: str, filename: str, index: int) -> bytes | None:
    """PNG of the picture that fills an image-only page, or of the PDF page."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pptx":
        prs = Presentation(file_path)
        raw = _parse_slide(prs.slides[index], prs.slide_width)[2]
    elif suffix == ".pdf":
        with fitz.open(file_path) as doc:
            return _page_png(doc[index])
    elif suffix in IMAGE_SUFFIXES:
        raw = Path(file_path).read_bytes()
    else:
        return None
    if raw is None:
        return None
    try:
        image = Image.open(BytesIO(raw))
        out = BytesIO()
        image.convert("RGB").save(out, "PNG")
        return out.getvalue()
    except Exception:
        return None


VISION_PROMPT = (
    "这是一页演示文稿的图片。请完整提取页面上的文字，并按阅读顺序整理成要点；"
    "图表请写出关键数据和结论，流程图写出步骤关系。只输出内容本身，不要解释。"
)


async def describe_section_image(
    file_path: str, filename: str, index: int, user_id: int
) -> str | None:
    """Read one page with the user's vision model; ``None`` when no vision
    model is configured or the page has no readable picture."""
    from ..ai.base import AIMessage, ImageContent, MessageRole, TextContent
    from .db_config_service import get_db_config_service
    from .service_instances import get_ppt_service_for_user

    config = await get_db_config_service().get_all_config(user_id=user_id)
    # The role lookup silently falls back to the default text model, which cannot see.
    if not config.get("vision_analysis_model_name"):
        return None
    image = await asyncio.to_thread(section_image, file_path, filename, index)
    if image is None:
        return None
    url = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
    response = await get_ppt_service_for_user(user_id)._chat_completion_for_role(
        "vision_analysis",
        messages=[
            AIMessage(
                role=MessageRole.USER,
                content=[TextContent(text=VISION_PROMPT), ImageContent(image_url={"url": url})],
            )
        ],
    )
    return response.content.strip()
