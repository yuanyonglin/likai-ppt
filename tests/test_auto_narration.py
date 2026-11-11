"""Narration is generated automatically after a PPT finishes, with cloud TTS."""

import asyncio
from types import SimpleNamespace

import pytest

from landppt.services import auto_narration


class FakeConfig:
    def __init__(self, values):
        self.values = values

    async def get_config_value(self, key, user_id=None):
        return self.values.get(key)


class FakeTaskManager:
    def __init__(self, active=None):
        self.active = active or {}
        self.created = []
        self.running_tasks = {}

    async def find_active_task_async(self, task_type, metadata_filter=None):
        return self.active.get(task_type)

    def create_task(self, task_type, metadata):
        self.created.append((task_type, metadata))
        return "task-1"

    async def execute_task(self, task_id, func):
        return await func()

    async def update_task_status_async(self, *args, **kwargs):
        return None


CONFIGURED = {"cloud_tts_api_key": "k", "cloud_tts_base_url": "https://tts.example/minimax"}


def patch_env(monkeypatch, config, manager):
    import landppt.services.background_tasks as background_tasks
    import landppt.services.db_config_service as db_config_service

    monkeypatch.setattr(db_config_service, "get_db_config_service", lambda: FakeConfig(config))
    monkeypatch.setattr(background_tasks, "get_task_manager", lambda: manager)


@pytest.mark.asyncio
async def test_skipped_when_cloud_tts_not_configured(monkeypatch):
    manager = FakeTaskManager()
    patch_env(monkeypatch, {"cloud_tts_api_key": "k"}, manager)
    assert await auto_narration.schedule_auto_narration("p1", 7) is None
    assert manager.created == []


@pytest.mark.asyncio
async def test_skipped_while_unattended_run_owns_project(monkeypatch):
    manager = FakeTaskManager(active={"unattended_pipeline": SimpleNamespace(task_id="u1")})
    patch_env(monkeypatch, CONFIGURED, manager)
    assert await auto_narration.schedule_auto_narration("p1", 7) is None
    assert manager.created == []


@pytest.mark.asyncio
async def test_generates_all_slides_with_cloud_tts(monkeypatch):
    import landppt.services.narration_service as narration_service

    calls = []

    class FakeNarration:
        def __init__(self, user_id):
            self.user_id = user_id

        async def generate_project_slide_audios(self, **kwargs):
            calls.append((self.user_id, kwargs))
            return [SimpleNamespace(slide_index=0), SimpleNamespace(slide_index=1)]

    monkeypatch.setattr(narration_service, "NarrationService", FakeNarration)
    manager = FakeTaskManager()
    patch_env(monkeypatch, CONFIGURED, manager)

    assert await auto_narration.schedule_auto_narration("p1", 7, "topic") == "task-1"
    await asyncio.gather(*manager.running_tasks.values())

    assert manager.created[0][0] == "narration_generation"
    assert manager.created[0][1]["provider"] == "cloud_tts"
    (user_id, kwargs), = calls
    assert user_id == 7
    assert kwargs["project_id"] == "p1"
    assert kwargs["provider"] == "cloud_tts"
    assert kwargs.get("slide_indices") is None


@pytest.mark.asyncio
async def test_reuses_running_narration_task(monkeypatch):
    manager = FakeTaskManager(active={"narration_generation": SimpleNamespace(task_id="n1")})
    patch_env(monkeypatch, CONFIGURED, manager)
    assert await auto_narration.schedule_auto_narration("p1", 7) == "n1"
    assert manager.created == []
