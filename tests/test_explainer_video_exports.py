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
    monkeypatch.setattr(assets, "_local_file", lambda video_id, key: Path(key))
    return add


def test_parse_explainer_video_id():
    assert parse_explainer_video_id(video_page_html("v-1")) == "v-1"
    assert parse_explainer_video_id("<div data-explainer-video-id='v-2'></div>") == "v-2"
    assert parse_explainer_video_id('<div class="explainer-video-page"><video src="x.mp4"></video></div>') is None
    assert parse_explainer_video_id("") is None


def test_asset_paths_resolve_through_artifact_storage_and_missing_files_fail(monkeypatch, tmp_path):
    from landppt.services.storage import factory
    from landppt.services.storage.local_storage import LocalArtifactStorage

    monkeypatch.setattr(factory, "get_artifact_storage", lambda: LocalArtifactStorage(root=str(tmp_path)))
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads/v.mp4").write_bytes(b"v")
    (tmp_path / "uploads/c.png").write_bytes(b"c")
    rows = {"ok": ("uploads/v.mp4", "uploads/c.png"), "gone": ("uploads/v.mp4", "uploads/missing.png")}
    monkeypatch.setattr(assets, "_query_video_files", rows.get)

    asset = assets.load_explainer_video_asset("ok")
    assert asset.video_path == (tmp_path / "uploads/v.mp4").resolve()
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


# --- narration video export ------------------------------------------------------------


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def _probe(path, entries, stream=None):
    args = ["ffprobe", "-v", "error"]
    if stream:
        args += ["-select_streams", stream]
    out = subprocess.run(
        [*args, "-show_entries", entries, "-of", "json", str(path)], check=True, capture_output=True, text=True
    ).stdout
    return json.loads(out)


def _duration(path):
    return float(_probe(path, "format=duration")["format"]["duration"])


def _mean_volume(path, start, duration):
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", f"{start}", "-t", f"{duration}", "-i", str(path), "-af", "volumedetect",
         "-vn", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    line = next(l for l in err.splitlines() if "mean_volume" in l)
    return float(line.split("mean_volume:")[1].split("dB")[0])


def _frame_color(path, at):
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", str(path), "-frames:v", "1", "-vf", "scale=1:1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return tuple(out[:3])


@pytest.fixture
def narration_export(tmp_path, monkeypatch, video_files):
    """Project: [narrated green slide 1.5s, explainer video 2.0s (blue + 880Hz tone), narrated green 1.0s]."""
    from landppt.services import (
        narration_audio_repository,
        narration_service,
        speech_script_repository,
        video_export_service,
    )

    for index, seconds in ((0, 1.5), (2, 1.0)):
        _ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-ar", "24000", "-ac", "1",
                str(tmp_path / f"n{index}.mp3"))
    video = tmp_path / "explainer.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=2", "-f", "lavfi", "-i",
            "sine=frequency=880:duration=2", "-ar", "44100", "-ac", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(video))
    video_files("v1", video_path=video)

    audios = [
        narration_service.NarrationAudioResult(
            slide_index=i, language="zh", voice="", rate="+0%", audio_path=str(tmp_path / f"n{i}.mp3"),
            duration_ms=ms, cached=True,
        )
        for i, ms in ((0, 1500), (2, 1000))
    ]

    class FakeNarration:
        def __init__(self, user_id=None):
            pass

        async def generate_project_slide_audios(self, **kwargs):
            return audios

    class FakeRepo:
        async def get_current_speech_scripts_by_project(self, *a, **k):
            # A stale script on the video page index must not produce subtitles.
            return [SimpleNamespace(slide_index=i, script_content=f"讲稿{i}") for i in (0, 1, 2)]

        async def list_by_project(self, **kwargs):
            return []

        def close(self):
            pass

    recorded, shot = [], []

    async def record_html_video(html_path, out_path, *, width, height, duration_ms, **kwargs):
        recorded.append(Path(html_path).name)
        _ffmpeg("-f", "lavfi", "-i", f"color=c=0x00FF00:s={width}x{height}:r=25:d={duration_ms / 1000}",
                "-c:v", "libvpx", "-b:v", "200k", out_path)
        return True

    async def screenshot_html(html_path, png_path, *, width, height, **kwargs):
        shot.append(Path(html_path).name)
        Image.new("RGB", (width, height), (0, 255, 0)).save(png_path)
        return True

    render = SimpleNamespace(is_available=lambda: True, record_html_video=record_html_video,
                             screenshot_html=screenshot_html)
    monkeypatch.setattr(narration_service, "NarrationService", FakeNarration)
    monkeypatch.setattr(speech_script_repository, "SpeechScriptRepository", FakeRepo)
    monkeypatch.setattr(narration_audio_repository, "NarrationAudioRepository", FakeRepo)
    monkeypatch.setattr(video_export_service.NarrationVideoExportService, "_get_html_render_service",
                        staticmethod(lambda: render))
    monkeypatch.setenv("LANDPPT_NARRATION_VIDEO_PAD_RECORD_MS", "600")
    project = SimpleNamespace(
        project_id="p-video", topic="demo", user_id=1,
        slides_data=[{"html_content": "<p>a</p>"}, {"html_content": video_page_html("v1")}, {"html_content": "<p>c</p>"}],
    )
    return video_export_service.NarrationVideoExportService(), project, recorded, shot


@needs_ffmpeg
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "static"])
async def test_narration_video_splices_explainer_video_with_original_sound(narration_export, tmp_path, mode):
    service, project, recorded, shot = narration_export
    width, height = 320, 180
    export = service._export_project_video_live if mode == "live" else service._export_project_video_static
    result = await export(
        project=project, language="zh", fps=30, width=width, height=height, embed_subtitles=False,
        subtitle_style=None, uploads_dir=str(tmp_path / "uploads"),
    )
    assert result["success"], result
    rendered = recorded if mode == "live" else shot
    assert rendered == ["slide_0.html", "slide_2.html"]  # video page is never rendered

    out = Path(result["video_path"])
    assert _duration(out) == pytest.approx(4.5, abs=0.15)
    stream = _probe(out, "stream=width,height", "v:0")["streams"][0]
    assert (stream["width"], stream["height"]) == (width, height)
    # Video page window [1.5s, 3.5s] shows the explainer video (blue) with its own sound.
    assert _frame_color(out, 0.7)[1] > 150  # green slide
    r, g, b = _frame_color(out, 2.5)
    assert b > 150 and g < 80
    assert _frame_color(out, 4.0)[1] > 150
    for start in (0.2, 1.7, 3.7):
        assert _mean_volume(out, start, 0.6) > -40

    srt = Path(result["subtitle_path"]).read_text(encoding="utf-8")
    assert "讲稿1" not in srt and "讲稿0" in srt and "讲稿2" in srt
    # The third slide's subtitles start after the video page.
    assert "00:00:03,500" in srt


@pytest.mark.asyncio
async def test_narration_video_fails_when_explainer_video_missing(narration_export, tmp_path, monkeypatch):
    service, project, _, _ = narration_export
    monkeypatch.setattr(assets, "_query_video_files", lambda video_id: None)
    with pytest.raises(FileNotFoundError, match="v1"):
        await service._export_project_video_static(
            project=project, language="zh", fps=30, width=320, height=180, embed_subtitles=False,
            subtitle_style=None, uploads_dir=str(tmp_path / "uploads"),
        )


def test_remote_storage_files_are_downloaded_into_cache(monkeypatch, tmp_path):
    from landppt.services.storage import factory

    class RemoteStorage:
        async def open_stream(self, key):
            yield f"bytes-of-{key}".encode()

    monkeypatch.setattr(factory, "get_artifact_storage", lambda: RemoteStorage())
    monkeypatch.setattr(assets, "PROJECT_ROOT", tmp_path)
    path = assets._local_file("vid", "users/1/v.mp4")
    assert path == tmp_path / "temp/explainer_video_cache/vid/v.mp4"
    assert path.read_bytes() == b"bytes-of-users/1/v.mp4"
