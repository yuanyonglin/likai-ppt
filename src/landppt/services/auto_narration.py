"""Generate narration automatically once a PPT finishes generating.

After a deck is fully generated, speech scripts and per-slide audio are produced in
the background with the user's cloud TTS settings, so the presentation can narrate
without a separate manual step. Nothing is scheduled when cloud TTS is not
configured, or while an unattended run owns the project (it has its own stages).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

AUTO_NARRATION_PROVIDER = "cloud_tts"
NARRATION_TASK_TYPE = "narration_generation"


async def resolve_auto_narration_provider(user_id: Optional[int]) -> Optional[str]:
    """Return the provider to narrate with, or None when cloud TTS is not configured."""
    if user_id is None:
        return None
    from .db_config_service import get_db_config_service

    config_service = get_db_config_service()
    api_key = await config_service.get_config_value("cloud_tts_api_key", user_id=user_id)
    base_url = await config_service.get_config_value("cloud_tts_base_url", user_id=user_id)
    if str(api_key or "").strip() and str(base_url or "").strip():
        return AUTO_NARRATION_PROVIDER
    return None


async def schedule_auto_narration(
    project_id: str, user_id: Optional[int], topic: str = "", language: str = "zh"
) -> Optional[str]:
    """Start background narration generation for a finished deck.

    Returns the background task id, or None when nothing was scheduled. Failures to
    schedule are logged and never break the PPT generation that called this.
    """
    try:
        provider = await resolve_auto_narration_provider(user_id)
        if provider is None:
            logger.info("Auto narration skipped for %s: cloud TTS is not configured", project_id)
            return None

        from .background_tasks import TaskStatus, get_task_manager
        from .unattended_service import UNATTENDED_TASK_TYPE

        task_manager = get_task_manager()
        if await task_manager.find_active_task_async(
            task_type=UNATTENDED_TASK_TYPE,
            metadata_filter={"project_id": project_id, "user_id": user_id},
        ):
            logger.info("Auto narration skipped for %s: unattended run owns narration", project_id)
            return None

        metadata = {"project_id": project_id, "language": language, "provider": provider, "user_id": user_id}
        existing = await task_manager.find_active_task_async(
            task_type=NARRATION_TASK_TYPE, metadata_filter=metadata
        )
        if existing:
            return existing.task_id

        task_id = task_manager.create_task(
            NARRATION_TASK_TYPE, {**metadata, "project_topic": topic, "auto": True}
        )

        async def narration_task():
            from .narration_service import NarrationService

            async def update_progress(done_count: int, total_count: int) -> None:
                if total_count > 0:
                    progress = min(99.0, max(1.0, round(done_count * 100.0 / total_count, 2)))
                    await task_manager.update_task_status_async(task_id, TaskStatus.RUNNING, progress=progress)

            items = await NarrationService(user_id=user_id).generate_project_slide_audios(
                project_id=project_id,
                provider=provider,
                language=language,
                uploads_dir="uploads",
                progress_callback=update_progress,
            )
            logger.info("Auto narration finished for %s: %d slide(s)", project_id, len(items))
            return {"success": True, "language": language, "count": len(items), "auto": True}

        task_manager.running_tasks[task_id] = asyncio.create_task(
            task_manager.execute_task(task_id, narration_task)
        )
        logger.info("Auto narration scheduled for %s (task %s, provider %s)", project_id, task_id, provider)
        return task_id
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not schedule auto narration for %s: %s", project_id, exc, exc_info=True)
        return None
