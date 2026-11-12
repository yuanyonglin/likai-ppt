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
