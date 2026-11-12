"""Explainer video API: parse uploads, generate, place into decks, serve files."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...auth.middleware import get_current_user_required
from ...core.config import app_config
from ...database.models import ExplainerVideo, Project, User
from ...services.explainer_video import service, sources, tts
from ...services.explainer_video.llm import ScriptError, normalize_script
from ...services.explainer_video.placement import PlacementError, list_placements, place_video, restore_slide

router = APIRouter()


def get_session_factory():
    from ...database.database import AsyncSessionLocal

    return AsyncSessionLocal


class SourceRequest(BaseModel):
    type: Literal["slide", "upload", "text"]
    project_id: Optional[str] = None
    slide_id: Optional[str] = None
    file_id: Optional[str] = None
    section_index: int = 0
    title: Optional[str] = None
    text: Optional[str] = Field(default=None, max_length=20000)


class OptionsRequest(BaseModel):
    duration: Literal["1m", "2m", "3m"] = "1m"
    tts_provider: str = "auto"
    voice: Optional[str] = None
    style: Literal["follow_ppt", "business_blue", "dark_tech"] = "follow_ppt"
    subtitles: bool = True


class PlacementRequest(BaseModel):
    mode: Literal["insert", "replace", "none"] = "none"
    project_id: Optional[str] = None
    slide_id: Optional[str] = None


class CreateRequest(BaseModel):
    source: SourceRequest
    extra: str = Field(default="", max_length=2000)
    options: OptionsRequest = OptionsRequest()
    placement: PlacementRequest = PlacementRequest()


class PlaceRequest(BaseModel):
    project_id: str
    after_slide_id: Optional[str] = None
    replace_slide_id: Optional[str] = None


class RestoreRequest(BaseModel):
    project_id: str
    slide_id: str


class RegenerateRequest(BaseModel):
    script: Optional[Dict[str, Any]] = None


async def _owned_video(session, video_id: str, user: User) -> ExplainerVideo:
    video = await session.get(ExplainerVideo, video_id)
    if video is None or video.user_id != user.id:
        raise HTTPException(404, "讲解视频不存在")
    return video


def _response(video: ExplainerVideo, placements=None, task_id: Optional[str] = None) -> Dict[str, Any]:
    body = service.serialize(video, placements)
    task_id = task_id or video.task_id
    body["polling_endpoint"] = f"/api/explainer-videos/{video.id}"
    body["task_polling_endpoint"] = f"/api/landppt/tasks/{task_id}" if task_id else None
    return body


@router.post("/api/explainer-videos/parse-upload")
async def parse_upload(file: UploadFile = File(...), user: User = Depends(get_current_user_required)):
    data = await file.read()
    if len(data) > max(int(getattr(app_config, "max_file_size", 0) or 0), 50 * 1024 * 1024):
        raise HTTPException(413, "文件过大")
    try:
        return await sources.store_upload(user.id, file.filename or "", data)
    except sources.SourceError as exc:
        raise HTTPException(400, str(exc)) from None
    except ValueError as exc:  # file_processor wraps parse failures as ValueError
        raise HTTPException(400, str(exc)) from None


@router.post("/api/explainer-videos")
async def create_video(
    request: CreateRequest,
    user: User = Depends(get_current_user_required),
    session_factory=Depends(get_session_factory),
):
    source = request.source
    try:
        await tts.resolve_provider(user.id, request.options.tts_provider)
        async with session_factory() as session:
            if source.project_id:
                owned = await session.scalar(
                    select(Project.project_id).where(Project.project_id == source.project_id, Project.user_id == user.id)
                )
                if owned is None:
                    raise HTTPException(404, "项目不存在或无权访问")
            if source.type == "slide":
                if not source.project_id or not source.slide_id:
                    raise HTTPException(400, "slide 来源需要 project_id 和 slide_id")
                title = (await sources.load_slide_source(
                    session, user_id=user.id, project_id=source.project_id, slide_id=source.slide_id
                )).title
                ref = {"project_id": source.project_id, "slide_id": source.slide_id}
            elif source.type == "upload":
                title = sources.load_upload_section(user.id, source.file_id or "", source.section_index).title
                ref = {"file_id": source.file_id, "section_index": source.section_index}
            else:
                title = sources.text_source(source.title, source.text).title
                ref = {"title": source.title or "", "text": source.text or ""}

            placement = request.placement.model_dump()
            placement["project_id"] = placement.get("project_id") or source.project_id
            video = ExplainerVideo(
                id=service.new_video_id(),
                user_id=user.id,
                project_id=placement["project_id"],
                source_type=source.type,
                source_ref=ref,
                options_json={**request.options.model_dump(), "extra": request.extra, "placement": placement},
                title=title or "讲解视频",
                status="pending",
            )
            session.add(video)
            await session.commit()
    except (sources.SourceError, tts.TTSError) as exc:
        raise HTTPException(400, str(exc)) from None

    task_id = await service.submit_generation(video)
    async with session_factory() as session:
        video = await session.get(ExplainerVideo, video.id)
        video.task_id = task_id
        await session.commit()
    return {"video_id": video.id, "task_id": task_id, **_response(video, task_id=task_id)}


@router.get("/api/explainer-videos")
async def list_videos(
    project_id: Optional[str] = Query(default=None, max_length=100),
    user: User = Depends(get_current_user_required),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        stmt = select(ExplainerVideo).where(ExplainerVideo.user_id == user.id)
        if project_id:
            stmt = stmt.where(ExplainerVideo.project_id == project_id)
        videos = (await session.scalars(stmt.order_by(ExplainerVideo.created_at.desc()).limit(200))).all()
        return {"videos": [_response(video) for video in videos]}


@router.get("/api/explainer-videos/{video_id}")
async def get_video(
    video_id: str,
    user: User = Depends(get_current_user_required),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        video = await _owned_video(session, video_id, user)
        return _response(video, await list_placements(session, video))


@router.post("/api/explainer-videos/{video_id}/place")
async def place(
    video_id: str,
    request: PlaceRequest,
    user: User = Depends(get_current_user_required),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        video = await _owned_video(session, video_id, user)
        try:
            return await place_video(
                session, video, user_id=user.id, project_id=request.project_id,
                after_slide_id=request.after_slide_id, replace_slide_id=request.replace_slide_id,
            )
        except PlacementError as exc:
            raise HTTPException(exc.status_code, exc.message) from None


@router.post("/api/explainer-videos/{video_id}/restore")
async def restore(
    video_id: str,
    request: RestoreRequest,
    user: User = Depends(get_current_user_required),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        video = await _owned_video(session, video_id, user)
        try:
            return await restore_slide(
                session, video, user_id=user.id, project_id=request.project_id, slide_id=request.slide_id
            )
        except PlacementError as exc:
            raise HTTPException(exc.status_code, exc.message) from None


@router.post("/api/explainer-videos/{video_id}/regenerate")
async def regenerate(
    video_id: str,
    request: RegenerateRequest,
    user: User = Depends(get_current_user_required),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        video = await _owned_video(session, video_id, user)
        if video.status in {"pending", "running"}:
            raise HTTPException(409, "视频正在生成中")
        if request.script is not None:
            try:
                video.script_json = normalize_script(request.script, video.title)
            except ScriptError as exc:
                raise HTTPException(400, str(exc)) from None
        # Already placed pages keep pointing at this id; only the files change.
        options = dict(video.options_json or {})
        options["placement"] = {"mode": "none"}
        video.options_json = options
        video.status, video.stage, video.error = "pending", None, None
        await session.commit()
    task_id = await service.submit_generation(video, reuse_script=request.script is not None)
    async with session_factory() as session:
        video = await session.get(ExplainerVideo, video_id)
        video.task_id = task_id
        await session.commit()
    return {"video_id": video_id, "task_id": task_id, **_response(video, task_id=task_id)}


# ---- files: public by unguessable id, like /api/image/view/ (share pages, exports) ----

async def _public_video(session_factory, video_id: str) -> ExplainerVideo:
    async with session_factory() as session:
        video = await session.get(ExplainerVideo, video_id)
    if video is None or video.status != "completed" or not video.artifact_id:
        raise HTTPException(404, "视频不存在")
    return video


@router.api_route("/api/explainer-videos/{video_id}/video.mp4", methods=["GET", "HEAD"])
async def video_file(video_id: str, request: Request, session_factory=Depends(get_session_factory)):
    from ...services.artifact_preview_service import preview_response
    from ...services.storage import get_artifact_service
    from ...services.storage.factory import get_artifact_storage

    video = await _public_video(session_factory, video_id)
    artifact = await get_artifact_service().get_artifact(video.artifact_id)
    if artifact is None:
        raise HTTPException(404, "视频文件不存在")
    response = await preview_response(
        artifact, get_artifact_storage(), method=request.method,
        range_header=request.headers.get("range"), if_range=request.headers.get("if-range"),
    )
    # Exports load slides from file:// pages; same-origin would block the video there.
    response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    response.headers["Cache-Control"] = "no-cache"
    return response


@router.get("/api/explainer-videos/{video_id}/cover.png")
async def cover_file(video_id: str, session_factory=Depends(get_session_factory)):
    from ...services.storage.factory import get_artifact_storage

    video = await _public_video(session_factory, video_id)
    if not video.cover_path:
        raise HTTPException(404, "封面不存在")
    storage = get_artifact_storage()
    stream = storage.open_stream(video.cover_path)
    try:
        first = await anext(stream)
    except (FileNotFoundError, StopAsyncIteration):
        await stream.aclose()
        raise HTTPException(404, "封面不存在") from None

    async def chunks():
        try:
            yield first
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()

    return StreamingResponse(
        chunks(), media_type="image/png",
        headers={"Cache-Control": "no-cache", "Cross-Origin-Resource-Policy": "cross-origin"},
    )
