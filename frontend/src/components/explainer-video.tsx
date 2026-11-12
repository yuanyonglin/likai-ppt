import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, App, Button, Card, Empty, Flex, Input, Modal, Progress, Radio, Select, Space, Spin, Steps, Tag, Typography } from 'antd';
import { DownloadOutlined, EditOutlined, LoadingOutlined, PlusSquareOutlined, PushpinOutlined, ReloadOutlined, VideoCameraOutlined } from '@ant-design/icons';
import { request, type ApiData } from '../api';
import { modalFocus } from './modal-focus';
import { SlideDocument } from './slide-document';
import {
  createExplainerApi, createExplainerTracker, explainerCoverFile, explainerStages, explainerVideoFile, formatClock, parseExplainerVideoId,
  placementLabel, segmentStarts, sourceLabel, stageIndex, type ExplainerSegment, type ExplainerTracker, type ExplainerVideo, type TrackerState,
} from '../controllers/explainer-video-controller';
import './explainer-video.css';

export const explainerApi = createExplainerApi();
export const videoUrl = (video: ExplainerVideo) => video.video_url || explainerVideoFile(video.id);
export const coverUrl = (video: ExplainerVideo) => video.cover_url || explainerCoverFile(video.id);
export const editorURL = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/edit`;

export function useExplainerTracker(): [TrackerState, ExplainerTracker] {
  const [tracker] = useState(() => createExplainerTracker(explainerApi));
  const [state, setState] = useState(tracker.getSnapshot);
  useEffect(() => { const off = tracker.subscribe(setState); return () => { off(); tracker.dispose(); }; }, [tracker]);
  return [state, tracker];
}

export function ExplainerProgress({ state, title, crumb, extra }: { state: TrackerState; title: string; crumb?: string; extra?: ReactNode }) {
  const current = Math.max(0, stageIndex(state.stage));
  const failed = state.phase === 'failed';
  return <Card className="explainer-progress" size="small">
    {crumb && <Typography.Text type="secondary">{crumb}</Typography.Text>}
    <Typography.Title level={4}>{failed ? '讲解视频生成失败' : `正在生成讲解视频：${title}`}</Typography.Title>
    <Typography.Text type="secondary">后台任务，可以离开本页；完成后会出现在「素材 · 讲解视频」。</Typography.Text>
    <Steps className="explainer-progress-steps" size="small" responsive={false} current={current} status={failed ? 'error' : 'process'} items={explainerStages.map(([, label]) => ({ title: label }))} />
    {failed ? <Alert type="error" showIcon title={state.error} action={extra} />
      : <section aria-label="讲解视频生成进度"><Flex justify="space-between"><span role="status" aria-live="polite">{state.message || `${explainerStages[current][1]}…`}</span><strong>{state.percent ? `${state.percent}%` : <LoadingOutlined spin aria-hidden />}</strong></Flex>
        <Progress percent={state.percent} strokeColor="var(--lp-primary)" showInfo={false} aria-label="讲解视频生成进度" />{extra}</section>}
  </Card>;
}

type PickedSlide = { project: ApiData; slide: ApiData; index: number };
/** Pick a project slide: the explainer source, or the page to insert after. */
export function SlidePicker({ open, title, sourceMode = false, initialProjectId, onCancel, onPick }: { open: boolean; title: string; sourceMode?: boolean; initialProjectId?: string; onCancel: () => void; onPick: (picked: PickedSlide) => void | Promise<void> }) {
  const [projects, setProjects] = useState<ApiData[]>([]), [projectId, setProjectId] = useState(initialProjectId || '');
  const [slides, setSlides] = useState<ApiData[]>([]), [selected, setSelected] = useState(-1);
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    request('/api/projects?page=1&page_size=50', 'GET', undefined, controller.signal).then(result => {
      const list = Array.isArray(result.projects) ? result.projects : [];
      setProjects(list);
      setProjectId(current => current && list.some((item: ApiData) => item.project_id === current) ? current : list[0]?.project_id || '');
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [open]);
  useEffect(() => {
    if (!open || !projectId) return;
    const controller = new AbortController();
    setLoading(true); setError(''); setSlides([]); setSelected(-1);
    request(`/api/projects/${encodeURIComponent(projectId)}/slides-data`, 'GET', undefined, controller.signal)
      .then(result => { if (!controller.signal.aborted) setSlides(Array.isArray(result.slides_data) ? result.slides_data : []); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [open, projectId]);
  const project = projects.find(item => item.project_id === projectId);
  async function confirm() {
    if (!project || selected < 0) return;
    setBusy(true);
    try { await onPick({ project, slide: slides[selected], index: selected }); } finally { setBusy(false); }
  }
  return <Modal open={open} title={title} width={960} onCancel={onCancel} modalRender={modalFocus} destroyOnHidden
    footer={<Space><Button onClick={onCancel}>取消</Button><Button type="primary" disabled={selected < 0} loading={busy} onClick={confirm}>确定</Button></Space>}>
    <Space orientation="vertical" size="middle" className="explainer-picker">
      <Select aria-label="选择项目" className="explainer-picker-project" showSearch optionFilterProp="label" value={projectId || undefined} placeholder="选择项目"
        options={projects.map(item => ({ value: item.project_id, label: item.title || item.topic || item.project_id }))} onChange={setProjectId} />
      {error && <Alert type="error" showIcon title={error} />}
      {loading || !projectId && !error ? <div role="status"><Spin size="small" /> 正在读取页面…</div> : !slides.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="这个项目还没有页面" />
        : <div className="explainer-slide-grid" role="listbox" aria-label="项目页面">{slides.map((slide, index) => {
          const isVideo = !!parseExplainerVideoId(slide.html_content);
          const disabled = sourceMode && isVideo;
          return <button type="button" key={slide.slide_id || index} role="option" aria-selected={selected === index} disabled={disabled} className={`explainer-slide${selected === index ? ' is-selected' : ''}`} onClick={() => setSelected(index)}>
            <SlideDocument html={slide.html_content || ''} title={`第 ${index + 1} 页预览`} />
            <span>{isVideo && <Tag color="blue">🎬 视频页</Tag>}{index + 1}. {slide.title || '未命名页面'}</span>
          </button>;
        })}</div>}
    </Space>
  </Modal>;
}

/** Insert after or replace the source page (contract §3 place). */
export function PlaceDialog({ open, video, target, onCancel, onPlaced }: { open: boolean; video: ExplainerVideo; target: { project_id: string; slide_id: string; label?: string }; onCancel: () => void; onPlaced: (projectId: string, result: { slide_id: string; slide_index: number }) => void }) {
  const { message } = App.useApp();
  const [mode, setMode] = useState<'insert' | 'replace'>('insert'), [busy, setBusy] = useState(false);
  async function confirm() {
    setBusy(true);
    try {
      const result = await explainerApi.place(video.id, mode === 'insert' ? { project_id: target.project_id, after_slide_id: target.slide_id } : { project_id: target.project_id, replace_slide_id: target.slide_id });
      onPlaced(target.project_id, result);
    } catch (reason) { message.error((reason as Error).message); } finally { setBusy(false); }
  }
  return <Modal open={open} title="加入 PPT" width={620} onCancel={onCancel} modalRender={modalFocus} okText="确定" cancelText="取消" okButtonProps={{ loading: busy }} onOk={confirm}>
    {target.label && <Typography.Paragraph type="secondary">目标：{target.label}</Typography.Paragraph>}
    <Radio.Group className="explainer-place-options" value={mode} onChange={event => setMode(event.target.value)} options={[
      { value: 'insert', label: <div><strong>在该页后新增一页视频页 <Tag>推荐</Tag></strong><Typography.Paragraph type="secondary">原页面保留不动；新页面是整页视频。PDF、打印、讲稿都不受影响。</Typography.Paragraph></div> },
      { value: 'replace', label: <div><strong>用视频替换该页</strong><Typography.Paragraph type="secondary">该页变成整页视频，原页面内容会备份，可随时「恢复原页面」。PDF 里这一页只剩视频封面。</Typography.Paragraph></div> },
    ]} />
  </Modal>;
}

/** Insert a finished video after a page chosen in any project. */
export function InsertIntoProject({ video, open, onClose, onPlaced }: { video: ExplainerVideo; open: boolean; onClose: () => void; onPlaced?: (projectId: string) => void }) {
  const { message } = App.useApp();
  return <SlidePicker open={open} title="插入到哪一页之后" onCancel={onClose} onPick={async ({ project, slide, index }) => {
    try {
      await explainerApi.place(video.id, { project_id: project.project_id, after_slide_id: slide.slide_id });
      message.success(`已插入到「${project.title || '项目'}」第 ${index + 1} 页之后`);
      onClose(); onPlaced?.(project.project_id);
    } catch (reason) { message.error((reason as Error).message); }
  }} />;
}

/** Player + segmented script (click to seek, edit, re-voice) + placement actions. */
export function ExplainerResult({ state, tracker, onPlaced, actions = true }: { state: TrackerState; tracker: ExplainerTracker; onPlaced?: (projectId: string) => void; actions?: boolean }) {
  const video = state.video!;
  const player = useRef<HTMLVideoElement>(null);
  const [editing, setEditing] = useState(false), [draft, setDraft] = useState<ExplainerSegment[]>([]);
  const [placing, setPlacing] = useState(false), [inserting, setInserting] = useState(false);
  const segments = video.script?.segments || [], starts = segmentStarts(video.script);
  const fromSlide = video.source_type === 'slide';
  const ref = video.source_ref || {};
  const placed = onPlaced || ((projectId: string) => location.assign(editorURL(projectId)));
  const seek = (time: number) => { const node = player.current; if (node) { node.currentTime = time; void node.play().catch(() => {}); } };
  const download = <Button type={fromSlide ? 'default' : 'primary'} icon={<DownloadOutlined aria-hidden />} href={videoUrl(video)} download={`${video.title || '讲解视频'}.mp4`}>下载 MP4</Button>;
  return <div className="explainer-result">
    <Flex align="center" gap={8} wrap><Typography.Title level={4}>{video.title}</Typography.Title><Tag>已完成{video.duration_ms ? ` · ${formatClock(video.duration_ms / 1000)}` : ''}</Tag><Tag color={video.placements?.length ? 'green' : 'blue'}>{placementLabel(video)}</Tag></Flex>
    <Typography.Text type="secondary">{sourceLabel(video)}</Typography.Text>
    <div className="explainer-result-grid">
      <div>
        <video ref={player} className="explainer-player" src={videoUrl(video)} poster={coverUrl(video)} controls playsInline preload="metadata" aria-label="讲解视频播放器" />
        {actions && <Space wrap className="explainer-result-actions">
          {fromSlide ? <><Button type="primary" icon={<PushpinOutlined aria-hidden />} onClick={() => setPlacing(true)}>加入 PPT</Button>{download}</>
            : <>{download}<Button icon={<PlusSquareOutlined aria-hidden />} onClick={() => setInserting(true)}>插入到某个项目</Button></>}
          <Button icon={<ReloadOutlined aria-hidden />} onClick={() => void tracker.regenerate()}>重新生成画面</Button>
        </Space>}
      </div>
      <section aria-label="讲解稿">
        <Flex justify="space-between" align="center"><strong>讲解稿 <Typography.Text type="secondary">（点击跳转，可编辑后重新配音）</Typography.Text></strong>
          {!editing && <Button size="small" icon={<EditOutlined aria-hidden />} disabled={!segments.length} onClick={() => { setDraft(segments.map(item => ({ ...item }))); setEditing(true); }}>编辑讲解稿</Button>}</Flex>
        {editing ? <Space orientation="vertical" className="explainer-segments">
          {draft.map((segment, index) => <Input.TextArea key={index} aria-label={`第 ${index + 1} 段讲解稿`} autoSize={{ minRows: 2, maxRows: 8 }} value={segment.text} onChange={event => setDraft(current => current.map((item, i) => i === index ? { ...item, text: event.target.value } : item))} />)}
          <Space><Button onClick={() => setEditing(false)}>取消</Button><Button type="primary" disabled={draft.some(item => !item.text.trim())} onClick={() => { setEditing(false); void tracker.regenerate({ ...video.script, segments: draft }); }}>按新讲解稿重新配音</Button></Space>
        </Space> : !segments.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无讲解稿" />
          : <ol className="explainer-segments">{segments.map((segment, index) => <li key={index}><button type="button" className="explainer-segment" onClick={() => seek(starts[index])}>
            <Typography.Text type="secondary">{formatClock(starts[index])}{segment.visual ? ` · ${segment.visual.slice(0, 18)}` : ''}</Typography.Text><span>{segment.text}</span>
          </button></li>)}</ol>}
      </section>
    </div>
    {fromSlide && placing && <PlaceDialog open video={video} target={{ project_id: ref.project_id, slide_id: ref.slide_id, label: [ref.project_title, ref.slide_title].filter(Boolean).join(' · ') }} onCancel={() => setPlacing(false)} onPlaced={projectId => { setPlacing(false); placed(projectId); }} />}
    {inserting && <InsertIntoProject open video={video} onClose={() => setInserting(false)} onPlaced={placed} />}
  </div>;
}

/** Tracker-backed view used by the composer, the editor and the library. */
export function ExplainerTrackerView({ state, tracker, title, crumb, onPlaced, actions, onBack }: { state: TrackerState; tracker: ExplainerTracker; title: string; crumb?: string; onPlaced?: (projectId: string) => void; actions?: boolean; onBack?: () => void }) {
  if (state.phase === 'completed' && state.video) return <ExplainerResult state={state} tracker={tracker} onPlaced={onPlaced} actions={actions} />;
  return <ExplainerProgress state={state} title={state.video?.title || title} crumb={crumb}
    extra={state.phase === 'failed' && <Space>{state.videoId && <Button onClick={() => tracker.track(state.videoId)}>重新查询</Button>}{onBack && <Button onClick={onBack}>返回修改</Button>}</Space>} />;
}

export function ExplainerResultModal({ videoId, onClose, onPlaced, actions, zIndex }: { videoId: string | null; onClose: () => void; onPlaced?: (projectId: string) => void; actions?: boolean; zIndex?: number }) {
  const [state, tracker] = useExplainerTracker();
  useEffect(() => { if (videoId) tracker.track(videoId); else tracker.reset(); }, [videoId, tracker]);
  return <Modal open={!!videoId} title={<Space><VideoCameraOutlined aria-hidden />讲解视频</Space>} width={1100} zIndex={zIndex} footer={null} onCancel={onClose} modalRender={modalFocus} destroyOnHidden>
    {state.phase !== 'idle' && <ExplainerTrackerView state={state} tracker={tracker} title="讲解视频" onPlaced={onPlaced} actions={actions} />}
  </Modal>;
}

/** 素材库 · 讲解视频 */
export function ExplainerVideoLibrary({ active }: { active: boolean }) {
  const [videos, setVideos] = useState<ExplainerVideo[]>([]), [loading, setLoading] = useState(false), [error, setError] = useState(''), [reload, setReload] = useState(0);
  const [opened, setOpened] = useState<string | null>(null), [inserting, setInserting] = useState<ExplainerVideo | null>(null);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setLoading(true); setError('');
    explainerApi.list(undefined, controller.signal).then(list => { if (!controller.signal.aborted) setVideos(list); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, reload]);
  const refresh = () => setReload(value => value + 1);
  return <div className="explainer-library settings-stack">
    <div className="page-heading"><div><h1>讲解视频</h1><p>所有生成的讲解视频，可下载，或插入到项目作为新的一页。</p></div><Space><Button href="/dashboard?kind=video" icon={<VideoCameraOutlined aria-hidden />}>生成讲解视频</Button><Button aria-label="刷新讲解视频" loading={loading} icon={<ReloadOutlined aria-hidden />} onClick={refresh} /></Space></div>
    {error && <Alert showIcon type="error" title="讲解视频加载失败" description={error} action={<Button onClick={refresh}>重试</Button>} />}
    {!loading && !error && !videos.length && <Card size="small"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有讲解视频，先在首页切到「讲解视频」生成一个。" /></Card>}
    <section className="explainer-library-grid" aria-label="讲解视频列表" aria-busy={loading}>{videos.map(video => {
      const done = video.status === 'completed';
      return <Card key={video.id} size="small" cover={done ? <video src={videoUrl(video)} poster={coverUrl(video)} controls preload="none" playsInline aria-label={`播放 ${video.title}`} /> : <div className="explainer-library-pending">{video.status === 'failed' ? '生成失败' : <><LoadingOutlined spin aria-hidden /> 生成中</>}</div>}>
        <Typography.Text strong ellipsis={{ tooltip: video.title }}>{video.title}</Typography.Text>
        <Typography.Paragraph type="secondary" ellipsis={{ rows: 1, tooltip: sourceLabel(video) }}>{sourceLabel(video)}</Typography.Paragraph>
        <Flex gap={6} wrap>{video.duration_ms ? <Tag>{formatClock(video.duration_ms / 1000)}</Tag> : null}<Tag color={video.placements?.length ? 'green' : 'blue'}>{placementLabel(video)}</Tag>{video.status === 'failed' && <Tag color="error">失败</Tag>}</Flex>
        <Space wrap className="explainer-library-actions">
          <Button size="small" disabled={!done} href={done ? videoUrl(video) : undefined} download={`${video.title || '讲解视频'}.mp4`} icon={<DownloadOutlined aria-hidden />}>下载</Button>
          <Button size="small" disabled={!done} icon={<PlusSquareOutlined aria-hidden />} onClick={() => setInserting(video)}>插入到项目</Button>
          <Button size="small" onClick={() => setOpened(video.id)}>{done ? '讲解稿' : '查看进度'}</Button>
        </Space>
      </Card>;
    })}</section>
    {inserting && <InsertIntoProject open video={inserting} onClose={() => setInserting(null)} onPlaced={refresh} />}
    <ExplainerResultModal videoId={opened} onClose={() => { setOpened(null); refresh(); }} />
  </div>;
}
