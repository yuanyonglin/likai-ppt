"""Explainer video: page marker, place/restore, task status flow."""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from landppt.database.models import (
    Base,
    ExplainerVideo,
    NarrationAudio,
    Project,
    SlideData,
    SpeechScript,
    User,
)
from landppt.services.explainer_video.page import (
    build_video_page_html,
    parse_explainer_video_id,
)
from landppt.services.explainer_video.placement import (
    PlacementError,
    list_placements,
    place_video,
    restore_slide,
)

PROJECT = "proj-1"


def test_parse_marker_from_generated_and_edited_html():
    assert parse_explainer_video_id(build_video_page_html("v-123")) == "v-123"
    # Editors may re-serialize with single quotes and extra attributes.
    edited = "<div style='x' data-explainer-video-id='abc' class=\"explainer-video-page\"></div>"
    assert parse_explainer_video_id(edited) == "abc"
    assert parse_explainer_video_id("<div>普通页面</div>") is None
    assert parse_explainer_video_id("") is None
    assert parse_explainer_video_id(None) is None


def test_video_page_html_matches_contract():
    html = build_video_page_html("v1")
    assert 'data-explainer-video-id="v1"' in html
    assert 'src="/api/explainer-videos/v1/video.mp4"' in html
    assert 'poster="/api/explainer-videos/v1/cover.png"' in html


@pytest_asyncio.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ev.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add_all([User(id=i, username=f"u{i}", password_hash="x") for i in (1, 2)])
        session.add(
            Project(
                project_id=PROJECT,
                user_id=1,
                title="T",
                topic="T",
                scenario="general",
                outline={"title": "T", "slides": [{"title": f"P{i}", "page_number": i + 1} for i in range(3)]},
                slides_data=[
                    {"slide_id": f"s{i}", "title": f"P{i}", "html_content": f"<p>{i}</p>", "page_number": i + 1}
                    for i in range(3)
                ],
            )
        )
        for i in range(3):
            session.add(
                SlideData(
                    project_id=PROJECT,
                    slide_index=i,
                    slide_id=f"s{i}",
                    title=f"P{i}",
                    content_type="content",
                    html_content=f"<p>{i}</p>",
                    slide_metadata={"slide_type": "content"},
                )
            )
            session.add(
                SpeechScript(
                    project_id=PROJECT,
                    slide_index=i,
                    language="zh",
                    slide_title=f"P{i}",
                    script_content=f"script-{i}",
                    generation_type="single",
                    tone="x",
                    target_audience="x",
                    language_complexity="x",
                    speaking_pace="x",
                )
            )
            session.add(
                NarrationAudio(
                    project_id=PROJECT,
                    slide_index=i,
                    voice="v",
                    content_hash="a" * 64,
                    file_path=f"audio-{i}.mp3",
                )
            )
        session.add(
            ExplainerVideo(id="vid-1", user_id=1, source_type="text", title="讲解", status="completed")
        )
    yield factory
    await engine.dispose()


async def _state(factory):
    async with factory() as session:
        rows = (await session.scalars(select(SlideData).order_by(SlideData.slide_index))).all()
        scripts = {s.script_content: s.slide_index for s in (await session.scalars(select(SpeechScript))).all()}
        audios = {a.file_path: a.slide_index for a in (await session.scalars(select(NarrationAudio))).all()}
        project = await session.scalar(select(Project))
        video = await session.get(ExplainerVideo, "vid-1")
        return rows, scripts, audios, project, video


@pytest.mark.asyncio
async def test_insert_after_shifts_later_pages_and_their_narration(sessions):
    async with sessions() as session:
        video = await session.get(ExplainerVideo, "vid-1")
        result = await place_video(session, video, user_id=1, project_id=PROJECT, after_slide_id="s0")
    assert result["slide_index"] == 1

    rows, scripts, audios, project, video = await _state(sessions)
    assert [r.slide_id for r in rows][0] == "s0"
    assert [r.slide_id for r in rows][2:] == ["s1", "s2"]
    assert [r.slide_index for r in rows] == [0, 1, 2, 3]
    assert parse_explainer_video_id(rows[1].html_content) == "vid-1"
    assert rows[1].slide_id == result["slide_id"]
    assert rows[1].is_user_edited
    assert scripts == {"script-0": 0, "script-1": 2, "script-2": 3}
    assert audios == {"audio-0.mp3": 0, "audio-1.mp3": 2, "audio-2.mp3": 3}
    assert [s["slide_id"] for s in project.slides_data] == ["s0", result["slide_id"], "s1", "s2"]
    assert [s["page_number"] for s in project.slides_data] == [1, 2, 3, 4]
    assert [s["title"] for s in project.outline["slides"]] == ["P0", "讲解", "P1", "P2"]

    async with sessions() as session:
        video = await session.get(ExplainerVideo, "vid-1")
        assert await list_placements(session, video) == [
            {"project_id": PROJECT, "slide_id": result["slide_id"], "mode": "insert"}
        ]


@pytest.mark.asyncio
async def test_replace_backs_up_and_restore_brings_page_back(sessions):
    async with sessions() as session:
        video = await session.get(ExplainerVideo, "vid-1")
        result = await place_video(session, video, user_id=1, project_id=PROJECT, replace_slide_id="s1")
    assert result == {"slide_id": "s1", "slide_index": 1}

    rows, scripts, audios, project, video = await _state(sessions)
    assert parse_explainer_video_id(rows[1].html_content) == "vid-1"
    assert parse_explainer_video_id(project.slides_data[1]["html_content"]) == "vid-1"
    assert video.replaced_slide_backup["s1"]["html_content"] == "<p>1</p>"
    assert "script-1" not in scripts and "audio-1.mp3" not in audios
    assert scripts["script-2"] == 2

    async with sessions() as session:
        video = await session.get(ExplainerVideo, "vid-1")
        assert (await list_placements(session, video))[0]["mode"] == "replace"
        with pytest.raises(PlacementError):
            await place_video(session, video, user_id=1, project_id=PROJECT, replace_slide_id="s1")
        await restore_slide(session, video, user_id=1, project_id=PROJECT, slide_id="s1")

    rows, _, _, project, video = await _state(sessions)
    assert rows[1].html_content == "<p>1</p>" and rows[1].title == "P1"
    assert rows[1].slide_metadata == {"slide_type": "content"}
    assert project.slides_data[1]["html_content"] == "<p>1</p>"
    assert video.replaced_slide_backup is None


@pytest.mark.asyncio
async def test_place_rejects_foreign_project_and_bad_arguments(sessions):
    async with sessions() as session:
        video = await session.get(ExplainerVideo, "vid-1")
        with pytest.raises(PlacementError) as exc:
            await place_video(session, video, user_id=2, project_id=PROJECT, after_slide_id="s0")
        assert exc.value.status_code == 404
        with pytest.raises(PlacementError) as exc:
            await place_video(
                session, video, user_id=1, project_id=PROJECT, after_slide_id="s0", replace_slide_id="s1"
            )
        assert exc.value.status_code == 400
        with pytest.raises(PlacementError) as exc:
            await restore_slide(session, video, user_id=1, project_id=PROJECT, slide_id="s1")
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_migration_023_creates_table(tmp_path):
    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import AsyncSession

    from landppt.database.migrations import DatabaseMigration

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[User.__table__])
    async with AsyncSession(engine) as session:
        await DatabaseMigration()._migration_023_up(session)
        await DatabaseMigration()._migration_023_up(session)  # idempotent
        await session.commit()
    async with engine.connect() as conn:
        columns = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("explainer_videos")}
        )
    assert {"replaced_slide_backup", "script_json", "stage", "artifact_id"} <= columns
    await engine.dispose()


# ---- generation pipeline status flow ---------------------------------------

from pathlib import Path  # noqa: E402

from landppt.services.explainer_video import service  # noqa: E402
from landppt.services.explainer_video.animation import build_animation_html, build_cues  # noqa: E402
from landppt.services.explainer_video.sources import SourceContent  # noqa: E402


def _stub_pipeline(monkeypatch, seen, fail_stage=None):
    async def fake_source(session, video):
        return SourceContent(title="页面", text="内容", colors=["#C00000"])

    async def write_script(user_id, source, *, duration, extra):
        if fail_stage == "script":
            raise RuntimeError("model down")
        return {"title": "讲解标题", "segments": [{"text": "第一句。第二句。", "bullets": []}, {"text": "第三句。"}]}

    async def provider(user_id, name):
        return "cloud_tts"

    async def synthesize(*, user_id, provider, voice, text, out_path, language="zh"):
        out_path.write_bytes(b"x")
        return out_path, 2000, "voice"

    async def plan_scenes(user_id, script, source):
        return [{"template": "title", "heading": "开场"}, {"template": "steps", "heading": "步骤", "items": ["a", "b"]}]

    async def touch(*args):
        path = args[-1]
        path.write_bytes(b"x")
        return path

    async def record(html_path, total, work_dir):
        seen.append(("html", html_path.read_text(encoding="utf-8")))
        return work_dir / "raw.webm", 0.5

    async def store(video, mp4, cover):
        return {"artifact_id": "art-1", "video_path": "k/v.mp4", "cover_path": "k/c.png", "cover_artifact_id": "art-2"}

    monkeypatch.setattr(service, "load_source", fake_source)
    monkeypatch.setattr(service.llm, "write_script", write_script)
    monkeypatch.setattr(service.llm, "plan_scenes", plan_scenes)
    monkeypatch.setattr(service.tts, "resolve_provider", provider)
    monkeypatch.setattr(service.tts, "synthesize", synthesize)
    monkeypatch.setattr(service.render, "mix_narration", touch)
    monkeypatch.setattr(service.render, "record_animation", record)
    monkeypatch.setattr(service.render, "compose_mp4", touch)
    monkeypatch.setattr(service.render, "extract_cover", touch)
    monkeypatch.setattr(service, "store_outputs", store)


async def _notifications(sessions):
    from sqlalchemy import select
    from landppt.database.models import Notification

    async with sessions() as session:
        return (await session.scalars(select(Notification))).all()


def _watch_status(sessions, seen):
    """Record (status, stage) every time the row is committed."""
    from sqlalchemy import event

    def listener(mapper, connection, target):
        seen.append((target.status, target.stage))

    event.listen(ExplainerVideo, "after_update", listener)
    return lambda: event.remove(ExplainerVideo, "after_update", listener)


@pytest.mark.asyncio
async def test_generation_walks_stages_and_completes(sessions, monkeypatch):
    seen = []
    _stub_pipeline(monkeypatch, seen)
    async with sessions() as session:
        session.add(ExplainerVideo(
            id="vid-2", user_id=1, source_type="slide", status="pending",
            source_ref={"project_id": PROJECT, "slide_id": "s1"},
            options_json={"placement": {"mode": "insert", "project_id": PROJECT}},
        ))
        await session.commit()
    stop = _watch_status(sessions, seen)
    try:
        result = await service.run_generation("vid-2", session_factory=sessions)
    finally:
        stop()
    assert result["success"] is True
    states = [item for item in seen if item[0] != "html"]
    assert states[:5] == [
        ("running", "parse"), ("running", "script"), ("running", "tts"), ("running", "animation"), ("running", "render"),
    ]
    assert ("completed", None) in states

    async with sessions() as session:
        video = await session.get(ExplainerVideo, "vid-2")
        assert video.status == "completed" and video.error is None
        assert video.artifact_id == "art-1" and video.cover_path == "k/c.png"
        # lead 0.6 + 2.0 + gap 0.4 + 2.0 + tail 0.8
        assert video.duration_ms == 5800
        assert [s["start"] for s in video.script_json["segments"]] == [0.6, 3.0]
        placements = await list_placements(session, video)
    # placement.mode=insert puts it right after the source slide.
    assert placements and placements[0]["mode"] == "insert"
    rows, *_ = await _state(sessions)
    assert parse_explainer_video_id(rows[2].html_content) == "vid-2"

    notes = await _notifications(sessions)
    assert [(n.level, n.link_url) for n in notes] == [("success", "/dashboard?kind=video&explainer=vid-2")]
    assert notes[0].title.startswith("讲解视频已生成")

    html = next(item[1] for item in seen if item[0] == "html")
    assert "window.__start" in html and "第三句" in html and "#C00000" in html
    assert "http://" not in html and "https://" not in html and "<link" not in html


@pytest.mark.asyncio
async def test_generation_failure_records_error_and_stage(sessions, monkeypatch):
    _stub_pipeline(monkeypatch, [], fail_stage="script")
    async with sessions() as session:
        session.add(ExplainerVideo(id="vid-3", user_id=1, source_type="text", status="pending",
                                   source_ref={"title": "t", "text": "x"}))
        await session.commit()
    with pytest.raises(RuntimeError):
        await service.run_generation("vid-3", session_factory=sessions)
    async with sessions() as session:
        video = await session.get(ExplainerVideo, "vid-3")
    assert (video.status, video.stage, video.error) == ("failed", "script", "model down")
    notes = await _notifications(sessions)
    assert [(n.level, n.title, n.body) for n in notes] == [("error", "讲解视频生成失败：讲解视频", "model down")]


def test_cues_follow_segment_timing_and_html_escapes_text():
    cues = build_cues(["你好。世界！"], [1.0], [2.0])
    assert cues == [[1.0, 2.0, "你好。"], [2.0, 3.0, "世界！"]]
    html = build_animation_html(
        title="<b>t</b>", scenes=[{"template": "bullets", "heading": "</script><x>", "items": ["a"]}],
        starts=[0.6], durations=[2.0], cues=[[0.6, 2.6, "</script>"]], total=3.4,
        theme={"bg": "#fff", "primary": "#000"},
    )
    assert "</script><x>" not in html and "&lt;/script&gt;&lt;x&gt;" in html
    assert html.count("</script>") == 1


@pytest.mark.asyncio
async def test_upload_sections_by_heading_and_reload(tmp_path, monkeypatch):
    from landppt.services.explainer_video import sources

    monkeypatch.setattr(sources, "UPLOADS_ROOT", tmp_path)
    md = "# 总览\n介绍\n\n## 第一部分\n内容一 ![img](a.png)\n\n## 第二部分\n内容二\n".encode()
    payload = await sources.store_upload(7, "doc.md", md)
    assert [s["title"] for s in payload["sections"]] == ["总览", "第一部分", "第二部分"]
    assert "img" not in payload["sections"][1]["text"]
    section = sources.load_upload_section(7, payload["file_id"], 2)
    assert section.title == "第二部分" and "内容二" in section.text
    with pytest.raises(sources.SourceError):
        sources.load_upload_section(8, payload["file_id"], 0)  # another user's upload
    with pytest.raises(sources.SourceError):
        await sources.store_upload(7, "a.exe", b"x")


@pytest.mark.asyncio
async def test_ffmpeg_never_reads_terminal(monkeypatch):
    import asyncio

    from landppt.services.explainer_video import render

    calls = {}

    class Done:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        calls["args"], calls["kwargs"] = args, kwargs
        return Done()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await render._ffmpeg("-i", "x")
    assert calls["args"][:2] == ("ffmpeg", "-nostdin")
    assert calls["kwargs"]["stdin"] == asyncio.subprocess.DEVNULL
