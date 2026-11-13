"""Worker handler for explainer video generation."""

from ...services.explainer_video.service import TASK_TYPE, run_generation
from ..registry import task_handler


@task_handler(TASK_TYPE)
async def run_explainer_video(task):
    metadata = task.metadata
    return await run_generation(
        metadata["video_id"], task.task_id, reuse_script=bool(metadata.get("reuse_script"))
    )
