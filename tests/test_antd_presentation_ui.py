"""Real built viewer controls, unchanged srcdoc, owner/share and narration contracts."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from test_app_ui import browser, ui_page  # noqa: F401

SIZES = [(1440, 900), (1024, 768), (390, 844), (320, 568), (844, 390)]
TITLE = "设计流程与跨部门协作：从内容到演示"
OUT = Path("artifacts/viewer-theme-20261024")


def slide(index):
    return dict(
        slide_id=f"slide-{index}",
        slide_index=index,
        title=f"协作流程 {index + 1}",
        html_content=f'<!doctype html><html lang="zh"><meta charset="utf-8"><body style="margin:0;width:1280px;height:720px;background:#eee7d4;color:#27231c"><main style="padding:80px"><h1 style="font-size:54px">协作流程 {index + 1}</h1><p style="font-size:28px">组织内容 · 确认设计 · 持续优化</p></main></body></html>',
    )


def setup(
    page,
    load,
    shared=False,
    speech=False,
    languages=None,
    count=4,
    query="",
    title=TITLE,
):
    page.add_init_script(
        """window.testAudio=[];window.testIntervals=[];window.testClosed=0;
        window.close=()=>testClosed++;
        const originalInterval=window.setInterval;
        window.setInterval=(callback,ms,...args)=>{const id=originalInterval(callback,ms,...args);testIntervals.push({callback,ms,id});return id;};
        window.Audio=class extends EventTarget {
            constructor(){super();this.currentTime=0;this.paused=true;this.src='';testAudio.push(this);}
            async play(){if(window.failAudio)throw Error('blocked');this.paused=false;this.dispatchEvent(new Event('play'));}
            pause(){this.paused=true;this.dispatchEvent(new Event('pause'));}
            load(){} removeAttribute(name){if(name==='src')this.src='';}
        };"""
    )
    state = dict(
        reads=[],
        writes=[],
        errors=[],
        slides=[slide(i) for i in range(count)],
        updated=100,
        fail=False,
    )
    page.on("pageerror", lambda error: state["errors"].append(str(error)))

    def api(route):
        req = route.request
        state["reads" if req.method == "GET" else "writes"].append(req.url)
        path = urlsplit(req.url).path
        if "/narration/cues/" in path:
            result = dict(
                success=True,
                duration_ms=2000,
                cues=[dict(start_ms=0, end_ms=2000, text="流程介绍 <b>保持纯文本</b>")],
            )
        else:
            result = dict(
                status="success",
                slides_data=state["slides"],
                total_slides=len(state["slides"]),
                updated_at=state["updated"],
            )
        route.fulfill(
            status=503 if state["fail"] else 200,
            json=result if not state["fail"] else dict(detail="讲解服务暂时不可用"),
        )

    page.route("**/api/**", api)
    load(
        "pages/project/project_fullscreen_presentation.html",
        path=("/share/share-test" if shared else "/projects/ui-viewer/fullscreen")
        + query,
        project=dict(
            project_id="ui-viewer",
            title=title,
            updated_at=100,
            slides_data=state["slides"],
        ),
        slides_count=count,
        is_shared=shared,
        share_token="share-test" if shared else "",
        has_speech_scripts=speech,
        narration_languages=languages or ["zh", "en"],
    )
    if count:
        page.get_by_role("heading", name=title, exact=True).wait_for()
        expect(page.locator(".slide-frame.visible")).to_have_count(1)
    else:
        page.get_by_text("暂无可放映的页面", exact=True).wait_for()
    return state


def counter(page, text):
    expect(page.get_by_role("status", name="当前页", exact=True)).to_have_text(text)


def keyboard(page, key):
    page.locator("body").focus()
    page.keyboard.press(key)
    page.wait_for_timeout(220)


def choose_language(page, value):
    page.get_by_role("combobox", name="讲解语言").click()
    page.locator(".ant-select-dropdown:visible .ant-select-item-option-content").filter(
        has_text=re.compile("^" + re.escape(value) + "$")
    ).click()


def open_panel(page, label):
    if label == "页面缩略图":
        trigger = page.get_by_role("button", name=label, exact=True)
        trigger.click()
    else:
        trigger = page.get_by_role("button", name="更多操作", exact=True)
        trigger.click()
        page.get_by_role("menuitem", name=label, exact=True).click()
    return trigger


@pytest.mark.parametrize("width,height", SIZES)
@pytest.mark.parametrize(
    "scene", ["owned", "shared", "narration", "thumbnails", "shortcuts"]
)
def test_viewer_geometry_and_unchanged_slide_documents(ui_page, width, height, scene):
    page, load = ui_page
    page.set_viewport_size(dict(width=width, height=height))
    state = setup(page, load, shared=scene == "shared", speech=scene == "narration")
    if scene == "narration":
        expect(page.get_by_role("button", name="讲解", exact=True)).to_be_visible()
        page.get_by_role("button", name="讲解", exact=True).click()
        expect(page.get_by_role("status", name="讲解字幕")).to_have_text(
            "流程介绍 <b>保持纯文本</b>"
        )
        assert page.locator(".presentation-subtitle b").count() == 0
    if scene in ["thumbnails", "shortcuts"]:
        open_panel(page, "页面缩略图" if scene == "thumbnails" else "快捷键")
        panel = page.get_by_role(
            "dialog",
            name="页面缩略图" if scene == "thumbnails" else "快捷键",
            exact=True,
        )
        expect(panel).to_be_visible()
        if scene == "thumbnails":
            selected = panel.locator('[aria-current="page"]')
            expect(selected).to_have_css("background-color", "rgb(31, 31, 34)")
            expect(selected).to_have_css("border-top-color", "rgb(245, 245, 245)")
            expect(selected.locator(".presentation-thumbnail-label")).to_have_css(
                "color", "rgb(161, 161, 170)"
            )
        else:
            expect(panel.locator("kbd").first).to_have_css(
                "background-color", "rgb(31, 31, 34)"
            )
            expect(panel.locator("kbd").first).to_have_css(
                "color", "rgb(236, 236, 238)"
            )
        assert panel.bounding_box()["width"] <= width
        if scene == "shortcuts":
            escape = panel.get_by_text("Esc", exact=True)
            assert escape.evaluate(
                "e=>{const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}"
            )
        page.keyboard.press("Escape")
        expect(panel).not_to_be_visible()
    for label in ["下一页", "页面缩略图", "全屏", "退出演示"]:
        button = page.get_by_role("button", name=label, exact=True)
        box = button.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width
        assert box["y"] >= 0 and box["y"] + box["height"] <= height
        assert box["height"] >= (44 if width <= 600 or height <= 450 else 32)
        assert button.evaluate(
            "e=>{const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}"
        )
    frame = page.locator(".slide-frame.visible")
    assert frame.get_attribute("srcdoc") == state["slides"][0]["html_content"]
    expect(page.locator("body")).to_have_css("background-color", "rgb(11, 11, 12)")
    for toolbar in page.locator(".presentation-toolbar").all():
        expect(toolbar).to_have_css("background-color", "rgb(22, 22, 24)")
        expect(toolbar).to_have_css("color", "rgb(236, 236, 238)")
    expect(page.get_by_role("status", name="当前页", exact=True)).to_have_css(
        "color", "rgb(161, 161, 170)"
    )
    expect(frame.content_frame.locator("body")).to_have_css(
        "background-color", "rgb(238, 231, 212)"
    )
    expect(frame.content_frame.locator("body")).to_have_css("color", "rgb(39, 35, 28)")
    if scene in ["owned", "shared"]:
        OUT.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(OUT / f"presentation-{scene}-{width}x{height}.png"))
    rect = frame.bounding_box()
    canvas = page.locator(".slide-display").bounding_box()
    assert abs(rect["width"] / rect["height"] - 16 / 9) < 0.01
    assert abs(rect["width"] - canvas["width"]) < 1.5
    assert abs(rect["height"] - canvas["height"]) < 1.5
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not state["errors"] and not state["writes"]
    assert page.locator('a[href="/admin"],a[href="/auth/profile"]').count() == 0


def test_buttons_keyboard_bounds_and_cached_navigation(ui_page):
    page, load = ui_page
    state = setup(page, load)
    expect(page.get_by_role("button", name="上一页", exact=True)).to_be_disabled()
    page.get_by_role("button", name="下一页", exact=True).click()
    counter(page, "2 / 4")
    second_id = page.locator(".slide-frame.visible").get_attribute("id")
    keyboard(page, "Home")
    counter(page, "1 / 4")
    keyboard(page, "ArrowRight")
    counter(page, "2 / 4")
    assert page.locator(".slide-frame.visible").get_attribute("id") == second_id
    keyboard(page, "End")
    counter(page, "4 / 4")
    expect(page.get_by_role("button", name="下一页", exact=True)).to_be_disabled()
    keyboard(page, "ArrowRight")
    counter(page, "4 / 4")
    keyboard(page, "ArrowLeft")
    counter(page, "3 / 4")
    assert not state["errors"]


@pytest.mark.parametrize(
    "panel,label", [("slides", "页面缩略图"), ("keys", "快捷键"), ("cache", "缓存状态")]
)
def test_panels_trap_focus_and_escape_without_exiting(ui_page, panel, label):
    page, load = ui_page
    setup(page, load)
    trigger = open_panel(page, label)
    dialog = page.get_by_role("dialog", name=label, exact=True)
    expect(dialog).to_be_visible()
    controls = dialog.locator("button:visible")
    controls.last.focus()
    page.keyboard.press("Tab")
    assert controls.first.evaluate("e=>e===document.activeElement")
    page.keyboard.press("Shift+Tab")
    assert controls.last.evaluate("e=>e===document.activeElement")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("LandPPTPresentation.getState().index") == 0
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()
    assert page.evaluate("testClosed") == 0


def test_thumbnail_selection_scales_original_document_and_restores_focus(ui_page):
    page, load = ui_page
    state = setup(page, load)
    trigger = page.get_by_role("button", name="页面缩略图", exact=True)
    trigger.click()
    panel = page.get_by_role("dialog", name="页面缩略图")
    frames = panel.locator("iframe")
    expect(frames).to_have_count(4)
    assert [frame.get_attribute("srcdoc") for frame in frames.all()] == [
        item["html_content"] for item in state["slides"]
    ]
    thumb = panel.get_by_role("button", name="第 3 页：协作流程 3")
    thumb.click()
    expect(panel).not_to_be_visible()
    counter(page, "3 / 4")
    expect(trigger).to_be_focused()
    assert (
        page.locator(".slide-frame.visible").get_attribute("srcdoc")
        == state["slides"][2]["html_content"]
    )


def test_autoplay_single_timer_pause_visibility_and_last_page(ui_page):
    page, load = ui_page
    setup(page, load)
    page.get_by_role("button", name="自动播放", exact=True).click()
    expect(page.get_by_role("button", name="暂停自动播放", exact=True)).to_be_visible()
    assert page.evaluate("testIntervals.filter(x=>x.ms===5000).length") == 1
    page.evaluate("testIntervals.find(x=>x.ms===5000).callback()")
    counter(page, "2 / 4")
    page.evaluate(
        "Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))"
    )
    expect(page.get_by_role("button", name="自动播放", exact=True)).to_be_visible()
    assert not page.evaluate("LandPPTPresentation.getState().autoPlaying")
    keyboard(page, "End")
    expect(page.get_by_role("button", name="自动播放", exact=True)).to_be_disabled()


def test_autohide_reveals_by_keyboard_and_keeps_focused_controls(ui_page):
    page, load = ui_page
    setup(page, load)
    page.mouse.move(0, 0)
    page.wait_for_function(
        "document.body.classList.contains('ui-hidden')", timeout=4000
    )
    assert page.locator(".presentation-ui").get_attribute("inert") is not None
    page.keyboard.press("Tab")
    page.wait_for_function("!document.body.classList.contains('ui-hidden')")
    page.get_by_role("button", name="下一页", exact=True).focus()
    page.wait_for_timeout(2400)
    assert not page.evaluate("LandPPTPresentation.getState().uiHidden")


@pytest.mark.parametrize("shared", [False, True])
def test_narration_owner_share_api_language_subtitle_and_stop(ui_page, shared):
    page, load = ui_page
    state = setup(page, load, shared=shared, speech=True, query="?language=en")
    expect(page.get_by_role("combobox", name="讲解语言")).to_have_value("")
    assert page.evaluate("LandPPTPresentation.getState().language") == "en"
    page.get_by_role("button", name="讲解", exact=True).click()
    expect(page.get_by_role("status", name="讲解字幕")).to_have_text(
        "流程介绍 <b>保持纯文本</b>"
    )
    audio = page.evaluate("testAudio[0].src")
    expected = "/api/share/share-test" if shared else "/api/projects/ui-viewer"
    assert audio.startswith(expected + "/narration/audio/0?language=en")
    assert ("autogen=1" in audio) is not shared
    choose_language(page, "中文")
    page.wait_for_function("testAudio[0].src.includes('language=zh')")
    page.get_by_role("button", name="停止讲解", exact=True).click()
    assert page.evaluate("testAudio[0].paused && testAudio[0].src === ''")
    expect(page.get_by_role("status", name="讲解字幕")).to_have_count(0)
    assert all(urlsplit(url).path.startswith(expected + "/") for url in state["reads"])
    assert all(
        ("autogen=1" in url) is not shared
        for url in state["reads"]
        if "/narration/" in url
    )
    assert not state["errors"] and not state["writes"]


def test_select_keyboard_never_changes_slide_and_disabled_language(ui_page):
    page, load = ui_page
    setup(page, load, speech=True, languages=["zh"], query="?language=en")
    assert page.evaluate("LandPPTPresentation.getState().language") == "zh"
    language = page.get_by_role("combobox", name="讲解语言")
    language.focus()
    page.keyboard.press("ArrowRight")
    counter(page, "1 / 4")
    language.click()
    expect(
        page.locator(".ant-select-dropdown:visible .ant-select-item-option-disabled")
    ).to_contain_text("English")
    page.keyboard.press("Escape")
    assert page.evaluate("testClosed") == 0


@pytest.mark.parametrize("failure", ["cues", "play"])
def test_narration_errors_stop_playback_and_allow_retry(ui_page, failure):
    page, load = ui_page
    state = setup(page, load, speech=True)
    state["fail"] = failure == "cues"
    page.evaluate("window.failAudio=" + ("true" if failure == "play" else "false"))
    page.get_by_role("button", name="讲解", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text(
        "讲解服务暂时不可用" if failure == "cues" else "浏览器限制自动播放"
    )
    expect(page.get_by_role("button", name="讲解", exact=True)).to_be_visible()
    assert not page.evaluate("LandPPTPresentation.getState().narration")
    state["fail"] = False
    page.evaluate("window.failAudio=false")
    page.get_by_role("button", name="讲解", exact=True).click()
    expect(page.get_by_role("button", name="停止讲解", exact=True)).to_be_visible()
    expect(page.get_by_role("status", name="讲解字幕")).to_be_visible()
    assert not state["errors"]


def test_stop_while_cues_pending_cannot_restart_audio(ui_page):
    page, load = ui_page
    setup(page, load, speech=True)
    page.evaluate(
        """window.cueResolve=null;const original=window.fetch;window.fetch=(url,...args)=>String(url).includes('/narration/cues/')?new Promise(resolve=>{cueResolve=()=>resolve(new Response(JSON.stringify({success:true,cues:[],duration_ms:1}),{status:200}));}):original(url,...args);"""
    )
    page.get_by_role("button", name="讲解", exact=True).click()
    page.wait_for_function("cueResolve !== null")
    page.get_by_role("button", name="停止讲解", exact=True).click()
    page.evaluate("cueResolve()")
    page.wait_for_timeout(150)
    assert page.evaluate("testAudio.length") == 0
    assert not page.evaluate("LandPPTPresentation.getState().narration")


@pytest.mark.parametrize("shared", [False, True])
def test_only_owner_polls_and_live_update_clamps_deleted_pages(ui_page, shared):
    page, load = ui_page
    state = setup(page, load, shared=shared)
    polls = page.evaluate("testIntervals.filter(x=>x.ms===10000).length")
    assert polls == (0 if shared else 1)
    if shared:
        assert not state["reads"]
        return
    keyboard(page, "End")
    state["slides"] = [slide(0), slide(1)]
    state["updated"] = 101
    page.evaluate("testIntervals.find(x=>x.ms===10000).callback()")
    counter(page, "2 / 2")
    expect(page.locator(".slide-frame.visible")).to_have_count(1)
    assert (
        page.locator(".slide-frame.visible").get_attribute("srcdoc")
        == state["slides"][1]["html_content"]
    )
    before_id = page.locator(".slide-frame.visible").get_attribute("id")
    page.evaluate("testIntervals.find(x=>x.ms===10000).callback()")
    page.wait_for_timeout(150)
    assert page.locator(".slide-frame.visible").get_attribute("id") == before_id
    assert not state["errors"]


def test_fullscreen_control_and_escape_leave_fullscreen_first(ui_page):
    page, load = ui_page
    setup(page, load)
    page.evaluate(
        """Object.defineProperty(document,'fullscreenElement',{configurable:true,get:()=>window.testFullscreen?document.documentElement:null});document.documentElement.requestFullscreen=async()=>{window.testFullscreen=true;document.dispatchEvent(new Event('fullscreenchange'));};document.exitFullscreen=async()=>{window.testFullscreen=false;document.dispatchEvent(new Event('fullscreenchange'));};"""
    )
    page.get_by_role("button", name="全屏", exact=True).click()
    expect(page.get_by_role("button", name="退出全屏", exact=True)).to_be_visible()
    assert page.locator("body").evaluate("e=>e.classList.contains('fullscreen-mode')")
    keyboard(page, "Escape")
    expect(page.get_by_role("button", name="全屏", exact=True)).to_be_visible()
    assert page.evaluate("testClosed") == 0


def test_public_data_contains_no_account_fields_and_escapes_titles(ui_page):
    page, load = ui_page
    title = '</script><img src=x onerror="window.titleExecuted=true">'
    state = setup(page, load, shared=True, title=title)
    config = json.loads(page.locator("#antdData").text_content())
    assert set(config) == {
        "page",
        "projectId",
        "title",
        "slidesCount",
        "slidesData",
        "updatedAt",
        "isShared",
        "shareToken",
        "hasSpeechScripts",
        "narrationLanguages",
    }
    assert page.locator(".app-header,.shell-header").count() == 0
    assert not state["reads"]
    assert not page.evaluate("window.titleExecuted || false")
    assert page.locator(".presentation-topbar img").count() == 0


def test_autoplay_drives_narration_when_speech_exists(ui_page):
    page, load = ui_page
    setup(page, load, speech=True, query="?narration=1&language=en")
    expect(page.get_by_role("button", name="停止讲解", exact=True)).to_be_visible()
    expect(page.get_by_role("status", name="讲解字幕")).to_be_visible()
    # While narrating, the play control reports playing and pauses the narration.
    page.get_by_role("button", name="暂停自动播放", exact=True).click()
    assert not page.evaluate("LandPPTPresentation.getState().narration")
    assert page.evaluate("testAudio[0].paused")
    # Play starts narration (which advances slides itself), not the timed interval.
    page.get_by_role("button", name="自动播放", exact=True).click()
    assert page.evaluate("LandPPTPresentation.getState().narration")
    assert not page.evaluate("testIntervals.some(x => x.ms === 5000)")
    expect(page.get_by_role("button", name="停止讲解", exact=True)).to_be_visible()


def test_unavailable_narration_is_hidden_and_shortcut_reports_reason(ui_page):
    page, load = ui_page
    state = setup(page, load)
    assert page.get_by_role("button", name="讲解", exact=True).count() == 0
    assert page.get_by_role("combobox", name="讲解语言").count() == 0
    keyboard(page, "n")
    expect(page.get_by_role("alert")).to_contain_text("暂无演讲稿")
    assert not state["reads"]


def test_navigation_to_pending_preload_finishes_when_frame_loads(ui_page):
    page, load = ui_page
    state = setup(page, load, count=2)
    waiting = []
    page.route("**/delayed-slide.css", lambda route: waiting.append(route))
    state["updated"] = 101
    state["slides"][1]["html_content"] = state["slides"][1]["html_content"].replace(
        "<meta", '<link rel="stylesheet" href="/delayed-slide.css"><meta', 1
    )
    page.evaluate("testIntervals.find(x=>x.ms===10000).callback()")
    expect(page.locator(".slide-frame.visible")).to_have_count(1)
    page.wait_for_function(
        "document.querySelectorAll('.slide-frame.not-loaded').length > 0"
    )
    page.get_by_role("button", name="下一页", exact=True).click()
    assert page.evaluate("LandPPTPresentation.getState().transitioning")
    assert waiting
    for route in waiting:
        route.fulfill(body="", content_type="text/css")
    counter(page, "2 / 2")
    assert not page.evaluate("LandPPTPresentation.getState().transitioning")
    assert not state["errors"]


@pytest.mark.parametrize("shared", [False, True])
def test_exit_fallback_retains_owner_and_public_navigation(ui_page, shared):
    page, load = ui_page
    setup(page, load, shared=shared)
    page.evaluate(
        "() => {window.testBack=0;history.back=()=>testBack++;window.close=()=>sessionStorage.setItem('viewerClosed','1');}"
    )
    page.get_by_role("button", name="退出演示", exact=True).click()
    page.wait_for_timeout(240)
    assert page.evaluate("sessionStorage.getItem('viewerClosed')") == "1"
    if shared:
        assert "/projects/" not in page.url
        assert page.evaluate("testBack") == 1
    else:
        assert urlsplit(page.url).path == "/projects/ui-viewer"


def test_fullscreen_browser_and_iframe_canvas_keep_original_document(ui_page):
    page, load = ui_page
    state = setup(page, load)
    page.get_by_role("button", name="全屏", exact=True).click()
    page.wait_for_function("!!document.fullscreenElement")
    expect(page.get_by_role("button", name="退出全屏", exact=True)).to_be_visible()
    assert (
        page.locator(".slide-frame.visible").get_attribute("srcdoc")
        == state["slides"][0]["html_content"]
    )
    page.get_by_role("button", name="退出全屏", exact=True).click()
    page.wait_for_function("!document.fullscreenElement")
    assert not state["errors"]


@pytest.mark.parametrize("count", [0, 1])
def test_empty_and_single_slide_never_start_invalid_navigation(ui_page, count):
    page, load = ui_page
    state = setup(page, load, shared=True, count=count)
    if count:
        counter(page, "1 / 1")
        for label in ["上一页", "下一页", "自动播放"]:
            expect(page.get_by_role("button", name=label, exact=True)).to_be_disabled()
    else:
        expect(page.get_by_text("暂无可放映的页面", exact=True)).to_be_visible()
        expect(page.locator(".presentation-empty .ant-result")).to_have_css(
            "background-color", "rgb(22, 22, 24)"
        )
        assert page.get_by_role("status", name="当前页").count() == 0
    assert not state["errors"] and not state["reads"]


def test_keyboard_after_clicking_slide_respects_embedded_inputs(ui_page):
    page, load = ui_page
    setup(page, load)
    frame = page.locator(".slide-frame.visible").content_frame
    frame.get_by_role("heading", name="协作流程 1").click()
    page.keyboard.press("ArrowRight")
    counter(page, "2 / 4")
    current = page.locator(".slide-frame.visible").content_frame
    current.locator("body").evaluate(
        "e=>{const input=document.createElement('input');input.setAttribute('aria-label','演示内输入');e.append(input);}"
    )
    current.get_by_role("textbox", name="演示内输入").focus()
    page.wait_for_timeout(220)
    page.keyboard.press("ArrowRight")
    counter(page, "2 / 4")
