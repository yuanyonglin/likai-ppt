"""Main navigation reuses the shell while reading actual Jinja route responses."""

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from test_app_ui import ROOT, browser, detail_project, template_environment, ui_page  # noqa: F401

ROUTES = {
    "/home": ("pages/home/index.html", "让想法， 成为好演示。", "home"),
    "/dashboard": ("pages/project/project_dashboard.html", "首页", "dashboard"),
    "/projects": ("pages/project/projects_list.html", "项目列表", "projects"),
    "/create": ("pages/project/scenarios.html", "创建 PPT", "create-project"),
    "/scenarios": ("pages/project/scenarios.html", "创建 PPT", "create-project"),
    "/global-master-templates": (
        "pages/template/global_master_templates.html",
        "模板管理",
        "templates",
    ),
    "/image-gallery": (
        "pages/image/image_gallery.html",
        "本地图床",
        "image-gallery",
    ),
    "/ai-config": ("pages/settings/ai_config.html", "系统配置管理", "settings"),
    "/projects/ui-detail": ("pages/project/project_detail.html", "项目工作区体验优化与下一阶段计划", "project-detail"),
    "/auth/profile": ("pages/account/profile.html", "个人资料", "profile"),
    "/credits": ("pages/account/user_credits.html", "我的积分", "credits"),
    "/admin/users": ("pages/admin/users.html", "用户管理", "admin-users"),
    "/admin/smtp": ("pages/admin/smtp.html", "邮件服务设置", "admin-settings"),
    "/admin/oauth": ("pages/admin/oauth.html", "OAuth 设置", "admin-settings"),
    "/admin/credits": ("pages/admin/credits.html", "积分管理", "admin-credits"),
    "/admin/community": ("pages/admin/community.html", "运营设置", "admin-community"),
    "/research": ("pages/project/research_status.html", "深度研究", "research"),
    "/image-generation-test": ("pages/image/image_generation_test.html", "AI 图片生成", "image-generation"),
    "/sponsors": ("pages/community/sponsor_thanks.html", "赞助感谢", "sponsor-thanks"),
}
PATHS = [
    "/home",
    "/projects",
    "/global-master-templates",
    "/image-gallery",
    "/ai-config",
    "/dashboard",
]
SIZES = [(1920, 1080), (1440, 900), (1024, 768), (390, 844), (320, 568), (844, 390)]


def navigation_app(page, env=None):
    env = env or template_environment(ROOT / "src/landppt/web/templates")
    user = dict(id=1, username="nav-user", is_admin=True)
    state = dict(
        reads=[],
        errors=[],
        overrides={},
        handlers={},
        values=dict(
            user=user,
            request=SimpleNamespace(state=SimpleNamespace(user=user)),
            credits_enabled=True,
            projects=[],
            recent_projects=[],
            page=1,
            page_size=20,
            total=0,
            status_filter=None,
            current_config={},
            current_provider="openai",
            provider_status={},
            ai_provider="openai",
            scenarios=[
                dict(id="general", name="通用"),
                dict(id="technology", name="科技技术"),
            ],
            selected_scenario="general",
            template_options=[],
            unattended_defaults=None,
            versions=[],
            todo_board=None,
        ),
    )
    page.on("pageerror", lambda error: state["errors"].append(str(error)))

    def render(path, **values):
        merged = {**state["values"], **state["overrides"].get(path, {}), **values}
        merged["request"] = SimpleNamespace(state=SimpleNamespace(user=merged["user"]))
        return env.get_template(ROUTES[path][0]).render(**merged)

    def serve(route):
        req = route.request
        path = urlsplit(req.url).path
        if path not in ROUTES and path != "/auth/login":
            route.fallback()
            return
        assert req.method == "GET"
        state["reads"].append(dict(path=path, kind=req.resource_type, url=req.url))
        if path in state["handlers"]:
            state["handlers"][path](route)
        elif path == "/auth/login":
            route.fulfill(
                body="<!doctype html><title>登录</title><h1>登录</h1>",
                content_type="text/html; charset=utf-8",
            )
        else:
            route.fulfill(body=render(path), content_type="text/html; charset=utf-8")

    page.route("http://ui.test/**", serve)
    state["render"] = render
    return state


def open_navigation(page, path):
    if page.get_by_role("button", name="菜单", exact=True).is_visible():
        if (
            page.get_by_role("button", name="菜单", exact=True).get_attribute(
                "aria-expanded"
            )
            != "true"
        ):
            page.get_by_role("button", name="菜单", exact=True).click()
    scope = '.ant-drawer' if page.get_by_role('button', name='菜单', exact=True).is_visible() else '.app-shell'
    link = page.locator(f'{scope} a[href="{path}"]').filter(visible=True).first
    if path == "/create" and not link.count():
        # Desktop project creation uses the existing /scenarios alias.
        link = page.locator(f'{scope} a[href="/scenarios"]').filter(visible=True).first
    link.click()
    expect(
        page.get_by_role("heading", name=ROUTES[path][1], exact=True)
    ).to_be_visible()
    expect(page.locator("#appMain")).to_have_attribute("aria-busy", "false")


@pytest.mark.parametrize("width,height", SIZES)
def test_project_detail_navigation_keeps_shell_and_reads_fresh_metadata(ui_page, detail_project, width, height):
    page, _ = ui_page
    state = navigation_app(page)
    state["values"].update(project=detail_project, projects=[{**detail_project, "updated_at": detail_project["created_at"], "todo_board": None}], total=1)
    page.set_viewport_size(dict(width=width, height=height))
    page.goto("http://ui.test/projects", wait_until="networkidle")
    page.evaluate("() => {window.navHeader=document.querySelector('.app-header');window.navMarker='detail';}")
    page.locator('.project-title:visible').first.click()
    expect(page.get_by_role("heading", name=detail_project["title"], exact=True)).to_be_visible()
    expect(page.locator("#appMain")).to_have_attribute("aria-busy", "false")
    assert page.evaluate("window.navHeader===document.querySelector('.app-header')")
    assert page.locator('.app-sidebar .app-nav a[href="/projects"]').get_attribute("aria-current") == "page"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
    page.get_by_role("link", name="返回项目列表", exact=True).click()
    expect(page.get_by_role("heading", name="项目列表", exact=True)).to_be_visible()
    state["overrides"]["/projects/ui-detail"] = dict(project={**detail_project, "topic": "后退时重新获取的主题"})
    page.go_back()
    expect(page.get_by_text("后退时重新获取的主题", exact=True)).to_be_visible()
    assert page.evaluate("window.navMarker") == "detail"
    assert sum(read["kind"] == "document" for read in state["reads"]) == 1
    assert sum(read["path"] == "/projects/ui-detail" for read in state["reads"]) == 2
    assert not state["errors"]


@pytest.mark.parametrize("path", ["/auth/profile", "/credits", "/admin/users", "/admin/smtp", "/admin/oauth", "/admin/credits", "/admin/community", "/research", "/image-generation-test", "/sponsors"])
def test_ordinary_shell_pages_share_navigation_from_detail(ui_page, detail_project, path):
    page, _ = ui_page
    state = navigation_app(page)
    state["values"].update(project=detail_project)
    page.goto("http://ui.test/projects/ui-detail", wait_until="networkidle")
    page.evaluate("window.navMarker='detail-start'")
    page.evaluate("path => {const a=document.createElement('a');a.href=path;a.textContent='打开目标页';document.getElementById('appMain').prepend(a)}", path)
    page.get_by_role("link", name="打开目标页", exact=True).click()
    expect(page.get_by_role("heading", name=ROUTES[path][1], exact=True)).to_be_visible()
    expect(page.locator("#appMain")).to_have_attribute("aria-busy", "false")
    assert page.evaluate("window.navMarker") == "detail-start"
    assert sum(read["kind"] == "document" for read in state["reads"]) == 1
    if path == "/admin/community":
        assert page.locator("#communityBlueprint").count() == 1
    open_navigation(page, "/projects")
    assert page.locator("#communityBlueprint").count() == 0
    assert not state["errors"]


def test_account_popup_link_uses_same_document(ui_page, detail_project):
    page, _ = ui_page
    state = navigation_app(page)
    state["values"].update(project=detail_project)
    page.goto("http://ui.test/projects/ui-detail", wait_until="networkidle")
    page.evaluate("window.navMarker='account-popup'")
    page.get_by_role("button", name="账户菜单", exact=True).click()
    page.locator('.app-account-menu').get_by_role("link", name="个人资料", exact=True).click()
    expect(page.get_by_role("heading", name="个人资料", exact=True)).to_be_visible()
    assert page.evaluate("window.navMarker") == "account-popup"
    assert not state["errors"]


def test_navigation_never_shows_sponsor_entry(ui_page):
    page, _ = ui_page
    page.route("**/api/community/public-settings", lambda route: route.fulfill(json={"sponsor_page_enabled": True}))
    state = navigation_app(page)
    state["values"]["user"] = dict(username="sponsor-reader", is_admin=True)
    page.goto("http://ui.test/dashboard", wait_until="networkidle")
    expect(page.get_by_role("heading", name="首页", exact=True)).to_be_visible()
    expect(page.locator("#navSponsorLink")).to_have_count(0)
    open_navigation(page, "/projects")
    expect(page.locator("#navSponsorLink")).to_have_count(0)
    assert not state["errors"]


@pytest.mark.parametrize("suffix", ["edit", "todo", "fullscreen", "select-template"])
def test_project_workspaces_keep_native_navigation(ui_page, detail_project, suffix):
    page, _ = ui_page
    state = navigation_app(page)
    state["values"].update(project=detail_project)
    page.route(f"**/projects/ui-detail/{suffix}", lambda route: route.fulfill(body='<meta charset="utf-8"><h1>独立工作区</h1>', content_type="text/html; charset=utf-8"))
    page.goto("http://ui.test/projects/ui-detail", wait_until="networkidle")
    page.evaluate("window.navMarker='ordinary-page'")
    page.evaluate("href => {const a=document.createElement('a');a.href=href;a.textContent='打开工作区';document.getElementById('appMain').prepend(a)}", f"/projects/ui-detail/{suffix}")
    page.get_by_role("link", name="打开工作区", exact=True).click()
    expect(page.get_by_role("heading", name="独立工作区", exact=True)).to_be_visible()
    assert page.evaluate("window.navMarker===undefined")


@pytest.mark.parametrize("width,height", SIZES)
def test_navigation_reuses_shell_across_all_main_pages(ui_page, width, height):
    page, _ = ui_page
    page.set_viewport_size(dict(width=width, height=height))
    state = navigation_app(page)
    page.goto("http://ui.test/dashboard", wait_until="networkidle")
    expect(page.get_by_role("heading", name="首页", exact=True)).to_be_visible()
    page.evaluate(
        "() => {window.navHeader=document.querySelector('.app-header');window.navMarker='same-document';}"
    )
    expect(page.locator(".app-brand:visible")).to_have_text("LandPPT")
    expect(page.locator(".sidebar-wordmark")).to_have_text("LandPPT")
    for path in PATHS:
        open_navigation(page, path)
        assert page.evaluate(
            "window.navMarker === 'same-document' && window.navHeader === document.querySelector('.app-header')"
        )
        assert urlsplit(page.url).path == path
        assert (
            page.evaluate(
                "JSON.parse(document.getElementById('antdData').textContent).page"
            )
            == ROUTES[path][2]
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
    assert sum(item["kind"] == "document" for item in state["reads"]) == 1
    # The dashboard reads the existing creation HTML to use the same account
    # defaults/catalogue; no private page payload is prefetched on hover.
    assert len(state["reads"]) == len(PATHS) + 3
    assert not state["errors"]


def test_navigation_reads_fresh_data_and_supports_back_forward(ui_page):
    page, _ = ui_page
    state = navigation_app(page)
    page.goto("http://ui.test/dashboard", wait_until="networkidle")
    page.evaluate("window.navMarker='same-document'")
    open_navigation(page, "/ai-config")
    page.locator("#field-openai_api_key").fill("discarded-on-page-exit")
    open_navigation(page, "/projects")
    state["overrides"]["/ai-config"] = dict(
        current_config=dict(openai_api_key="fresh-server-value")
    )
    page.go_back()
    expect(page.get_by_role("heading", name="系统配置管理", exact=True)).to_be_visible()
    expect(page.locator("#field-openai_api_key")).to_have_value("fresh-server-value")
    page.go_forward()
    expect(page.get_by_role("heading", name="项目列表", exact=True)).to_be_visible()
    assert page.evaluate("window.navMarker") == "same-document"
    assert sum(item["path"] == "/ai-config" for item in state["reads"]) == 2
    assert not state["errors"]


def test_navigation_last_click_wins_and_current_link_cancels_pending(ui_page):
    page, _ = ui_page
    state = navigation_app(page)
    page.goto("http://ui.test/home", wait_until="networkidle")
    page.evaluate(
        """() => {
      const original=fetch;window.pendingPages=[];
      window.fetch=(...args)=>new URL(args[0],location.href).pathname==='/projects'
        ? new Promise(resolve=>window.pendingPages.push(resolve)) : original(...args);
    }"""
    )
    page.locator('.app-sidebar a[href="/projects"]').click()
    expect(page.locator("#appMain")).to_have_attribute("aria-busy", "true")
    open_navigation(page, "/global-master-templates")
    page.evaluate(
        "html => window.pendingPages[0](new Response(html))",
        state["render"]("/projects"),
    )
    expect(page.get_by_role("heading", name="模板管理", exact=True)).to_be_visible()
    assert urlsplit(page.url).path == "/global-master-templates"
    page.locator('.app-sidebar a[href="/projects"]').click()
    page.locator('.app-sidebar a[href="/global-master-templates"]').click()
    page.evaluate(
        "html => window.pendingPages[1](new Response(html))",
        state["render"]("/projects"),
    )
    expect(page.locator("#appMain")).to_have_attribute("aria-busy", "false")
    assert urlsplit(page.url).path == "/global-master-templates"
    assert not state["errors"]


@pytest.mark.parametrize("reason", ["auth", "identity", "assets", "invalid", "error"])
def test_navigation_uses_native_server_response_when_required(ui_page, reason):
    page, _ = ui_page
    state = navigation_app(page)
    page.goto("http://ui.test/dashboard", wait_until="networkidle")
    page.evaluate("window.navMarker='old-document'")
    if reason == "auth":
        # Route interception only handles the first URL of a redirect chain.
        # Model fetch's final response; the native login request uses the real handler.
        page.evaluate(
            """() => {
          const original=fetch;
          window.fetch=(...args)=>{
            if(new URL(args[0],location.href).pathname!=='/projects') return original(...args);
            const response=new Response('<h1>登录</h1>');
            Object.defineProperty(response,'url',{value:location.origin+'/auth/login'});
            return Promise.resolve(response);
          };
        }"""
        )
    elif reason == "identity":
        state["overrides"]["/projects"] = dict(
            user=dict(username="another-user", is_admin=False)
        )
    elif reason == "assets":
        state["handlers"]["/projects"] = lambda route: route.fulfill(
            body=state["render"]("/projects").replace(
                "/static/antd/app.css?v=", "/static/antd/app.css?v=changed-release-"
            ),
            content_type="text/html; charset=utf-8",
        )
    elif reason == "invalid":
        state["handlers"]["/projects"] = lambda route: route.fulfill(
            body="<h1>服务器页面</h1>", content_type="text/html; charset=utf-8"
        )
    else:
        state["handlers"]["/projects"] = lambda route: route.fulfill(
            status=500,
            body="<h1>服务器错误</h1>",
            content_type="text/html; charset=utf-8",
        )
    page.locator('.app-sidebar a[href="/projects"]').click()
    expected = (
        "登录"
        if reason == "auth"
        else (
            "服务器页面"
            if reason == "invalid"
            else "服务器错误" if reason == "error" else "项目列表"
        )
    )
    expect(page.get_by_role("heading", name=expected, exact=True)).to_be_visible()
    assert page.evaluate("window.navMarker") is None
    assert sum(item["kind"] == "document" for item in state["reads"]) >= 2


def test_navigation_does_not_execute_page_scripts_or_prefetch_data(ui_page):
    page, _ = ui_page
    state = navigation_app(page)
    state["handlers"]["/projects"] = lambda route: route.fulfill(
        body=state["render"]("/projects")
        + "<script>window.extraScriptRan=true</script>",
        content_type="text/html; charset=utf-8",
    )
    page.goto("http://ui.test/dashboard", wait_until="networkidle")
    reads_before_hover = list(state["reads"])
    page.locator('.app-sidebar a[href="/projects"]').hover()
    page.wait_for_timeout(1200)
    assert state["reads"] == reads_before_hover
    assert [read["path"] for read in state["reads"]] == ["/dashboard", "/create"]
    open_navigation(page, "/projects")
    assert page.evaluate("window.extraScriptRan") is None
    assert not state["errors"]


def test_navigation_keeps_notification_poller_and_same_page_link_stable(ui_page):
    page, _ = ui_page
    state = navigation_app(page)
    unread = []

    def count(route):
        unread.append(route.request.url)
        route.fulfill(json=dict(unread_count=0))

    page.route("**/notifications/unread-count", count)
    page.goto("http://ui.test/dashboard", wait_until="networkidle")
    page.evaluate("window.navMarker='same-document'")
    initial_reads = list(state["reads"])
    page.locator('.app-sidebar a[href="/dashboard"]').click()
    assert state["reads"] == initial_reads
    for path in ["/projects", "/create", "/ai-config", "/dashboard"]:
        open_navigation(page, path)
    assert len(unread) == 1
    assert page.evaluate("window.navMarker") == "same-document"
    assert not state["errors"]


def test_navigation_keeps_modified_click_and_new_tab_native(ui_page):
    page, _ = ui_page
    navigation_app(page)
    page.goto("http://ui.test/dashboard", wait_until="networkidle")
    with page.context.expect_page() as opening:
        page.locator('.app-sidebar a[href="/projects"]').click(
            modifiers=["Control"]
        )
    popup = opening.value
    popup.wait_for_url("**/projects")
    popup.wait_for_load_state()
    assert urlsplit(popup.url).path == "/projects"
    assert urlsplit(page.url).path == "/dashboard"
    popup.close()


def test_credits_link_is_not_highlighted_outside_its_page(ui_page):
    page, load = ui_page
    load("pages/project/project_dashboard.html", path="/dashboard", credits_enabled=True)
    credits = page.locator(".app-sidebar .sidebar-credits")
    expect(credits).to_be_visible()
    expect(credits).not_to_have_attribute("aria-current", "page")
    assert credits.evaluate("e => getComputedStyle(e).backgroundColor") == "rgba(0, 0, 0, 0)"
