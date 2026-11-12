"""Explainer video pages in exports, speech scripts and narration."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image

from landppt.services.explainer_video.page import parse_explainer_video_id
from landppt.services.export_infra import explainer_video_assets as assets

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")


def video_page_html(video_id: str) -> str:
    return (
        f'<div class="explainer-video-page" data-explainer-video-id="{video_id}" '
        'style="width:1280px;height:720px;position:relative;background:#000;overflow:hidden">'
        f'<video src="/api/explainer-videos/{video_id}/video.mp4" poster="/api/explainer-videos/{video_id}/cover.png" '
        'controls playsinline preload="metadata" style="width:100%;height:100%;object-fit:contain"></video></div>'
    )


@pytest.fixture
def video_files(tmp_path, monkeypatch):
    """Register explainer videos in place of the explainer_videos table."""
    rows = {}

    def add(video_id, video_path=None, color=(220, 30, 30)):
        cover = tmp_path / f"{video_id}_cover.png"
        Image.new("RGB", (1920, 1080), color).save(cover)
        video = Path(video_path) if video_path else tmp_path / f"{video_id}.mp4"
        if not video.exists():
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        rows[video_id] = (str(video), str(cover))
        return assets.ExplainerVideoAsset(video_id, video, cover)

    monkeypatch.setattr(assets, "_query_video_files", rows.get)
    return add


def test_parse_explainer_video_id():
    assert parse_explainer_video_id(video_page_html("v-1")) == "v-1"
    assert parse_explainer_video_id("<div data-explainer-video-id='v-2'></div>") == "v-2"
    assert parse_explainer_video_id('<div class="explainer-video-page"><video src="x.mp4"></video></div>') is None
    assert parse_explainer_video_id("") is None


def test_asset_paths_resolve_relative_to_project_root_and_missing_files_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "PROJECT_ROOT", tmp_path)
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads/v.mp4").write_bytes(b"v")
    (tmp_path / "uploads/c.png").write_bytes(b"c")
    rows = {"ok": ("uploads/v.mp4", "uploads/c.png"), "gone": ("uploads/v.mp4", "uploads/missing.png")}
    monkeypatch.setattr(assets, "_query_video_files", rows.get)

    asset = assets.load_explainer_video_asset("ok")
    assert asset.video_path == tmp_path / "uploads/v.mp4"
    with pytest.raises(FileNotFoundError, match="missing.png"):
        assets.load_explainer_video_asset("gone")
    with pytest.raises(FileNotFoundError, match="unknown"):
        assets.load_explainer_video_asset("unknown")

    slides = [{"html_content": "<p>a</p>"}, {"html_content": video_page_html("ok")}, None]
    assert assets.explainer_video_pages(slides) == {1: asset}
    assert assets.explainer_video_page_indices(slides) == {1}


def test_swap_video_page_for_cover(tmp_path, video_files):
    asset = video_files("v1")
    plain = tmp_path / "slide_1.html"
    plain.write_text("<html><body><p>normal</p></body></html>", encoding="utf-8")
    assert assets.swap_video_page_for_cover(str(plain)) == str(plain)

    page = tmp_path / "slide_2.html"
    page.write_text(f"<html><body>{video_page_html('v1')}</body></html>", encoding="utf-8")
    swapped = Path(assets.swap_video_page_for_cover(str(page)))
    assert swapped.parent == tmp_path and swapped != page
    content = swapped.read_text(encoding="utf-8")
    assert asset.cover_path.resolve().as_uri() in content
    assert "<video" not in content


def _playwright_converter():
    from landppt.services.pyppeteer_pdf_converter import PlaywrightPDFConverter

    converter = PlaywrightPDFConverter()
    if not converter.is_available():
        pytest.skip("Playwright not available")
    return converter


@pytest.mark.asyncio
async def test_pdf_and_screenshot_render_video_page_as_cover(tmp_path, video_files):
    """Real Chromium render: the video page becomes its cover, other pages are untouched."""
    fitz = pytest.importorskip("fitz")
    converter = _playwright_converter()
    video_files("v1", color=(220, 30, 30))
    normal = tmp_path / "slide_1.html"
    normal.write_text(
        "<html><body style='margin:0'><div style='width:1280px;height:720px;background:rgb(30,200,30)'></div></body></html>",
        encoding="utf-8",
    )
    page = tmp_path / "slide_2.html"
    page.write_text(f"<html><body style='margin:0'>{video_page_html('v1')}</body></html>", encoding="utf-8")

    try:
        png = tmp_path / "shot.png"
        assert await converter.screenshot_html(str(page), str(png), width=1280, height=720, optimize_for_static=True)
        shot = Image.open(png).convert("RGB")
        assert shot.getpixel((shot.width // 2, shot.height // 2))[:3] == pytest.approx((220, 30, 30), abs=6)

        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        merged = tmp_path / "merged.pdf"
        await converter.convert_multiple_html_to_pdf([str(normal), str(page)], str(pdf_dir), str(merged))
    finally:
        await converter.close()

    doc = fitz.open(str(merged))
    assert doc.page_count == 2
    assert doc[0].rect == doc[1].rect
    colors = []
    for pdf_page in doc:
        pix = pdf_page.get_pixmap(dpi=36)
        colors.append(tuple(pix.pixel(pix.width // 2, pix.height // 2)))
    assert colors[0] == pytest.approx((30, 200, 30), abs=6)
    assert colors[1] == pytest.approx((220, 30, 30), abs=6)


@pytest.mark.asyncio
async def test_speech_scripts_skip_video_pages_keep_indices(monkeypatch):
    from landppt.services import speech_script_service as module

    service = object.__new__(module.SpeechScriptService)
    service.user_id = None
    service.ai_provider = object()

    async def no_contexts(*_args):
        return {}

    generated = []

    async def fake_script(slide, slide_index, *args):
        generated.append(slide_index)
        return f"script-{slide_index}"

    service._load_existing_script_contexts = no_contexts
    service._generate_script_for_slide = fake_script
    project = SimpleNamespace(
        project_id="p-video",
        slides_data=[
            {"title": "A", "html_content": "<p>a</p>"},
            {"title": "Video", "html_content": video_page_html("v1")},
            {"title": "C", "html_content": "<p>c</p>"},
        ],
    )

    result = await service.generate_multi_slide_scripts_with_retry(
        project, [0, 1, 2], module.SpeechScriptCustomization(), max_retries=1, task_id="t-video"
    )
    assert result.success and result.error_message is None
    assert [s.slide_index for s in result.scripts] == [0, 2]
    assert generated == [0, 2]
    progress = module.progress_tracker.get_progress("t-video")
    assert (progress.completed_slides, progress.skipped_slides) == (2, 1)

    only_video = await service.generate_multi_slide_scripts_with_retry(
        project, [1], module.SpeechScriptCustomization(), max_retries=1, task_id="t-video-only"
    )
    assert not only_video.success
    assert only_video.error_message == "所选页面均为讲解视频页，无需生成演讲稿"


@pytest.mark.asyncio
async def test_narration_scripts_exclude_video_pages(monkeypatch):
    from landppt.services import db_project_manager, narration_service, speech_script_repository, speech_script_service

    scripts = {i: SimpleNamespace(slide_index=i, script_content=f"s{i}") for i in (0, 1, 2)}

    class Repo:
        async def get_current_speech_scripts_by_project(self, project_id, language="zh"):
            return list(scripts.values())

        def close(self):
            pass

    project = SimpleNamespace(
        slides_data=[{"html_content": "a"}, {"html_content": video_page_html("v1")}, {"html_content": "c"}]
    )

    class Manager:
        async def get_project(self, project_id, user_id=None):
            return project

    monkeypatch.setattr(speech_script_repository, "SpeechScriptRepository", Repo)
    monkeypatch.setattr(db_project_manager, "DatabaseProjectManager", Manager)
    monkeypatch.setattr(speech_script_service, "SpeechScriptService", None)  # must not regenerate

    service = narration_service.NarrationService(user_id=1)
    result = await service._ensure_speech_scripts(project_id="p", language="zh")
    assert [s.slide_index for s in result] == [0, 2]
    assert await service._ensure_speech_scripts(project_id="p", language="zh", slide_indices=[1]) == []
