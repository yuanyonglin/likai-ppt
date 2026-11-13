"""Explainer video generation: source → script → TTS → scenes → recording → MP4.

The ``explainer_videos`` row is the authoritative state (task state in memory/Valkey
expires); every stage is written to it, and failures are recorded before re-raising.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...core.config import app_config
from ...database.models import ExplainerVideo
from . import llm, render, sources, tts
from .animation import build_animation_html, build_cues, resolve_theme
from .page import cover_url, video_url

logger = logging.getLogger(__name__)

TASK_TYPE = "explainer_video_generation"
STAGE_PROGRESS = {"parse": 5, "script": 15, "tts": 35, "animation": 55, "render": 70}
STAGE_MESSAGES = {
    "parse": "正在读取内容…",
    "script": "AI 正在撰写讲解稿…",
    "tts": "正在生成配音…",
    "animation": "正在制作动画画面…",
    "render": "正在录制并合成视频…",
}
LEAD_SECONDS, GAP_SECONDS, TAIL_SECONDS = 0.6, 0.4, 0.8
VIDEO_ARTIFACT_TYPE = "narration_video_export"  # library category "讲解视频"
COVER_ARTIFACT_TYPE = "explainer_video_cover"
DEFAULT_OPTIONS = {"duration": "1m", "tts_provider": "auto", "voice": "", "style": "follow_ppt", "subtitles": True}


def _session_factory():
    from ...database.database import AsyncSessionLocal

    return AsyncSessionLocal


def serialize(video: ExplainerVideo, placements: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    done = video.status == "completed"
    return {
        "id": video.id,
        "status": video.status,
        "stage": video.stage,
        "progress": 100 if done else STAGE_PROGRESS.get(video.stage or "", 0),
        "error": video.error,
        "title": video.title,
        "project_id": video.project_id,
        "source_type": video.source_type,
        "source_ref": video.source_ref,
        "options": video.options_json,
        "task_id": video.task_id,
        "script": video.script_json,
        "video_url": video_url(video.id) if done else None,
        "cover_url": cover_url(video.id) if done else None,
        "duration_ms": video.duration_ms,
        "artifact_id": video.artifact_id,
        "placements": placements or [],
        "created_at": video.created_at,
        "updated_at": video.updated_at,
        "updated_at": video.updated_at,
    }


def segment_timeline(durations_ms: List[int]) -> tuple[List[float], List[float], float]:
    starts, durations, cursor = [], [], LEAD_SECONDS
    for duration_ms in durations_ms:
        seconds = duration_ms / 1000
        starts.append(round(cursor, 2))
        durations.append(round(seconds, 2))
        cursor += seconds + GAP_SECONDS
    return starts, durations, round(cursor - GAP_SECONDS + TAIL_SECONDS, 2)


async def load_source(session, video: ExplainerVideo) -> sources.SourceContent:
    ref = video.source_ref or {}
    if video.source_type == "slide":
        return await sources.load_slide_source(
            session, user_id=video.user_id, project_id=ref.get("project_id"), slide_id=ref.get("slide_id")
        )
    if video.source_type == "upload":
        return sources.load_upload_section(video.user_id, ref.get("file_id"), int(ref.get("section_index", 0)))
    return sources.text_source(ref.get("title"), ref.get("text"))


async def store_outputs(video: ExplainerVideo, mp4: Path, cover: Path) -> Dict[str, Any]:
    """Save MP4 (shown in the library) and cover as artifacts; drop the previous ones."""
    from ..storage import get_artifact_service

    artifacts = get_artifact_service()
    previous = [video.artifact_id, (video.options_json or {}).get("cover_artifact_id")]
    safe_title = "".join(ch for ch in (video.title or "讲解视频") if ch not in '\\/:*?"<>|').strip() or "讲解视频"
    meta = {"explainer_video_id": video.id}
    video_artifact = await artifacts.save_file(
        local_path=str(mp4), user_id=video.user_id, artifact_type=VIDEO_ARTIFACT_TYPE,
        project_id=video.project_id, task_id=video.id, filename=f"{safe_title}_讲解视频.mp4",
        content_type="video/mp4", metadata_json=meta,
    )
    cover_artifact = await artifacts.save_file(
        local_path=str(cover), user_id=video.user_id, artifact_type=COVER_ARTIFACT_TYPE,
        project_id=video.project_id, task_id=video.id, filename=f"{video.id}_cover.png",
        content_type="image/png", metadata_json=meta,
    )
    for artifact_id in previous:
        old = await artifacts.get_artifact(artifact_id) if artifact_id else None
        if old is not None and old.id not in {video_artifact.id, cover_artifact.id}:
            try:
                await artifacts.delete_artifact(old)
            except Exception:  # noqa: BLE001 - stale file cleanup must not fail a finished video
                logger.warning("Failed to delete previous explainer artifact %s", artifact_id, exc_info=True)
    return {
        "artifact_id": video_artifact.id,
        "video_path": video_artifact.storage_key,
        "cover_path": cover_artifact.storage_key,
        "cover_artifact_id": cover_artifact.id,
    }


async def _auto_place(session_factory, video_id: str) -> Optional[str]:
    """Run the requested placement; return an error message instead of failing the video."""
    from .placement import PlacementError, place_video

    async with session_factory() as session:
        video = await session.get(ExplainerVideo, video_id)
        placement = (video.options_json or {}).get("placement") or {}
        mode = placement.get("mode") or "none"
        if mode == "none":
            return None
        ref = video.source_ref or {}
        project_id = placement.get("project_id") or video.project_id
        anchor = placement.get("slide_id") or (ref.get("slide_id") if video.source_type == "slide" else None)
        if not project_id or not anchor:
            return "未指定放置位置，请手动加入 PPT"
        try:
            await place_video(
                session, video, user_id=video.user_id, project_id=project_id,
                after_slide_id=anchor if mode == "insert" else None,
                replace_slide_id=anchor if mode == "replace" else None,
            )
        except PlacementError as exc:
            return f"自动加入 PPT 失败：{exc.message}"
    return None


def result_url(video_id: str) -> str:
    """Home page studio opened on this video's progress or result."""
    return f"/dashboard?kind=video&explainer={video_id}"


async def _notify(session_factory, video_id: str, *, ok: bool) -> None:
    """In-app notification for a finished run; best-effort like every other notification."""
    from ...database.repositories import NotificationRepository

    try:
        async with session_factory() as session:
            video = await session.get(ExplainerVideo, video_id)
            title = video.title or "讲解视频"
            await NotificationRepository(session).create({
                "id": str(uuid.uuid4()),
                "user_id": video.user_id,
                "project_id": video.project_id,
                "notification_type": "explainer_video",
                "level": "success" if ok else "error",
                "title": f"讲解视频已生成：{title}" if ok else f"讲解视频生成失败：{title}",
                "body": (video.error or "点击查看视频，可播放、下载或加入 PPT。") if ok else (video.error or "未知错误"),
                "link_url": result_url(video_id),
                "payload": {"video_id": video_id},
                "is_read": False,
                "created_at": time.time(),
            })
    except Exception:  # noqa: BLE001 - a notification must not fail the video
        logger.warning("Failed to notify explainer video %s", video_id, exc_info=True)


async def run_generation(
    video_id: str,
    task_id: Optional[str] = None,
    *,
    reuse_script: bool = False,
    session_factory: Optional[Callable] = None,
) -> Dict[str, Any]:
    factory = session_factory or _session_factory()
    from ..background_tasks import TaskStatus, get_task_manager

    manager = get_task_manager()

    async def update(**fields) -> ExplainerVideo:
        async with factory() as session:
            video = await session.get(ExplainerVideo, video_id)
            for key, value in fields.items():
                setattr(video, key, value)
            video.updated_at = time.time()
            await session.commit()
        stage = fields.get("stage")
        if task_id and stage:
            task = manager.tasks.get(task_id)
            if task is not None:
                task.metadata["stage"] = stage
                task.metadata["progress_message"] = STAGE_MESSAGES[stage]
            await manager.update_task_status_async(task_id, TaskStatus.RUNNING, progress=STAGE_PROGRESS[stage])
        return video

    work_dir = Path(tempfile.mkdtemp(prefix="landppt_explainer_"))
    try:
        video = await update(status="running", stage="parse", error=None)
        options = {**DEFAULT_OPTIONS, **(video.options_json or {})}
        async with factory() as session:
            source = await load_source(session, video)

        if reuse_script and video.script_json and video.script_json.get("segments"):
            script = llm.normalize_script(video.script_json, source.title)
        else:
            await update(stage="script")
            script = await llm.write_script(
                video.user_id, source, duration=options["duration"], extra=options.get("extra") or ""
            )
        title = script["title"]
        await update(stage="tts", title=title, script_json=script)

        provider = await tts.resolve_provider(video.user_id, options.get("tts_provider"))
        audio_paths, durations_ms = [], []
        for index, segment in enumerate(script["segments"]):
            path, duration_ms, _ = await tts.synthesize(
                user_id=video.user_id, provider=provider, voice=options.get("voice"),
                text=segment["text"], out_path=work_dir / f"seg{index}.mp3",
            )
            audio_paths.append(path)
            durations_ms.append(duration_ms)
        starts, durations, total = segment_timeline(durations_ms)
        for segment, start, duration in zip(script["segments"], starts, durations):
            segment["start"], segment["duration"] = start, duration

        await update(stage="animation", script_json=script)
        scenes = await llm.plan_scenes(video.user_id, script, source)
        texts = [segment["text"] for segment in script["segments"]]
        html = build_animation_html(
            title=title, scenes=scenes, starts=starts, durations=durations,
            cues=build_cues(texts, starts, durations), total=total,
            theme=resolve_theme(options.get("style") or "follow_ppt", source.colors),
            subtitles=bool(options.get("subtitles", True)),
        )
        html_path = work_dir / "animation.html"
        html_path.write_text(html, encoding="utf-8")
        script["scenes"] = scenes

        await update(stage="render", script_json=script)
        audio = await render.mix_narration(audio_paths, starts, total, work_dir / "narration.m4a")
        raw, offset = await render.record_animation(html_path, total, work_dir)
        mp4 = await render.compose_mp4(raw, offset, audio, total, work_dir / "explainer.mp4")
        cover_at = starts[0] + min(durations[0] * 0.8, 3.0)
        cover = await render.extract_cover(mp4, cover_at, work_dir / "cover.png")

        async with factory() as session:
            video = await session.get(ExplainerVideo, video_id)
        stored = await store_outputs(video, mp4, cover)
        options_json = {**(video.options_json or {}), "cover_artifact_id": stored.pop("cover_artifact_id")}
        await update(
            status="completed", stage=None, error=None, duration_ms=int(total * 1000),
            options_json=options_json, **stored,
        )
        placement_error = await _auto_place(factory, video_id)
        if placement_error:
            await update(error=placement_error)
        await _notify(factory, video_id, ok=True)
        return {"success": True, "video_id": video_id, "duration_ms": int(total * 1000)}
    except asyncio.CancelledError:
        await update(status="failed", error="任务被取消")
        raise
    except Exception as exc:
        logger.exception("Explainer video %s failed", video_id)
        await update(status="failed", error=str(exc) or exc.__class__.__name__)
        await _notify(factory, video_id, ok=False)
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def submit_generation(video: ExplainerVideo, *, reuse_script: bool = False) -> str:
    """Start the background task (worker queue or inline) and return its id."""
    from ..background_tasks import get_task_manager

    manager = get_task_manager()
    metadata = {
        "video_id": video.id,
        "user_id": video.user_id,
        "project_id": video.project_id,
        "reuse_script": reuse_script,
        "stage": "pending",
    }
    task_id = await manager.submit_queued_task(TASK_TYPE, metadata=metadata)
    if str(app_config.task_execution_mode or "inline").lower() != "queue":
        from ...tasks.handlers.explainer_video_tasks import run_explainer_video

        async_task = asyncio.create_task(manager.execute_task(task_id, run_explainer_video, manager.tasks[task_id]))
        manager.running_tasks[task_id] = async_task
    return task_id


def new_video_id() -> str:
    return str(uuid.uuid4())
