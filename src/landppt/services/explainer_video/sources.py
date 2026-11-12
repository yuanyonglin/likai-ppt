"""Read what a video explains: a deck page, a section of an uploaded file, or a description."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database.models import Project, SlideData, SpeechScript
from .page import parse_explainer_video_id

UPLOAD_EXTENSIONS = {".pptx", ".pdf", ".docx", ".md", ".txt"}
UPLOADS_ROOT = Path("uploads") / "explainer_videos" / "sources"


class SourceError(ValueError):
    """A public, actionable problem with the requested source."""


@dataclass
class SourceContent:
    title: str
    text: str
    speech: str = ""
    colors: List[str] = field(default_factory=list)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def dominant_colors(html: str, limit: int = 5) -> List[str]:
    counts: Dict[str, int] = {}
    for color in re.findall(r"#[0-9a-fA-F]{6}\b", html or ""):
        counts[color.upper()] = counts.get(color.upper(), 0) + 1
    return [color for color, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:limit]


async def load_slide_source(session: AsyncSession, *, user_id: int, project_id: str, slide_id: str) -> SourceContent:
    row = await session.scalar(
        select(SlideData)
        .join(Project, Project.project_id == SlideData.project_id)
        .where(SlideData.project_id == project_id, SlideData.slide_id == slide_id, Project.user_id == user_id)
    )
    if row is None:
        raise SourceError("页面不存在或无权访问")
    if parse_explainer_video_id(row.html_content):
        raise SourceError("讲解视频页不能再生成讲解视频")
    script = await session.scalar(
        select(SpeechScript)
        .where(SpeechScript.project_id == project_id, SpeechScript.slide_index == row.slide_index)
        .order_by(SpeechScript.updated_at.desc())
        .limit(1)
    )
    return SourceContent(
        title=row.title or "",
        text=html_to_text(row.html_content),
        speech=(script.script_content if script else "") or "",
        colors=dominant_colors(row.html_content),
    )


# ---- uploads -------------------------------------------------------------

def _upload_dir(user_id: int, file_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", file_id or ""):
        raise SourceError("无效的 file_id")
    return UPLOADS_ROOT / str(int(user_id)) / file_id


def _clean_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if stripped:
            return stripped[:40]
    return ""


def _split_by_headings(markdown: str, max_chars: int = 3000) -> List[Dict[str, str]]:
    blocks: List[List[str]] = [[]]
    for line in (markdown or "").splitlines():
        if re.match(r"^#{1,2}\s+\S", line) and any(item.strip() for item in blocks[-1]):
            blocks.append([])
        blocks[-1].append(line)
    sections: List[Dict[str, str]] = []
    for block in blocks:
        text = _clean_markdown("\n".join(block))
        if not text:
            continue
        # Headingless documents still need pieces short enough to explain in minutes.
        for start in range(0, len(text), max_chars):
            piece = text[start:start + max_chars]
            sections.append({"title": _first_heading(piece) or f"第{len(sections) + 1}节", "text": piece})
    return sections


def _parse_pptx(path: Path) -> List[Dict[str, str]]:
    # Seam for the PPTX-parsing branch: replace with its parser when merged.
    from markitdown import MarkItDown

    markdown = MarkItDown().convert(str(path)).text_content or ""
    parts = re.split(r"<!--\s*Slide number:\s*(\d+)\s*-->", markdown)
    sections = []
    for number, body in zip(parts[1::2], parts[2::2]):
        text = _clean_markdown(body)
        sections.append({"title": _first_heading(text) or f"第{number}页", "text": text})
    return sections


def _parse_pdf(path: Path) -> List[Dict[str, str]]:
    import fitz

    sections = []
    with fitz.open(str(path)) as document:
        for number, page in enumerate(document, start=1):
            text = re.sub(r"[ \t]+", " ", page.get_text("text") or "").strip()
            sections.append({"title": _first_heading(text) or f"第{number}页", "text": text})
    return sections


async def parse_upload_sections(path: Path, filename: str) -> List[Dict[str, Any]]:
    ext = Path(filename).suffix.lower()
    if ext == ".pptx":
        from ...utils.thread_pool import run_blocking_io

        sections = await run_blocking_io(_parse_pptx, path)
    elif ext == ".pdf":
        from ...utils.thread_pool import run_blocking_io

        sections = await run_blocking_io(_parse_pdf, path)
    elif ext in {".docx", ".md", ".txt"}:
        from ..file_processor import FileProcessor

        result = await FileProcessor().process_file(str(path), filename)
        sections = _split_by_headings(result.processed_content)
    else:
        raise SourceError(f"不支持的文件格式：{ext or '(无扩展名)'}")
    sections = [section for section in sections if section["text"].strip()]
    if not sections:
        raise SourceError("文件中没有可讲解的文字内容")
    return [{"index": index, **section} for index, section in enumerate(sections)]


async def store_upload(user_id: int, filename: str, data: bytes) -> Dict[str, Any]:
    ext = Path(filename or "").suffix.lower()
    if ext not in UPLOAD_EXTENSIONS:
        raise SourceError(f"不支持的文件格式：{ext or '(无扩展名)'}，支持 {', '.join(sorted(UPLOAD_EXTENSIONS))}")
    file_id = uuid.uuid4().hex
    directory = _upload_dir(user_id, file_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"source{ext}"
    path.write_bytes(data)
    sections = await parse_upload_sections(path, filename)
    payload = {"file_id": file_id, "filename": Path(filename).name, "sections": sections}
    (directory / "sections.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def load_upload_section(user_id: int, file_id: str, section_index: int) -> SourceContent:
    manifest = _upload_dir(user_id, file_id) / "sections.json"
    if not manifest.exists():
        raise SourceError("上传文件不存在，请重新上传")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    sections = payload.get("sections") or []
    if not 0 <= int(section_index) < len(sections):
        raise SourceError("所选章节不存在")
    section = sections[int(section_index)]
    return SourceContent(title=section.get("title") or payload.get("filename") or "", text=section["text"])


def text_source(title: Optional[str], text: Optional[str]) -> SourceContent:
    title, text = (title or "").strip(), (text or "").strip()
    if not title and not text:
        raise SourceError("请填写标题或描述")
    return SourceContent(title=title or text[:30], text=text or title)
