import { createContext, memo, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { App, Button, Drawer, Dropdown, Input, Space, Tooltip } from 'antd';
import { BellOutlined, MenuOutlined, UserOutlined, SettingOutlined, LogoutOutlined, GithubOutlined, MailOutlined, HeartOutlined, SearchOutlined, HomeOutlined, FolderOutlined, PictureOutlined, LayoutOutlined, MenuFoldOutlined, MenuUnfoldOutlined, FileTextOutlined } from '@ant-design/icons';
import { AppearanceToggle } from './components/appearance';
import { BrandSymbol } from './components/brand-symbol';
import { version } from '../package.json';
import { modalFocus } from './components/modal-focus';
import { NotificationDrawer, SiteAnnouncement } from './components/shell-feedback';
import type { ApiData } from './api';

export interface ShellData {
  page: string;
  user: { username: string; is_admin?: boolean } | null;
  creditsEnabled: boolean;
}

declare global {
  interface Window {
    NotificationCenter?: { mount(options: Record<string, unknown>): any };
  }
}

export const NavigationPending = createContext('');

function NavigationProgress() {
  const pending = useContext(NavigationPending);
  return pending ? <div className="navigation-progress" role="status" aria-label="正在打开页面" /> : null;
}

function ShellMain({ className, children }: { className: string; children: ReactNode }) {
  const pending = useContext(NavigationPending);
  return <main id="appMain" tabIndex={-1} aria-busy={!!pending} className={className}>{children}</main>;
}

// Pending navigation updates only the progress line and main accessibility state.
// Keep the sidebar, account menus and notification surfaces out of that render.
export const Shell = memo(function Shell({ data, children }: { data: ShellData; children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => {
    try { const saved = localStorage.getItem('landppt:sidebar-collapsed'); if (saved !== null) return saved === '1'; } catch { /* Use the viewport default when storage is unavailable. */ }
    return window.matchMedia('(min-width: 1024px) and (max-width: 1399px)').matches;
  });
  const [search, setSearch] = useState('');
  const [recent, setRecent] = useState<ApiData[]>([]);
  const authPage = data.page === 'auth';
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const { modal, message } = App.useApp();
  const [notice, setNotice] = useState<ApiData>();
  const [sponsorsEnabled, setSponsorsEnabled] = useState(false);
  const [notifications, setNotifications] = useState<ApiData>({});
  const [notificationOpen, setNotificationOpen] = useState(false);
  const center = useRef<any>(null);
  useEffect(() => { try { localStorage.setItem('landppt:sidebar-collapsed', collapsed ? '1' : '0'); } catch { /* Storage may be unavailable in private browsing. */ } }, [collapsed]);
  useEffect(() => {
    if (!data.user || data.page === 'home' || data.page === 'auth') { setRecent([]); return; }
    const controller = new AbortController();
    void fetch('/api/projects?page=1&page_size=8', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
      .then(response => response.ok ? response.json() : null)
      .then(value => { if (!controller.signal.aborted) setRecent(Array.isArray(value?.projects) ? value.projects.slice(0, 8) : []); })
      .catch(() => { /* The navigation remains usable when the project list is unavailable. */ });
    return () => controller.abort();
  }, [data]);
  useEffect(() => {
    let disposed = false;
    let reading: AbortController | undefined;
    async function refresh() {
      reading?.abort();
      const controller = new AbortController(); reading = controller;
      try {
        const response = await fetch('/api/community/public-settings', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
        if (!response.ok) return;
        const settings = await response.json();
        if (!disposed && !controller.signal.aborted) {
          setNotice(settings.site_notice);
          setSponsorsEnabled(settings.sponsor_page_enabled === true);
        }
      } catch { /* Retain the last known settings on transient failures. */ }
    }
    const visible = () => { if (document.visibilityState === 'visible') void refresh(); };
    void refresh();
    window.addEventListener('landppt:community-settings-saved', refresh);
    document.addEventListener('visibilitychange', visible);
    return () => {
      disposed = true; reading?.abort();
      window.removeEventListener('landppt:community-settings-saved', refresh);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [data]);
  useEffect(() => {
    let disposed = false;
    if (data.user) {
      center.current = window.NotificationCenter?.mount({ bell: 'navNotificationBell', badge: 'navNotificationBadge', renderUI: (value: ApiData) => { if (!disposed) setNotifications(value); }, onOpenChange: (open: boolean) => { if (!disposed) setNotificationOpen(open); }, notifyUI: (type: 'error' | 'success' | 'warning', content: string) => { if (!disposed) message[type](content); } });
      if (center.current?.panel) { center.current.panel.classList.add('app-notification-host'); center.current.panel.setAttribute('role', 'presentation'); }
    }
    return () => { disposed = true; center.current?.dispose(); center.current = null; };
  }, [data.user?.username]);
  useEffect(() => { setMenuOpen(false); }, [data.page]);
  const links = [
    ['首页', '/dashboard', <HomeOutlined aria-hidden />], ['项目', '/projects', <FolderOutlined aria-hidden />], ['模板', '/global-master-templates', <LayoutOutlined aria-hidden />], ['素材', '/image-gallery', <PictureOutlined aria-hidden />],
  ];
  const current = (href: string) => location.pathname === href || (href === '/projects' && location.pathname.startsWith('/projects/'));
  const logout = () => modal.confirm({ title: '退出登录', content: '确定要退出登录吗？', okText: '退出', cancelText: '取消', modalRender: modalFocus, onOk: () => { location.href = '/auth/logout'; } });
  const navigation = <nav aria-label="主导航" className="app-nav">{links.map(([label, href, icon]) => <Tooltip key={String(href)} title={collapsed && !menuOpen ? label : undefined} placement="right"><a href={String(href)} aria-label={String(label)} aria-current={current(String(href)) ? 'page' : undefined}>{icon}<span className="app-nav-label">{label}</span></a></Tooltip>)}</nav>;
  const searchForm = <form className="sidebar-search" action="/projects" role="search"><Input aria-label="搜索项目" name="search" placeholder="搜索项目…" prefix={<SearchOutlined aria-hidden />} value={search} onChange={event => setSearch(event.target.value)} /><button className="sr-only" type="submit">搜索</button></form>;
  const recentProjects = <section className="sidebar-recent" aria-label="最近项目"><div className="sidebar-section-label">最近项目</div>{recent.length ? recent.map(project => <a key={project.project_id} href={`/projects/${encodeURIComponent(project.project_id)}`} title={project.title} aria-current={location.pathname.startsWith(`/projects/${encodeURIComponent(project.project_id)}`) ? 'page' : undefined}><FileTextOutlined aria-hidden /><span>{project.title || '未命名项目'}</span></a>) : <p className="subtle">项目会显示在这里</p>}</section>;
  const account = data.user ? [
    { key: 'profile', label: <a href="/auth/profile">个人资料</a>, icon: <UserOutlined aria-hidden /> },
    ...(data.creditsEnabled ? [{ key: 'credits', label: <a href="/credits">我的积分</a> }] : []),
    ...(data.user.is_admin ? [{ key: 'admin', label: <a href="/admin">管理后台</a>, icon: <SettingOutlined aria-hidden /> }] : []),
    { key: 'logout', label: '退出登录', icon: <LogoutOutlined aria-hidden />, onClick: logout },
  ] : [];
  return <div className={`app-shell${data.page === 'home' ? ' home-shell' : ''}${authPage ? ' auth-shell' : ''}${collapsed ? ' sidebar-collapsed' : ''}${['slides-editor', 'generator-workspace', 'project-detail', 'todo'].includes(data.page) ? ' workspace-shell' : ''}`}>
    <a className="skip-link" href="#appMain">跳到主内容</a>
    {!authPage && <aside className="app-sidebar" aria-label="应用侧栏">
      <div className="sidebar-brand-row"><a className="app-brand" href="/home" aria-label="立铠 PPT 首页"><BrandSymbol /><span className="sidebar-wordmark">立铠 PPT</span></a><Button type="text" aria-label={collapsed ? '展开导航' : '收起导航'} aria-expanded={!collapsed} icon={collapsed ? <MenuUnfoldOutlined aria-hidden /> : <MenuFoldOutlined aria-hidden />} onClick={() => setCollapsed(value => !value)} /></div>
      {collapsed ? <Tooltip title="搜索项目" placement="right"><Button type="text" aria-label="搜索项目" href="/projects" icon={<SearchOutlined aria-hidden />} /></Tooltip> : searchForm}
      {navigation}{data.page !== 'home' && recentProjects}
      <div className="sidebar-bottom">{data.creditsEnabled && <a className="sidebar-credits" href="/credits" aria-current={location.pathname === '/credits' ? 'page' : undefined}><span aria-hidden>◈</span><span className="app-nav-label">我的积分</span></a>}<a href="/ai-config" aria-label="系统配置"><SettingOutlined aria-hidden /><span className="app-nav-label">系统配置</span></a>{(sponsorsEnabled || data.user?.is_admin) && <a id="navSponsorLink" href="/sponsors"><HeartOutlined aria-hidden /><span className="app-nav-label">{sponsorsEnabled ? '赞助感谢' : '赞助预览'}</span></a>}</div>
    </aside>}
    <div className="app-body"><header className="app-header">
      {!authPage && <Button ref={menuTrigger} className="mobile-menu" type="text" aria-label="菜单" aria-expanded={menuOpen} icon={<MenuOutlined aria-hidden />} onClick={() => setMenuOpen(true)} />}
      <a className="app-brand mobile-brand" href="/home">立铠 PPT</a>
      {authPage ? <Space size={8} className="app-account"><AppearanceToggle /><Button href="/home">返回首页</Button></Space> : <Space size={8} className="app-account"><AppearanceToggle className="app-appearance" />
        <Tooltip title="系统配置" trigger={['hover', 'focus']}><Button className="app-settings" type="text" href="/ai-config" aria-label="系统配置" icon={<SettingOutlined aria-hidden />} /></Tooltip>
        {data.user ? <><Button type="text" id="navNotificationBell" aria-label="通知" icon={<BellOutlined aria-hidden />}><span id="navNotificationBadge" hidden>0</span></Button>
          <Dropdown classNames={{ root: 'app-account-menu' }} menu={{ items: account }} trigger={['click']}><Button type="text" icon={<UserOutlined aria-hidden />} aria-label="账户菜单"><span className="account-name">{data.user.username}</span></Button></Dropdown></>
          : <Button href="/auth/login">登录</Button>}
      </Space>}
    </header>
    <NavigationProgress />
    <Drawer title="导航" open={menuOpen} onClose={() => setMenuOpen(false)} afterOpenChange={open => { if (!open) menuTrigger.current?.focus(); }} size={280}>
      {searchForm}{navigation}{data.page !== 'home' && recentProjects}<nav aria-label="辅助导航" className="app-nav account-nav"><a href="/dashboard">创建 PPT</a><a href="/home">产品首页</a><a href="/ai-config">系统配置</a>{(sponsorsEnabled || data.user?.is_admin) && <a id="mobileSponsorLink" href="/sponsors">{sponsorsEnabled ? '赞助感谢' : '赞助预览'}</a>}</nav><nav aria-label="账户导航" className="app-nav account-nav">
        {data.user ? <><a href="/auth/profile">个人资料</a>{data.creditsEnabled && <a href="/credits">我的积分</a>}{data.user.is_admin && <a href="/admin">管理后台</a>}<Button type="text" onClick={logout}>退出登录</Button></>
          : <><a href="/auth/login">登录</a><a href="/auth/login?tab=register">注册</a></>}
      </nav>
    </Drawer>
    <SiteAnnouncement notice={notice} />
    <NotificationDrawer open={notificationOpen} state={notifications} center={center.current} />
    <ShellMain className={`app-main${authPage ? ' auth-main' : ''}${data.page === 'home' ? ' home-main' : ''}`}>{children}</ShellMain>
    {data.page === 'home' && <footer className="app-footer"><span>立铠 PPT · v{version}</span><a href="/home">产品首页</a>{(sponsorsEnabled || data.user?.is_admin) && <a id="footerSponsorLink" className="app-footer-sponsor" href="/sponsors"><HeartOutlined aria-hidden />{sponsorsEnabled ? '赞助感谢' : '赞助预览'}</a>}</footer>}
  </div></div>;
});
