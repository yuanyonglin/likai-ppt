"""Put a finished explainer video into a deck as a page, or undo a replacement."""

from __future__ import annotations

import copy
import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database.models import ExplainerVideo, NarrationAudio, Project, SlideData, SpeechScript
from ...database.service import DatabaseService
from .page import build_video_page_html, build_video_page_metadata, parse_explainer_video_id


class PlacementError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


async def _owned_project(session: AsyncSession, project_id: str, user_id: int) -> Project:
    project = await session.scalar(
        select(Project).where(Project.project_id == project_id, Project.user_id == user_id)
    )
    if project is None:
        raise PlacementError(404, "项目不存在或无权访问")
    return project


async def _slide_rows(session: AsyncSession, project_id: str) -> List[SlideData]:
    return list(
        (
            await session.scalars(
                select(SlideData).where(SlideData.project_id == project_id).order_by(SlideData.slide_index)
            )
        ).all()
    )


def _find_row(rows: List[SlideData], slide_id: str) -> SlideData:
    for row in rows:
        if row.slide_id == slide_id:
            return row
    raise PlacementError(404, "页面不存在")


def _renumber(items: List[Any]) -> None:
    for index, item in enumerate(items):
        if isinstance(item, dict):
            item["page_number"] = index + 1


def _apply_to_json_entry(project: Project, row: SlideData) -> None:
    slides_json = copy.deepcopy(project.slides_data or [])
    if 0 <= row.slide_index < len(slides_json) and isinstance(slides_json[row.slide_index], dict):
        entry = slides_json[row.slide_index]
        entry.update(
            {
                "slide_id": row.slide_id,
                "title": row.title,
                "html_content": row.html_content,
                "metadata": copy.deepcopy(row.slide_metadata or {}),
                "is_user_edited": row.is_user_edited,
            }
        )
        project.slides_data = slides_json


async def _insert_after(
    session: AsyncSession, video: ExplainerVideo, project: Project, user_id: int, after_slide_id: str
) -> Dict[str, Any]:
    anchor = _find_row(await _slide_rows(session, project.project_id), after_slide_id)
    insert_index = anchor.slide_index + 1

    # Shifts speech scripts and narration audio of the later pages.
    ok = await DatabaseService(session).apply_slide_structure_operation(
        project.project_id, {"type": "insert", "from_index": insert_index}, user_id=user_id
    )
    if not ok:
        raise PlacementError(400, "插入页面失败")

    for row in await _slide_rows(session, project.project_id):
        if row.slide_index >= insert_index:
            row.slide_index += 1
            row.updated_at = time.time()

    metadata = build_video_page_metadata(video.id)
    new_row = SlideData(
        project_id=project.project_id,
        slide_index=insert_index,
        slide_id=uuid.uuid4().hex,
        title=video.title or "讲解视频",
        content_type="content",
        html_content=build_video_page_html(video.id),
        slide_metadata=metadata,
        is_user_edited=True,
    )
    session.add(new_row)

    slides_json = copy.deepcopy(project.slides_data or [])
    slides_json.insert(
        min(insert_index, len(slides_json)),
        {
            "slide_id": new_row.slide_id,
            "title": new_row.title,
            "html_content": new_row.html_content,
            "slide_type": "content",
            "content_type": "content",
            "metadata": copy.deepcopy(metadata),
            "is_user_edited": True,
        },
    )
    _renumber(slides_json)
    project.slides_data = slides_json

    outline = copy.deepcopy(project.outline)
    if isinstance(outline, dict) and isinstance(outline.get("slides"), list):
        outline["slides"].insert(
            min(insert_index, len(outline["slides"])),
            {
                "title": new_row.title,
                "slide_type": "content",
                "type": "content",
                "description": "讲解视频",
                "content_points": [],
            },
        )
        _renumber(outline["slides"])
        project.outline = outline

    project.updated_at = time.time()
    await session.commit()
    return {"slide_id": new_row.slide_id, "slide_index": insert_index}


async def _replace(
    session: AsyncSession, video: ExplainerVideo, project: Project, replace_slide_id: str
) -> Dict[str, Any]:
    row = _find_row(await _slide_rows(session, project.project_id), replace_slide_id)
    if parse_explainer_video_id(row.html_content):
        raise PlacementError(400, "该页已是讲解视频页")

    backup = copy.deepcopy(video.replaced_slide_backup or {})
    backup[row.slide_id] = {
        "project_id": project.project_id,
        "title": row.title,
        "content_type": row.content_type,
        "html_content": row.html_content,
        "slide_metadata": copy.deepcopy(row.slide_metadata),
        "is_user_edited": row.is_user_edited,
    }
    video.replaced_slide_backup = backup

    row.title = video.title or row.title
    row.html_content = build_video_page_html(video.id)
    row.slide_metadata = build_video_page_metadata(video.id)
    row.is_user_edited = True
    row.updated_at = time.time()
    _apply_to_json_entry(project, row)

    # The page's old narration no longer matches what is shown.
    for model in (SpeechScript, NarrationAudio):
        await session.execute(
            delete(model).where(model.project_id == project.project_id, model.slide_index == row.slide_index)
        )

    project.updated_at = time.time()
    await session.commit()
    return {"slide_id": row.slide_id, "slide_index": row.slide_index}


async def place_video(
    session: AsyncSession,
    video: ExplainerVideo,
    *,
    user_id: int,
    project_id: str,
    after_slide_id: Optional[str] = None,
    replace_slide_id: Optional[str] = None,
) -> Dict[str, Any]:
    if bool(after_slide_id) == bool(replace_slide_id):
        raise PlacementError(400, "after_slide_id 与 replace_slide_id 必须二选一")
    if video.status != "completed":
        raise PlacementError(409, "视频尚未生成完成")
    project = await _owned_project(session, project_id, user_id)
    if replace_slide_id:
        return await _replace(session, video, project, replace_slide_id)
    return await _insert_after(session, video, project, user_id, after_slide_id)


async def restore_slide(
    session: AsyncSession, video: ExplainerVideo, *, user_id: int, project_id: str, slide_id: str
) -> Dict[str, Any]:
    project = await _owned_project(session, project_id, user_id)
    backup = copy.deepcopy(video.replaced_slide_backup or {})
    saved = backup.get(slide_id)
    if not saved or saved.get("project_id") != project_id:
        raise PlacementError(404, "没有可恢复的原页面")
    row = _find_row(await _slide_rows(session, project_id), slide_id)

    row.title = saved["title"]
    row.content_type = saved.get("content_type") or row.content_type
    row.html_content = saved["html_content"]
    row.slide_metadata = saved.get("slide_metadata")
    row.is_user_edited = bool(saved.get("is_user_edited"))
    row.updated_at = time.time()
    _apply_to_json_entry(project, row)

    backup.pop(slide_id)
    video.replaced_slide_backup = backup or None
    project.updated_at = time.time()
    await session.commit()
    return {"slide_id": row.slide_id, "slide_index": row.slide_index}


async def list_placements(session: AsyncSession, video: ExplainerVideo) -> List[Dict[str, Any]]:
    rows = (
        await session.execute(
            select(SlideData.project_id, SlideData.slide_id, SlideData.html_content)
            .join(Project, Project.project_id == SlideData.project_id)
            .where(
                Project.user_id == video.user_id,
                SlideData.html_content.contains("data-explainer-video-id"),
                SlideData.html_content.contains(video.id),
            )
        )
    ).all()
    backup = video.replaced_slide_backup or {}
    return [
        {
            "project_id": row.project_id,
            "slide_id": row.slide_id,
            "mode": "replace" if row.slide_id in backup else "insert",
        }
        for row in rows
        if parse_explainer_video_id(row.html_content) == video.id
    ]
