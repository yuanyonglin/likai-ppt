import importlib.util
import json
import sys
from datetime import datetime
from functools import partial
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zipfile import ZipFile

import pytest
from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.xmlchemy import OxmlElement


ROOT = Path(__file__).resolve().parents[1]
HANDLER_PATH = ROOT / "src/landppt/tasks/handlers/export_tasks.py"
NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
}


@pytest.fixture
def exports(monkeypatch):
    # Keep handler tests independent of app startup, credentials and external services.
    dependencies = {
        "services.background_tasks": {"TaskStatus": SimpleNamespace(RUNNING="running"), "get_task_manager": None},
        "services.narration_audio_repository": {"NarrationAudioRepository": None},
        "services.pdf_to_pptx_converter": {"get_pdf_to_pptx_converter": None},
        "services.pyppeteer_pdf_converter": {"get_pdf_converter": None},
        "services.speech_script_repository": {"SpeechScriptRepository": None},
        "services.storage": {"get_artifact_service": None},
        "web.route_modules.export_support": {
            "_generate_pdf_with_pyppeteer": None,
            "_prepare_html_for_file_based_export": lambda html, base: html,
        },
        "web.route_modules.narration_routes": {"_build_narration_audio_export_zip": None},
        "web.route_modules.support": {"ppt_service": None},
        "tasks.registry": {"task_handler": lambda name: lambda func: func},
    }
    for suffix, attributes in dependencies.items():
        name = f"landppt.{suffix}"
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("landppt.tasks.handlers._audio_export_test", HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audios(exports, tmp_path, rows):
    records = {}
    for index, duration in rows.items():
        path = tmp_path / f"slide_{index}.mp3"
        path.write_bytes(b"ID3" + bytes([index]))
        records[index] = SimpleNamespace(file_path=str(path), duration_ms=duration)
    repo = SimpleNamespace(
        get_latest_for_slide=AsyncMock(side_effect=lambda **kw: records.get(kw["slide_index"])),
        close=lambda: None,
    )
    exports.NarrationAudioRepository = lambda: repo
    return records, repo


@pytest.fixture
def inline_routes(exports, monkeypatch, tmp_path):
    from pydantic import BaseModel

    class ImagePPTXExportRequest(BaseModel):
        slides: list[dict]

    dependencies = {
        "auth.middleware": {"get_current_user_required": lambda: None},
        "core.config": {"app_config": SimpleNamespace(task_execution_mode="inline")},
        "database.models": {"User": SimpleNamespace},
        "utils.thread_pool": {"run_blocking_io": None},
    }
    for suffix, attributes in dependencies.items():
        name = f"landppt.{suffix}"
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    support = sys.modules["landppt.web.route_modules.export_support"]
    support.ImagePPTXExportRequest = ImagePPTXExportRequest
    support._generate_html_export_sync = None
    support._is_standard_pptx_export_enabled = AsyncMock(return_value=True)
    support._resolve_export_base_url = lambda request: "http://localhost"
    monkeypatch.setitem(sys.modules, "landppt.tasks.handlers.export_tasks", exports)
    name = "landppt.web.route_modules._inline_narration_export_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "src/landppt/web/route_modules/export_routes.py")
    routes = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, routes)
    spec.loader.exec_module(routes)
    routes.tempfile = SimpleNamespace(
        NamedTemporaryFile=partial(routes.tempfile.NamedTemporaryFile, dir=tmp_path),
        mkdtemp=partial(routes.tempfile.mkdtemp, dir=tmp_path),
    )
    manager = SimpleNamespace(
        tasks={}, update_task_status=Mock(), find_active_task_async=AsyncMock(return_value=None),
    )

    def submit_task(task_type, func, metadata):
        manager.callback = func
        manager.tasks["task"] = SimpleNamespace(
            task_id="task", task_type=task_type, metadata=metadata, result=None,
            status=SimpleNamespace(value="running"), progress=0,
            created_at=datetime(2026, 11, 12), updated_at=datetime(2026, 11, 12),
        )
        return "task"

    manager.submit_task = submit_task
    manager.get_task_async = AsyncMock(side_effect=lambda task_id: manager.tasks[task_id])
    sys.modules["landppt.services.background_tasks"].get_task_manager = lambda: manager
    routes.ppt_service = SimpleNamespace(project_manager=SimpleNamespace(
        get_project=AsyncMock(return_value=SimpleNamespace(topic="demo", slides_data=[{}, {}])),
    ))
    return routes, manager


def _presentation(count):
    prs = Presentation()
    for _ in range(count):
        prs.slides.add_slide(prs.slide_layouts[6])
    return prs


def _assert_audio(zip_file, page, source_index, duration):
    xml = etree.fromstring(zip_file.read(f"ppt/slides/slide{page}.xml"))
    rels = etree.fromstring(zip_file.read(f"ppt/slides/_rels/slide{page}.xml.rels"))
    audio_rid = xml.xpath(".//a:audioFile/@r:link", namespaces=NS)[0]
    media_rid = xml.xpath(".//p14:media/@r:embed", namespaces=NS)[0]
    audio_rel = rels.xpath(f'//*[@Id="{audio_rid}"]')[0]
    media_rel = rels.xpath(f'//*[@Id="{media_rid}"]')[0]
    assert audio_rel.get("Type") == RT.AUDIO
    assert media_rel.get("Type") == RT.MEDIA
    assert audio_rel.get("Target") == media_rel.get("Target")
    assert zip_file.read("ppt/" + audio_rel.get("Target").removeprefix("../")) == b"ID3" + bytes([source_index])
    assert not xml.xpath(".//a:videoFile | .//p:video", namespaces=NS)
    assert xml.xpath("./p:transition/@advTm", namespaces=NS) == [str(duration + 1000)]
    assert xml.xpath("./p:transition/@advClick", namespaces=NS) == ["1"]
    assert xml.xpath(".//p:audio/p:cMediaNode/@showWhenStopped", namespaces=NS) == ["0"]
    spid = xml.xpath(".//p:audio//p:spTgt/@spid", namespaces=NS)[0]
    main_seq = './/p:seq/p:cTn[@nodeType="mainSeq"]'
    assert xml.xpath(f'{main_seq}//p:cond[@evt="onBegin"]/p:tn/@val', namespaces=NS) == ["2"]
    play = xml.xpath(f'{main_seq}//p:cTn[@presetClass="mediacall"][@nodeType="afterEffect"]//p:cmd', namespaces=NS)[0]
    assert play.get("cmd") == "playFrom(0.0)"
    assert play.xpath(".//p:spTgt/@spid", namespaces=NS) == [spid]
    assert play.xpath("./p:cBhvr/p:cTn/@dur", namespaces=NS) == [str(duration)]
    ids = xml.xpath(".//p:cTn/@id", namespaces=NS)
    assert len(ids) == len(set(ids))
    assert xml.index(xml.find("p:transition", NS)) < xml.index(xml.find("p:timing", NS))


@pytest.mark.asyncio
async def test_embed_audio_skips_missing_sources_and_preserves_transition(exports, tmp_path):
    records, repo = _audios(exports, tmp_path, {1: 1000, 2: 2345})
    Path(records[1].file_path).unlink()
    prs = _presentation(3)
    transition = OxmlElement("p:transition")
    transition.append(OxmlElement("p:fade"))
    prs.slides[2]._element.append(transition)
    assert await exports._embed_narration_audio(prs, "project") == [1, 2]
    assert repo.get_latest_for_slide.await_args_list[2].kwargs == {
        "project_id": "project", "slide_index": 2, "language": "zh",
    }
    assert transition.find("p:fade", NS) is not None
    assert len(prs.slides[0].shapes) == len(prs.slides[1].shapes) == 0
    output = BytesIO()
    prs.save(output)
    assert len(Presentation(BytesIO(output.getvalue())).slides) == 3
    with ZipFile(output) as z:
        _assert_audio(z, 3, 2, 2345)
        types = etree.fromstring(z.read("[Content_Types].xml"))
        assert types.xpath('//*[@ContentType="audio/mpeg"]')


@pytest.mark.asyncio
@pytest.mark.parametrize("probed_duration", [4321, None])
async def test_nullable_cached_duration_is_probed_or_export_fails(exports, tmp_path, monkeypatch, probed_duration):
    _, repo = _audios(exports, tmp_path, {0: None})
    closed = []
    repo.close = lambda: closed.append(True)
    module = ModuleType("landppt.services.narration_service")
    module.ffprobe_duration_ms = AsyncMock(return_value=probed_duration)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    prs = _presentation(1)
    if probed_duration is None:
        with pytest.raises(ValueError, match="Cannot determine narration duration for slide 1"):
            await exports._embed_narration_audio(prs, "project")
    else:
        assert await exports._embed_narration_audio(prs, "project") == []
        assert prs.slides[0]._element.find("p:transition", NS).get("advTm") == "5321"
    assert closed == [True]


def _task_setup(exports):
    task = SimpleNamespace(task_id="task", metadata={"project_id": "project", "user_id": 1})
    exports._project_for_task = AsyncMock(return_value=SimpleNamespace(topic="demo", slides_data=[{}, {}]))
    exports._set_progress = AsyncMock()
    captured = []

    async def save(task, path, *args):
        captured.append(Path(path).read_bytes())
        return "artifact"

    exports._save_artifact = save
    return task, captured


@pytest.mark.asyncio
@pytest.mark.parametrize("script_error", [False, True])
async def test_standard_export_embeds_audio_without_speech_scripts(exports, tmp_path, script_error):
    _audios(exports, tmp_path, {0: 3210})
    task, captured = _task_setup(exports)
    scripts = AsyncMock(return_value=[])
    if script_error:
        scripts.side_effect = RuntimeError("script lookup failed")
    exports.SpeechScriptRepository = lambda: SimpleNamespace(get_current_speech_scripts_by_project=scripts, close=lambda: None)
    exports.get_pdf_converter = lambda: SimpleNamespace(is_available=lambda: True)

    async def convert(pdf, pptx):
        _presentation(2).save(pptx)
        return True, "ok"

    exports.get_pdf_to_pptx_converter = lambda: SimpleNamespace(
        is_available=lambda: True, set_user_id_async=AsyncMock(), convert_pdf_to_pptx_async=convert,
    )
    exports._generate_pdf_with_pyppeteer = AsyncMock(return_value=True)
    result = await exports.export_pptx(task)
    assert result["success"]
    assert result["skipped_audio_slides"] == [2]
    with ZipFile(BytesIO(captured[0])) as z:
        _assert_audio(z, 1, 0, 3210)


@pytest.mark.asyncio
async def test_screenshot_failure_keeps_original_notes_and_audio_indices(exports, tmp_path):
    _audios(exports, tmp_path, {2: 2222, 8: 8888})
    task, captured = _task_setup(exports)
    task.metadata["slides"] = [{"index": i, "html_content": str(i)} for i in [2, 5, 8, 11]]
    scripts = [SimpleNamespace(slide_index=i, script_content=f"notes-{i}") for i in [2, 5, 8, 11]]
    exports.SpeechScriptRepository = lambda: SimpleNamespace(
        get_current_speech_scripts_by_project=AsyncMock(return_value=scripts), close=lambda: None,
    )

    async def screenshot(html, png, **kwargs):
        if Path(html).read_text() == "5":
            return False
        Image.new("RGB", (16, 9)).save(png)
        return True

    exports.get_pdf_converter = lambda: SimpleNamespace(is_available=lambda: True, screenshot_html=screenshot)
    result = await exports.export_html_screenshot_pptx(task)
    assert result["success"]
    assert result["slide_count"] == 3
    assert result["skipped_audio_slides"] == [12]
    prs = Presentation(BytesIO(captured[0]))
    assert [s.notes_slide.notes_text_frame.text for s in prs.slides] == ["notes-2", "notes-8", "notes-11"]
    with ZipFile(BytesIO(captured[0])) as z:
        _assert_audio(z, 1, 2, 2222)
        _assert_audio(z, 2, 8, 8888)
        assert not etree.fromstring(z.read("ppt/slides/slide3.xml")).xpath(".//p:audio", namespaces=NS)


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_indices", [True, False])
async def test_inline_screenshot_failure_preserves_original_notes_audio_and_status(
    exports, inline_routes, tmp_path, explicit_indices,
):
    routes, manager = inline_routes
    indices = [2, 5, 8, 11] if explicit_indices else [0, 1, 2, 3]
    _audios(exports, tmp_path, {indices[0]: 2222, indices[2]: 8888})
    scripts = [SimpleNamespace(slide_index=i, script_content=f"notes-{i}") for i in indices]
    sys.modules["landppt.services.speech_script_repository"].SpeechScriptRepository = lambda: SimpleNamespace(
        get_current_speech_scripts_by_project=AsyncMock(return_value=scripts), close=lambda: None,
    )

    async def screenshot(html, png, **kwargs):
        if Path(html).read_text() == str(indices[1]):
            return False
        Image.new("RGB", (16, 9)).save(png)
        return True

    routes.get_pdf_converter = lambda: SimpleNamespace(is_available=lambda: True, screenshot_html=screenshot)
    exports._embed_narration_audio = AsyncMock(wraps=exports._embed_narration_audio)
    slides = [{"html_content": str(i), **({"index": i} if explicit_indices else {})} for i in indices]
    user = SimpleNamespace(id=1)
    response = await routes.export_project_pptx_from_images(
        "project", routes.ImagePPTXExportRequest(slides=slides), None, user=user,
    )
    assert json.loads(response.body)["task_id"] == "task"
    result = await manager.callback()
    assert result["success"]
    assert result["skipped_audio_slides"] == [indices[3] + 1]
    exports._embed_narration_audio.assert_awaited_once()
    assert exports._embed_narration_audio.await_args.kwargs["slide_indices"] == [indices[0], indices[2], indices[3]]
    prs = Presentation(result["pptx_path"])
    assert [s.notes_slide.notes_text_frame.text for s in prs.slides] == [f"notes-{i}" for i in [indices[0], indices[2], indices[3]]]
    with ZipFile(result["pptx_path"]) as z:
        _assert_audio(z, 1, indices[0], 2222)
        _assert_audio(z, 2, indices[2], 8888)
        assert not etree.fromstring(z.read("ppt/slides/slide3.xml")).xpath(".//p:audio", namespaces=NS)
    task = manager.tasks["task"]
    task.status = SimpleNamespace(value="completed")
    task.result = result
    status = json.loads((await routes.get_task_status("task", user=user)).body)
    assert status["result"] == {"success": True, "skipped_audio_slides": [indices[3] + 1]}


@pytest.mark.asyncio
@pytest.mark.parametrize("script_error", [False, True])
async def test_inline_standard_export_embeds_audio_without_scripts(exports, inline_routes, tmp_path, script_error):
    routes, manager = inline_routes
    _audios(exports, tmp_path, {0: 3210})
    scripts = AsyncMock(return_value=[])
    if script_error:
        scripts.side_effect = RuntimeError("script lookup failed")
    sys.modules["landppt.services.speech_script_repository"].SpeechScriptRepository = lambda: SimpleNamespace(
        get_current_speech_scripts_by_project=scripts, close=lambda: None,
    )
    routes.get_pdf_converter = lambda: SimpleNamespace(is_available=lambda: True)

    async def convert(pdf, pptx):
        _presentation(2).save(pptx)
        return True, "ok"

    routes.get_pdf_to_pptx_converter = lambda: SimpleNamespace(
        is_available=lambda: True, set_user_id_async=AsyncMock(), convert_pdf_to_pptx_async=convert,
    )
    routes._generate_pdf_with_pyppeteer = AsyncMock(return_value=True)
    response = await routes.export_project_pptx("project", user=SimpleNamespace(id=1))
    assert json.loads(response.body)["task_id"] == "task"
    result = await manager.callback()
    assert result["success"]
    assert result["skipped_audio_slides"] == [2]
    with ZipFile(result["pptx_path"]) as z:
        _assert_audio(z, 1, 0, 3210)


# --- explainer video pages ---------------------------------------------------------------

VIDEO_PAGE = '<div class="explainer-video-page" data-explainer-video-id="v1"><video src="/api/explainer-videos/v1/video.mp4"></video></div>'


@pytest.fixture
def explainer_video(tmp_path, monkeypatch):
    from landppt.services.export_infra import explainer_video_assets

    video, cover = tmp_path / "explainer.mp4", tmp_path / "cover.png"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42-explainer")
    Image.new("RGB", (192, 108), (200, 0, 0)).save(cover)
    monkeypatch.setattr(explainer_video_assets, "_query_video_files", {"v1": (str(video), str(cover))}.get)
    monkeypatch.setattr(explainer_video_assets, "_local_file", lambda video_id, key: Path(key))
    return video, cover


def _assert_video(zip_file, page, video_bytes, slide_size):
    xml = etree.fromstring(zip_file.read(f"ppt/slides/slide{page}.xml"))
    rels = etree.fromstring(zip_file.read(f"ppt/slides/_rels/slide{page}.xml.rels"))
    video_rid = xml.xpath(".//a:videoFile/@r:link", namespaces=NS)[0]
    media_rid = xml.xpath(".//p14:media/@r:embed", namespaces=NS)[0]
    poster_rid = xml.xpath(".//p:pic/p:blipFill/a:blip/@r:embed", namespaces=NS)[0]
    video_rel, media_rel = rels.xpath(f'//*[@Id="{video_rid}"]')[0], rels.xpath(f'//*[@Id="{media_rid}"]')[0]
    assert video_rel.get("Type") == RT.VIDEO and media_rel.get("Type") == RT.MEDIA
    assert video_rel.get("Target") == media_rel.get("Target")
    assert zip_file.read("ppt/" + video_rel.get("Target").removeprefix("../")) == video_bytes
    assert rels.xpath(f'//*[@Id="{poster_rid}"]')[0].get("Type") == RT.IMAGE
    ext = xml.xpath(".//p:pic/p:spPr/a:xfrm/a:ext", namespaces=NS)[0]
    assert (int(ext.get("cx")), int(ext.get("cy"))) == slide_size
    assert not xml.xpath(".//p:audio | .//a:audioFile", namespaces=NS)
    assert not xml.xpath("./p:transition/@advTm", namespaces=NS)
    types = etree.fromstring(zip_file.read("[Content_Types].xml"))
    assert types.xpath('//*[@Extension="mp4"][@ContentType="video/mp4"]')


@pytest.mark.asyncio
async def test_embed_replaces_audio_with_explainer_video_on_video_pages(exports, tmp_path, explainer_video):
    # Stale narration cached on the video page index must not be embedded.
    _audios(exports, tmp_path, {0: 1000, 1: 1111, 2: 2000})
    prs = _presentation(3)
    slides = [{"html_content": "a"}, {"html_content": VIDEO_PAGE}, {"html_content": "c"}]
    assert await exports._embed_narration_audio(prs, "project", slides_data=slides) == []
    output = BytesIO()
    prs.save(output)
    with ZipFile(output) as z:
        _assert_audio(z, 1, 0, 1000)
        _assert_video(z, 2, explainer_video[0].read_bytes(), (prs.slide_width, prs.slide_height))
        _assert_audio(z, 3, 2, 2000)


@pytest.mark.asyncio
async def test_screenshot_export_embeds_explainer_video_by_project_index(exports, tmp_path, explainer_video):
    _audios(exports, tmp_path, {0: 1000})
    task, captured = _task_setup(exports)
    exports._project_for_task.return_value.slides_data = [{"html_content": "a"}, {"html_content": VIDEO_PAGE}]
    task.metadata["slides"] = [{"index": 0, "html_content": "a"}, {"index": 1, "html_content": VIDEO_PAGE}]
    exports.SpeechScriptRepository = lambda: SimpleNamespace(
        get_current_speech_scripts_by_project=AsyncMock(return_value=[]), close=lambda: None,
    )

    async def screenshot(html, png, **kwargs):
        Image.new("RGB", (16, 9)).save(png)
        return True

    exports.get_pdf_converter = lambda: SimpleNamespace(is_available=lambda: True, screenshot_html=screenshot)
    result = await exports.export_html_screenshot_pptx(task)
    assert result["success"] and result["skipped_audio_slides"] == []
    prs = Presentation(BytesIO(captured[0]))
    with ZipFile(BytesIO(captured[0])) as z:
        _assert_audio(z, 1, 0, 1000)
        _assert_video(z, 2, explainer_video[0].read_bytes(), (prs.slide_width, prs.slide_height))


@pytest.mark.asyncio
async def test_inline_standard_export_embeds_explainer_video(exports, inline_routes, tmp_path, explainer_video):
    routes, manager = inline_routes
    routes.ppt_service.project_manager.get_project.return_value.slides_data = [
        {"html_content": "a"}, {"html_content": VIDEO_PAGE},
    ]
    _audios(exports, tmp_path, {0: 3210})
    sys.modules["landppt.services.speech_script_repository"].SpeechScriptRepository = lambda: SimpleNamespace(
        get_current_speech_scripts_by_project=AsyncMock(return_value=[]), close=lambda: None,
    )
    routes.get_pdf_converter = lambda: SimpleNamespace(is_available=lambda: True)

    async def convert(pdf, pptx):
        _presentation(2).save(pptx)
        return True, "ok"

    routes.get_pdf_to_pptx_converter = lambda: SimpleNamespace(
        is_available=lambda: True, set_user_id_async=AsyncMock(), convert_pdf_to_pptx_async=convert,
    )
    routes._generate_pdf_with_pyppeteer = AsyncMock(return_value=True)
    await routes.export_project_pptx("project", user=SimpleNamespace(id=1))
    result = await manager.callback()
    assert result["success"] and result["skipped_audio_slides"] == []
    prs = Presentation(result["pptx_path"])
    with ZipFile(result["pptx_path"]) as z:
        _assert_audio(z, 1, 0, 3210)
        _assert_video(z, 2, explainer_video[0].read_bytes(), (prs.slide_width, prs.slide_height))
