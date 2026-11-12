import { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Alert, App, Button, Drawer, Dropdown, Grid, Modal, Segmented, Space, Splitter, Tag, Tooltip } from 'antd';
import { ArrowLeftOutlined, AudioOutlined, BarsOutlined, CodeOutlined, ColumnHeightOutlined, DoubleRightOutlined, EditOutlined, EllipsisOutlined, EyeOutlined, FolderOpenOutlined, LayoutOutlined, PictureOutlined, PlayCircleOutlined, ReloadOutlined, RobotOutlined, SaveOutlined, SplitCellsOutlined, UnorderedListOutlined } from '@ant-design/icons';
import type { ApiData } from '../api';
import { useNativeFeedback } from '../components/native-feedback';
import { modalFocus } from '../components/modal-focus';
import { NativeControls, keepNativeSubmitForms, nativeSelectIsOpen } from '../components/native-controls';
import { editorTaskNotice, EditorTaskPanel } from '../components/editor-task-progress';
import { EditorThumbnailMenu, type EditorContextMenu } from '../components/editor-thumbnail-menu';
import { EditorExportDialog, type EditorExportProgress } from '../components/editor-export-dialog';
import { EditorExportChooser } from '../components/editor-export-chooser';
import { EditorShareDialog, type EditorShareLinks } from '../components/editor-share-dialog';
import { EditorQuickIcons } from '../components/editor-quick-icons';
import { EditorDocumentSidebar } from '../components/editor-document-sidebar';
import { createEditorController } from '../controllers/editor-controller';
import { ProjectAgent } from '../components/project-agent';
import { ContinuousPreview } from '../components/continuous-preview';
import { EditorStartup } from '../components/editor-startup';
import { ExplainerEditorTools } from '../components/explainer-video-editor';
import '../components/native-surfaces.css';
import './slides-editor.css';

type DialogRecord = { key: number; node: HTMLElement; type: string[]; title: string; closable: boolean; origin: HTMLElement | null; footer?: HTMLElement | null; exportProgress?: EditorExportProgress; shareLinks?: EditorShareLinks };
type EditorState = ApiData & { dialogs: DialogRecord[]; toasts: ApiData[]; contextMenu: EditorContextMenu | null };
type Controller = ReturnType<typeof createEditorController>;
type WorkspaceView = 'overview' | 'planning' | 'generate' | 'slides' | 'sources' | 'exports';
const ProjectDetail = lazy(() => import('./project-detail'));
const Todo = lazy(() => import('./todo'));
const Generator = lazy(() => import('./generator-chrome'));
const SourceLibrary = lazy(() => import('../components/source-library').then(module => ({ default: module.SourceLibrary })));
const ArtifactLibrary = lazy(() => import('../components/artifact-library').then(module => ({ default: module.ArtifactLibrary })));

// Keep the exact native form nodes/listeners; Ant owns their bounded frame.
function NativeNodes({ nodes, className = 'editor-adapted-content' }: { nodes: Node[]; className?: string }) {
  const target = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const origins = nodes.map(node => ({ node, parent: node.parentNode, next: node.nextSibling }));
    keepNativeSubmitForms(nodes);
    nodes.forEach(node => target.current?.appendChild(node));
    return () => origins.forEach(({ node, parent, next }) => { if (parent && node.isConnected) parent.insertBefore(node, next?.parentNode === parent ? next : null); });
  }, [nodes]);
  return <div className={className} ref={target} />;
}

function EditorDialog({ record, index, tasks, controller }: { record: DialogRecord; index: number; tasks: ApiData[]; controller: Controller }) {
  const nativeDialog = record.node.tagName === 'DIALOG';
  const canvas = record.node.id === 'svg-page-editor';
  const [parts] = useState(() => ({ content: nativeDialog ? [record.node] : Array.from(record.node.childNodes), footer: !canvas && record.type[3] ? record.node.querySelector<HTMLElement>(record.type[3]) : null }));
  const footer = record.footer || parts.footer;
  const footerNodes = useMemo<Node[]>(() => footer ? [footer] : [], [footer]);
  const controlRoots = useMemo<HTMLElement[]>(() => footer ? [footer] : [], [footer]);
  useLayoutEffect(() => {
    const node = record.node, oldRole = node.getAttribute('role'), oldModal = node.getAttribute('aria-modal');
    node.classList.add(nativeDialog ? 'editor-ant-native-dialog' : 'editor-ant-modal-host');
    node.setAttribute('role', 'presentation'); node.removeAttribute('aria-modal');
    return () => {
      node.classList.remove('editor-ant-native-dialog', 'editor-ant-modal-host');
      if (oldRole) node.setAttribute('role', oldRole); else node.removeAttribute('role');
      if (oldModal) node.setAttribute('aria-modal', oldModal);
      const origin = record.origin;
      queueMicrotask(() => { if (!document.querySelector('.editor-ant-dialog') && origin?.isConnected && origin.getClientRects().length) origin.focus(); });
    };
  }, [record.node, nativeDialog]);
  return <Modal open aria-label={record.title} title={canvas ? null : record.title} footer={footer ? <NativeNodes nodes={footerNodes} /> : null}
    width={Number(record.type[4])} zIndex={2000 + index * 20} className={`editor-ant-dialog${canvas ? ' editor-ant-canvas' : ''}`}
    getContainer={nativeDialog ? undefined : () => record.node} closable={!canvas && record.closable} keyboard={!canvas && record.closable}
    mask={{ closable: !canvas && record.closable }} onCancel={() => controller.close(record)} modalRender={node => <div onKeyDownCapture={event => { if (event.key === 'Escape' && !canvas && !nativeSelectIsOpen(event.target)) { event.preventDefault(); event.stopPropagation(); if (record.closable) controller.close(record); } }}>{modalFocus(node)}</div>}>
    <EditorTaskPanel tasks={tasks} />
    <NativeNodes nodes={parts.content} />
    {!canvas && <NativeControls root={record.node} extraRoots={controlRoots} />}
  </Modal>;
}

function NativeAssistant() {
  const [node] = useState(() => document.getElementById('aiEditSidebar')!);
  const nodes = useRef<Node[]>([node]);
  return <><NativeNodes nodes={nodes.current} /><NativeControls root={node} /></>;
}

export default function SlidesEditor({ data }: { data: ApiData }) {
  useNativeFeedback();
  const { notification } = App.useApp(); const screens = Grid.useBreakpoint();
  const controller = useMemo(() => createEditorController((window as any).LandPPTEditorEngine, (window as any).LandPPTEditorSurfaces), []);
  const [state, setState] = useState<EditorState>(() => controller.getSnapshot() as EditorState);
  const saving = !!state.savePending || !!state.saving;
  const textPanel = state.documentPanel === 'outline' || state.documentPanel === 'speech';
  const previousToasts = useRef(new Map<string, string>()); const moreRef = useRef<HTMLButtonElement>(null);
  const taskDialog = [...state.dialogs].reverse().find(record => record.node.querySelector('.speech-modal-body-preview'));
  const [workspace] = useState<ApiData | null>(() => { const node = document.getElementById('workspaceData'); return node ? JSON.parse(node.textContent || '{}') : null; });
  const [view, setView] = useState<WorkspaceView>(() => {
    const query = new URLSearchParams(location.search).get('workspace');
    return ['overview', 'planning', 'generate', 'slides', 'sources', 'exports'].includes(query || '') ? query as WorkspaceView : data.workspaceView || 'slides';
  });
  const [visited, setVisited] = useState<Set<WorkspaceView>>(() => new Set([view]));
  const [continuous, setContinuous] = useState(!!workspace);
  const [railSizes, setRailSizes] = useState([340, 240]);
  const nativeContainer = useMemo(() => [document.querySelector('.editor-container')!], []);
  const nativeSidebar = useMemo(() => [document.getElementById('slidesSidebar')!], []);
  const nativeEditor = useMemo(() => [document.querySelector('.main-editor')!], []);
  const [ready, setReady] = useState(false);
  const desktop = (screens.lg ?? window.innerWidth >= 1024) && window.innerWidth >= 1024;
  const editing = view === 'slides';
  const assistantVisible = desktop && editing && state.aiOpen;
  const wasDesktop = useRef(desktop);
  // The desktop rail is open by default; on shrinking to a touch layout it would reappear as a full-screen drawer.
  useEffect(() => {
    if (wasDesktop.current && !desktop && state.aiOpen) controller.call('closeAIEditSidebar');
    wasDesktop.current = desktop;
  }, [desktop, state.aiOpen, controller]);
  const openView = (next: WorkspaceView) => {
    setVisited(previous => new Set([...previous, next]));
    setView(next);
    const url = new URL(location.href); url.searchParams.set('workspace', next);
    history.replaceState(history.state, '', url);
  };
  useEffect(() => {
    const node = nativeSidebar[0];
    const select = (event: Event) => { if ((event.target as HTMLElement).closest('.slide-thumbnail')) { setView('slides'); setContinuous(false); } };
    node.addEventListener('click', select);
    return () => node.removeEventListener('click', select);
  }, [nativeSidebar]);
  useEffect(() => {
    if (!workspace) return;
    const base = `/projects/${encodeURIComponent(String(data.projectId))}`;
    const route = (href: string) => {
      const url = new URL(href, location.href);
      if (url.origin !== location.origin) return false;
      if (url.pathname === base) openView('overview');
      else if (url.pathname === `${base}/todo`) openView(url.searchParams.get('view') === 'planning' ? 'planning' : 'generate');
      else if (url.pathname === `${base}/edit`) openView('slides');
      else if (url.pathname === '/image-gallery' && url.searchParams.get('project') === String(data.projectId) && ['sources', 'exports'].includes(url.searchParams.get('tab') || '')) openView(url.searchParams.get('tab') as WorkspaceView);
      else return false;
      return true;
    };
    const click = (event: MouseEvent) => {
      const link = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[href]');
      if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || !link || link.target || link.hasAttribute('download')) return;
      if (route(link.href)) event.preventDefault();
    };
    const navigate = (event: Event) => { if (route((event as CustomEvent<string>).detail)) event.preventDefault(); };
    document.addEventListener('click', click); document.addEventListener('landppt:navigate', navigate);
    return () => { document.removeEventListener('click', click); document.removeEventListener('landppt:navigate', navigate); };
  }, [workspace, data.projectId]);
  useLayoutEffect(() => {
    const node = nativeEditor[0] as HTMLElement;
    node.dataset.workspaceView = view; node.dataset.continuous = String(continuous && view === 'slides');
    requestAnimationFrame(() => controller.call('applyMainFrameScale'));
  }, [view, continuous, controller, nativeEditor]);
  useLayoutEffect(() => {
    // Stage speech shells before the observer/React handoff can paint legacy UI.
    document.body.classList.add('editor-ant-dialogs-ready');
    return () => { document.body.classList.remove('editor-ant-dialogs-ready'); };
  }, []);
  useEffect(() => {
    const unsubscribe = controller.subscribe(value => setState(value as EditorState));
    const hide = () => controller.suspend();
    const show = (event: PageTransitionEvent) => { if (event.persisted) controller.resume(); };
    controller.mount();
    window.addEventListener('pagehide', hide); window.addEventListener('pageshow', show);
    return () => { unsubscribe(); controller.dispose(); window.removeEventListener('pagehide', hide); window.removeEventListener('pageshow', show); };
  }, [controller]);
  // Enter editing with the desktop rail expanded, while allowing manual collapse
  // until the next view change. Keep its native form mounted to retain chat drafts.
  useEffect(() => {
    if (!editing) controller.call('closeAIEditSidebar');
    else if (desktop) controller.call('openAIEditSidebar', { focus: false });
  }, [editing, desktop, controller]);
  useEffect(() => {
    // NativeNodes has adopted the original nodes. Reveal only after the splitter
    // has a measurable preview pane; do not wait for remote artwork resources.
    let frame = 0;
    const reveal = () => {
      const pane = nativeEditor[0].parentElement;
      if (!pane?.clientWidth || !pane.clientHeight) { frame = requestAnimationFrame(reveal); return; }
      controller.call('applyMainFrameScale');
      controller.call('applyThumbnailPreviewScaleToAll');
      nativeContainer[0].removeAttribute('data-editor-staged');
      setReady(true);
    };
    frame = requestAnimationFrame(reveal);
    return () => { cancelAnimationFrame(frame); nativeContainer[0].setAttribute('data-editor-staged', ''); };
  }, [controller, nativeContainer, nativeEditor]);
  useLayoutEffect(() => {
    const resize = () => {
      const quickToolbar = document.getElementById('quickEditToolbar');
      const tools = document.getElementById('editorAntToolbar');
      const pane = document.querySelector('.editor-content');
      if (quickToolbar && tools && pane) {
        const width = quickToolbar.getBoundingClientRect().width, rect = pane.getBoundingClientRect();
        const center = Math.max(8 + width / 2, Math.min(rect.left + rect.width / 2, window.innerWidth - 8 - width / 2));
        quickToolbar.style.setProperty('--quick-toolbar-center', `${center}px`);
        quickToolbar.style.setProperty('--quick-toolbar-top', `${Math.max(8, Math.min(tools.getBoundingClientRect().bottom + 8, window.innerHeight - quickToolbar.offsetHeight - 8))}px`);
        (pane as HTMLElement).style.setProperty('--editor-quick-toolbar-height', `${quickToolbar.offsetHeight}px`);
      }
      const wrapper = document.getElementById('slideFrameWrapper');
      if (wrapper && wrapper.clientWidth > 20 && wrapper.clientHeight > 20) controller.call('applyMainFrameScale');
      controller.call('applyThumbnailPreviewScaleToAll'); controller.call('resizeCodeMirror');
    };
    const observer = new ResizeObserver(resize);
    const pane = document.querySelector('.editor-content'); if (pane) observer.observe(pane);
    const sidebar = document.getElementById('slidesSidebar'); if (sidebar) observer.observe(sidebar);
    const wrapper = document.getElementById('slideFrameWrapper'); if (wrapper) observer.observe(wrapper);
    const quickToolbar = document.getElementById('quickEditToolbar'); if (quickToolbar) observer.observe(quickToolbar);
    const frame = requestAnimationFrame(resize);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, []);
  useEffect(() => {
    const keys = new Map<string, string>();
    (taskDialog ? [] : state.toasts).forEach(toast => {
      const signature = JSON.stringify(toast); keys.set(toast.key, signature);
      if (previousToasts.current.get(toast.key) !== signature) notification.open({ key: toast.key, ...editorTaskNotice(toast), duration: 0, closable: false, placement: 'bottomRight' });
    });
    previousToasts.current.forEach((_, key) => { if (!keys.has(key)) notification.destroy(key); }); previousToasts.current = keys;
  }, [state.toasts, taskDialog?.key, notification]);
  useEffect(() => () => { previousToasts.current.forEach((_, key) => notification.destroy(key)); }, [notification]);
  const save = () => { void controller.save(); };
  const [explainerOpen, setExplainerOpen] = useState(false);
  const call = (name: string, ...args: unknown[]) => controller.call(name, ...args);
  const toolbar = <div className="editor-ant-toolbar" role="toolbar" aria-label="幻灯片编辑工具">
    <div className="editor-ant-view"><Segmented aria-label="编辑视图" value={state.mode === 'quickedit' ? 'preview' : state.mode} options={[
      { value: 'preview', label: '预览', title: '预览', icon: <EyeOutlined aria-hidden /> }, { value: 'edit', label: '代码', title: '代码', icon: <CodeOutlined aria-hidden /> }, { value: 'split', label: '分屏', title: '分屏', icon: <SplitCellsOutlined aria-hidden /> },
    ]} onChange={mode => { setContinuous(false); call('setMode', mode); }} /><Button aria-pressed={continuous} title="连续预览" icon={<ColumnHeightOutlined aria-hidden />} onClick={() => { setContinuous(value => !value); call('setMode', 'preview'); }}>连续预览</Button><Button disabled={!state.count} type={state.mode === 'quickedit' ? 'primary' : 'default'} title={state.svg ? '编辑 SVG' : '快速编辑'} icon={<EditOutlined aria-hidden />} onClick={() => { setContinuous(false); call('toggleQuickEditMode'); }}>{state.svg ? '编辑 SVG' : '快速编辑'}</Button>{state.packageContent && <Button onClick={() => controller.call('openPackageEditor')}>内容与版式</Button>}</div>
    <Space wrap size={8} className="editor-ant-actions"><Button disabled={!state.count} title="放映" icon={<PlayCircleOutlined aria-hidden />} onClick={() => controller.call('startSlideshow')}>放映</Button><Button type="primary" disabled={!state.count || textPanel && !state.documentDirty} loading={saving || state.documentSaving} icon={<SaveOutlined aria-hidden />} onClick={save}>{state.documentPanel === 'outline' ? '保存大纲' : state.documentPanel === 'speech' ? '保存讲稿' : '保存'}</Button><Button disabled={!state.count} title="AI 编辑" icon={<RobotOutlined aria-hidden />} onClick={() => controller.call('openAIEditSidebar')}>AI 编辑</Button>
      <Dropdown trigger={['click']} menu={{ items: [
        { key: 'current', label: '重新生成当前页', onClick: () => call('quickRegenerateSlide') }, { key: 'selected', label: `重新生成所选页（${state.selected}）`, onClick: () => call('regenerateSelectedSlides') }, { key: 'all', label: '重新生成全部', danger: true, onClick: () => call('regenerateAllSlides') },
      ] }}><Button disabled={!state.count} title="重新生成" icon={<ReloadOutlined aria-hidden />}>重新生成</Button></Dropdown>
      <EditorExportChooser projectTitle={data.title} count={state.count} client={state.clientExport} native={state.nativeExport} vector={state.vectorExport} standard={!!data.standardExport} saving={saving || state.documentSaving} run={call} />
      <Dropdown trigger={['click']} onOpenChange={open => { if (!open) queueMicrotask(() => moreRef.current?.focus()); }} menu={{ items: [
        { key: 'template', label: '更换模板', onClick: () => call('showChangeTemplateDialog') }, { key: 'share', label: '分享链接', onClick: () => call('showShareDialog') }, { key: 'speech', label: '生成演讲稿', onClick: () => call('showSpeechScriptDialog') }, { key: 'explainer', label: '🎬 生成本页讲解视频', disabled: !state.count || !!state.currentVideoId, onClick: () => setExplainerOpen(true) }, { key: 'chat', label: '自由对话（当前页）', disabled: state.svg, onClick: () => call('openNativeChatDialog') },
      ] }}><Button ref={moreRef} aria-label="更多工具" icon={<EllipsisOutlined aria-hidden />} /></Dropdown>
    </Space>
  </div>;
  return <>
    <div className="editor-mount" aria-busy={!ready}>
      {!ready && <EditorStartup />}
      <NativeNodes nodes={nativeContainer} className="workspace-native-root" />
    </div>
    {createPortal(<Splitter className={`project-splitter${desktop ? ' is-desktop' : ''}`} onResize={sizes => setRailSizes([sizes[2], sizes[0]])} onCollapse={(collapsed, sizes) => { setRailSizes([sizes[2], sizes[0]]); if (editing && collapsed[2] !== !state.aiOpen) call(collapsed[2] ? 'closeAIEditSidebar' : 'openAIEditSidebar'); if (collapsed[0] !== !state.sidebar) call('toggleSidebar'); }}>
      <Splitter.Panel size={desktop && state.sidebar ? railSizes[1] || 240 : 0} min={desktop && state.sidebar ? 200 : 0} max="38%" resizable={desktop} collapsible>
        <NativeNodes nodes={nativeSidebar} className="workspace-files-host" />
      </Splitter.Panel>
      <Splitter.Panel min={desktop ? 250 : 0} collapsible><NativeNodes nodes={nativeEditor} className="workspace-preview-host" /></Splitter.Panel>
      <Splitter.Panel className={`workspace-assistant-panel${editing ? '' : ' is-inactive'}`} size={assistantVisible ? railSizes[0] || 340 : 0} min={assistantVisible ? 280 : 0} max="45%" resizable={desktop && editing} collapsible={editing}>
        {desktop && <section className="workspace-assistant editor-ant-assistant" aria-label="AI 编辑助手" data-empty={!state.count}><header><div><RobotOutlined aria-hidden /><strong>AI 创作助手</strong>{state.count > 0 && <Tag>第 {state.current + 1} 页</Tag>}{workspace && state.count > 0 && <span className="workspace-assistant-meta">{state.count} 页 · v{workspace.detail.project.version}</span>}</div><Tooltip title="收起"><Button type="text" size="small" aria-label="收起 AI 助手" icon={<DoubleRightOutlined aria-hidden />} onClick={() => call('closeAIEditSidebar')} /></Tooltip></header><div className="workspace-assistant-tools" role="toolbar" aria-label="AI 助手工具"><ProjectAgent projectId={String(data.projectId)} compact /><Tooltip title="当前页大纲"><Button type="text" size="small" disabled={!state.count} aria-label="查看当前页大纲" icon={<UnorderedListOutlined aria-hidden />} onClick={() => call('showSlideOutline')} /></Tooltip>{data.autoRepair && <Tooltip title="修复排版"><Button type="text" size="small" disabled={!state.count} aria-label="修复排版" icon={<LayoutOutlined aria-hidden />} onClick={() => call('autoRepairSlideLayout')} /></Tooltip>}<Tooltip title="一键配图"><Button type="text" size="small" disabled={!state.count} aria-label="一键配图" icon={<PictureOutlined aria-hidden />} onClick={() => call('autoGenerateSlideImages')} /></Tooltip>{workspace && state.count > 0 && <><span className="workspace-assistant-divider" aria-hidden /><Tooltip title="检查文稿"><Button type="text" size="small" aria-label="检查文稿" icon={<ColumnHeightOutlined aria-hidden />} onClick={() => { openView('slides'); setContinuous(true); }} /></Tooltip><Tooltip title="准备讲稿"><Button type="text" size="small" aria-label="准备讲稿" icon={<AudioOutlined aria-hidden />} onClick={() => call('showSpeechScriptDialog')} /></Tooltip><Tooltip title="导出文件"><Button type="text" size="small" aria-label="导出文件" icon={<FolderOpenOutlined aria-hidden />} onClick={() => openView('exports')} /></Tooltip></>}</div>{!state.count && workspace && <div className="workspace-awaiting"><strong>从想法开始</strong><p>确认需求、整理大纲，再逐页完成设计。</p><Button type="primary" onClick={() => openView('planning')}>确认内容需求</Button></div>}<NativeAssistant /></section>}
      </Splitter.Panel>
    </Splitter>, document.getElementById('editorAntWorkspace')!)}
    <EditorQuickIcons />
    {state.commandError && createPortal(<Alert type="error" showIcon title={state.commandError} />, document.getElementById('editorAntToolbar')!)}
    {createPortal(<EditorDocumentSidebar projectId={String(data.projectId)} pages={state.documentPages} current={state.current} selected={state.selected} dialogCount={state.dialogs.length} bridge={controller} onNavigate={() => { openView('slides'); setContinuous(false); }} />, document.getElementById('editorAntSidebar')!)}
    <ExplainerEditorTools projectId={String(data.projectId)} page={{ slideId: state.currentSlideId, videoId: state.currentVideoId, index: state.current, title: state.title }} dialogOpen={explainerOpen} closeDialog={() => setExplainerOpen(false)} save={() => controller.save()} deleteCurrent={() => call('deleteSlide')} toolbar={document.getElementById('editorAntToolbar')} />
    <EditorThumbnailMenu state={state.contextMenu} run={controller.runContextAction} dismiss={controller.dismissContextMenu} />
    {createPortal(<header className="editor-ant-header"><Button aria-label="展开或收起幻灯片列表" aria-expanded={state.sidebar} aria-controls="slidesSidebar" icon={<BarsOutlined aria-hidden />} onClick={() => controller.call('toggleSidebar')} /><div className="editor-ant-title"><h1 title={data.title}>{data.title}</h1><span>{state.count ? `第 ${state.current + 1} / ${state.count} 页` : "暂无幻灯片"}{state.svg && <Tag>SVG</Tag>}</span></div>{workspace && <Segmented className="workspace-tabs" aria-label="项目工作区视图" value={view} options={[{ value: 'overview', label: '概览' }, { value: 'planning', label: '大纲' }, { value: 'generate', label: '生成' }, { value: 'slides', label: '编辑' }]} onChange={openView} />}<Button href="/projects" icon={<ArrowLeftOutlined aria-hidden />}>项目列表</Button></header>, document.getElementById('editorAntHeader')!)}
    {createPortal(toolbar, document.getElementById('editorAntToolbar')!)}
    {createPortal(<><div className="workspace-route-body" hidden={view === 'slides'}><Suspense fallback={<p role="status">正在加载工作区…</p>}>{workspace && <><div hidden={view !== 'overview'}>{visited.has('overview') && <ProjectDetail data={workspace.detail} />}</div><div hidden={view !== 'planning'}>{visited.has('planning') && <Todo data={workspace.planning} active={view === 'planning'} />}</div><div hidden={view !== 'generate'}>{visited.has('generate') && <Generator data={workspace.generation} active={view === 'generate'} />}</div><div hidden={view !== 'sources'}>{visited.has('sources') && <SourceLibrary active={view === 'sources'} projectId={String(data.projectId)} onSourcesSaved={(projectId, summary) => window.dispatchEvent(new CustomEvent('landppt:project-sources-saved', { detail: { projectId, summary } }))} />}</div><div hidden={view !== 'exports'}>{visited.has('exports') && <ArtifactLibrary active={view === 'exports'} projectId={String(data.projectId)} />}</div></>}</Suspense></div>{continuous && view === 'slides' && <ContinuousPreview pages={state.previewPages || []} onEdit={index => { setContinuous(false); call('selectSlide', index); }} />}</>, document.getElementById('editorWorkspacePage')!)}
    {!desktop && <Drawer title={<Space>AI 编辑助手<Tag>第 {state.current + 1} 页</Tag></Space>} open={editing && state.aiOpen} onClose={() => call('closeAIEditSidebar')} zIndex={1500} size={screens.xs ? '100%' : Math.min(state.aiWidth || 480, window.innerWidth)} className="editor-ant-assistant" forceRender
      extra={<Space size={4}><Button aria-label="查看当前页大纲" onClick={() => call('showSlideOutline')}>大纲</Button>{data.autoRepair && <Button disabled={!state.count} onClick={() => call('autoRepairSlideLayout')}>修复排版</Button>}<Button disabled={!state.count} aria-label="一键配图" onClick={() => call('autoGenerateSlideImages')}>配图</Button></Space>}>
      <NativeAssistant />
    </Drawer>}
    {state.dialogs.map((record, index) => record.node.id === 'exportOverlay'
      ? <EditorExportDialog key={record.key} record={record} index={index} projectTitle={data.title} cancel={controller.cancelExport} dismiss={controller.dismissExport} />
      : record.shareLinks ? <EditorShareDialog key={record.key} record={record} index={index} projectTitle={data.title} run={action => controller.runShareAction(record, action)} dismiss={() => controller.close(record)} />
      : <EditorDialog key={record.key} controller={controller} record={record} index={index} tasks={record.key === taskDialog?.key ? state.toasts : []} />)}
  </>;
}
