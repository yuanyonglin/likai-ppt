"""
Local files behind explainer video pages, for exports.

Video pages are recognised by ``data-explainer-video-id`` in ``html_content``;
their MP4 and cover live in ``explainer_videos.video_path/cover_path``.
"""

from __future__ import annotations

import asyncio
import html
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ..explainer_video.page import parse_explainer_video_id

PROJECT_ROOT = Path(__file__).resolve().parents[4]
EXPLAINER_VIDEO_MARKER = "data-explainer-video-id"


@dataclass(frozen=True)
class ExplainerVideoAsset:
    video_id: str
    video_path: Path
    cover_path: Path


def _query_video_files(video_id: str) -> Optional[tuple[str, str]]:
    """Read stored paths from ``explainer_videos`` (table owned by the generation branch)."""
    from sqlalchemy import text

    from ...database.database import SessionLocal

    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT video_path, cover_path FROM explainer_videos WHERE id = :id"),
            {"id": video_id},
        ).first()
        return (row[0], row[1]) if row else None
    finally:
        db.close()


def _local_file(video_id: str, key: str) -> Path:
    """Map an artifact storage key to a local file; remote storage is downloaded into a cache."""
    from ..storage.factory import get_artifact_storage
    from ..storage.local_storage import LocalArtifactStorage

    storage = get_artifact_storage()
    if isinstance(storage, LocalArtifactStorage):
        return storage._path_for_key(key)
    target = PROJECT_ROOT / "temp" / "explainer_video_cache" / video_id / Path(key).name
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)

        async def download():
            with open(target, "wb") as handle:
                async for chunk in storage.open_stream(key):
                    handle.write(chunk)

        # Callers are synchronous and may sit inside a running event loop.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(asyncio.run, download()).result()
    return target


def load_explainer_video_asset(video_id: str) -> ExplainerVideoAsset:
    """Resolve a video id to existing local files; raise when the video cannot be exported."""
    row = _query_video_files(video_id)
    if row is None or not row[0] or not row[1]:
        raise FileNotFoundError(f"Explainer video {video_id} has no rendered video/cover")
    video_path, cover_path = _local_file(video_id, row[0]), _local_file(video_id, row[1])
    for path in (video_path, cover_path):
        if not path.is_file():
            raise FileNotFoundError(f"Explainer video {video_id} file missing: {path}")
    return ExplainerVideoAsset(video_id=video_id, video_path=video_path, cover_path=cover_path)


def explainer_video_pages(slides: Iterable[Optional[Dict[str, Any]]]) -> Dict[int, ExplainerVideoAsset]:
    """Map zero-based slide index to its video files, for video pages only."""
    pages = {}
    for index, slide in enumerate(slides):
        video_id = parse_explainer_video_id((slide or {}).get("html_content") or "")
        if video_id:
            pages[index] = load_explainer_video_asset(video_id)
    return pages


def explainer_video_page_indices(slides: Iterable[Optional[Dict[str, Any]]]) -> set[int]:
    """Zero-based indices of video pages (no file lookup)."""
    return {
        index
        for index, slide in enumerate(slides)
        if parse_explainer_video_id((slide or {}).get("html_content") or "")
    }


def swap_video_page_for_cover(html_file_path: str) -> str:
    """For a rendered video page, write a cover-only page next to it and return its path."""
    source = Path(html_file_path)
    content = source.read_text(encoding="utf-8", errors="ignore")
    if EXPLAINER_VIDEO_MARKER not in content:
        return html_file_path
    video_id = parse_explainer_video_id(content)
    if not video_id:
        return html_file_path

    cover_uri = html.escape(load_explainer_video_asset(video_id).cover_path.resolve().as_uri(), quote=True)
    cover_file = source.with_name(f"{source.stem}.explainer-cover.html")
    cover_file.write_text(
        "<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><style>"
        "html,body{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#000}"
        "img{display:block;width:100%;height:100%;object-fit:contain}"
        "</style></head><body>"
        f"<img src=\"{cover_uri}\" alt=\"\">"
        "</body></html>",
        encoding="utf-8",
    )
    return str(cover_file)
