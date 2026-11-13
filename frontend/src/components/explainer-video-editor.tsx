import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { App, Button, Input, Modal, Radio, Tag, Typography } from 'antd';
import { DeleteOutlined, DownloadOutlined, EditOutlined, RollbackOutlined } from '@ant-design/icons';
import { modalFocus } from './modal-focus';
import { editorTaskNotice } from './editor-task-progress';
import { ExplainerResultModal, explainerApi, useExplainerTracker, videoUrl } from './explainer-video';
import { useTtsChoices } from './explainer-studio';
import { buildCreateBody, formatClock, placementOf, type ExplainerVideo, type PlacementMode } from '../controllers/explainer-video-controller';

type EditorPage = { slideId: string | null; videoId: string | null; index: number; title: string };
type Props = { projectId: string; page: EditorPage; dialogOpen: boolean; closeDialog: () => void; save: () => Promise<unknown>; deleteCurrent: () => void; toolbar: HTMLElement | null };
const noticeKey = 'explainer-video-generation';
// Above the editor workspace layers, like the editor's own dialogs.
const editorLayer = 2000;

/** Editor entry ("更多 → 生成本页讲解视频") plus the toolbar shown on video pages. */
export function ExplainerEditorTools({ projectId, page, dialogOpen, closeDialog, save, deleteCurrent, toolbar }: Props) {
  const { notification, message } = App.useApp();
  const [state, tracker] = useExplainerTracker();
  const tts = useTtsChoices();
  const [extra, setExtra] = useState(''), [mode, setMode] = useState<PlacementMode>('insert'), [starting, setStarting] = useState(false);
  const [resultId, setResultId] = useState<string | null>(null);
  const [video, setVideo] = useState<ExplainerVideo | null>(null), [busy, setBusy] = useState(false);
  const target = useRef({ mode, title: '' });

  useEffect(() => {
    if (state.phase === 'idle') return;
    const { title } = target.current;
    if (state.phase === 'running') {
      notification.open({ key: noticeKey, ...editorTaskNotice({ title: '正在生成讲解视频', message: `第 ${page.index + 1} 页「${title}」· ${state.message || '处理中'}`, percent: state.percent ? `${state.percent}%` : undefined }), duration: 0, closable: false, placement: 'bottomRight' });
    } else if (state.phase === 'failed') {
      notification.error({ key: noticeKey, title: '讲解视频生成失败', description: state.error, duration: 0, placement: 'bottomRight' });
    } else if (target.current.mode === 'none') {
      notification.success({ key: noticeKey, title: '讲解视频已生成', description: '已存入素材库，可下载或稍后加入 PPT。', placement: 'bottomRight' });
      setResultId(state.videoId);
    } else {
      // The backend placed the page; re-read the project so stale slides are never saved over it.
      notification.success({ key: noticeKey, title: '视频页已加入', description: '正在刷新编辑器…', placement: 'bottomRight' });
      location.reload();
    }
  }, [state.phase, state.percent, state.message, state.error]);

  useEffect(() => {
    setVideo(null);
    if (!page.videoId) return;
    const controller = new AbortController();
    explainerApi.get(page.videoId, controller.signal).then(value => { if (!controller.signal.aborted) setVideo(value); }).catch(() => { /* The bar still offers delete and download. */ });
    return () => controller.abort();
  }, [page.videoId]);

  async function start() {
    if (!page.slideId) { message.error('当前页尚未保存，请先保存后再生成讲解视频'); return; }
    setStarting(true);
    try {
      // Flush pending edits before the backend inserts or replaces pages.
      if (await save() === false) return;
      target.current = { mode, title: page.title };
      closeDialog(); setExtra('');
      await tracker.start(buildCreateBody({ source: { type: 'slide', project_id: projectId, slide_id: page.slideId }, extra, placement: mode,
        options: { duration: '1m', tts_provider: tts.value, voice: null, style: 'follow_ppt', subtitles: true } }));
    } finally { setStarting(false); }
  }
  async function restore() {
    if (!page.videoId || !page.slideId) return;
    setBusy(true);
    try {
      if (await save() === false) return;
      await explainerApi.restore(page.videoId, { project_id: projectId, slide_id: page.slideId });
      message.success('已恢复原页面，视频仍在素材库');
      location.reload();
    } catch (reason) { message.error((reason as Error).message); } finally { setBusy(false); }
  }

  const replaced = placementOf(video, projectId, page.slideId)?.mode === 'replace';
  const bar = page.videoId && toolbar && createPortal(<div className="explainer-video-bar" role="toolbar" aria-label="视频页工具">
    <Tag color="blue">🎬 视频页{video?.duration_ms ? ` · ${formatClock(video.duration_ms / 1000)}` : ''}</Tag>
    <Typography.Text type="secondary">整页视频，固定版式</Typography.Text><span className="explainer-video-bar-spacer" />
    {replaced ? <Button size="small" icon={<RollbackOutlined aria-hidden />} loading={busy} onClick={restore}>恢复原页面</Button>
      : <Button size="small" icon={<DeleteOutlined aria-hidden />} onClick={deleteCurrent}>删除视频页</Button>}
    <Button size="small" icon={<EditOutlined aria-hidden />} onClick={() => setResultId(page.videoId)}>编辑讲解稿</Button>
    <Button size="small" icon={<DownloadOutlined aria-hidden />} href={video ? videoUrl(video) : `/api/explainer-videos/${encodeURIComponent(page.videoId)}/video.mp4`} download={`${video?.title || '讲解视频'}.mp4`}>下载</Button>
  </div>, toolbar);

  return <>
    {bar}
    <Modal open={dialogOpen} title="🎬 生成本页讲解视频" width={600} zIndex={editorLayer} onCancel={closeDialog} modalRender={modalFocus} okText="开始生成" cancelText="取消" okButtonProps={{ loading: starting, disabled: state.phase === 'running' }} onOk={start}>
      <Typography.Paragraph type="secondary">当前页：第 {page.index + 1} 页 · {page.title || '未命名页面'}。将读取本页内容与已有讲稿扩写讲解稿。</Typography.Paragraph>
      {state.phase === 'running' && <Typography.Paragraph type="warning">已有讲解视频在生成中，请等待完成后再发起。</Typography.Paragraph>}
      <Input.TextArea aria-label="补充讲解方向" autoSize={{ minRows: 2, maxRows: 6 }} value={extra} onChange={event => setExtra(event.target.value)} placeholder="补充讲解方向（选填）" />
      <Typography.Paragraph strong className="explainer-dialog-label">生成后：</Typography.Paragraph>
      <Radio.Group className="explainer-place-options" value={mode} onChange={event => setMode(event.target.value)} options={[
        { value: 'insert', label: <span><strong>在本页后新增视频页</strong> <Tag>推荐</Tag></span> },
        { value: 'replace', label: <span><strong>替换本页</strong> <Typography.Text type="secondary">（原页备份，可恢复）</Typography.Text></span> },
        { value: 'none', label: <span><strong>只生成，不加入 PPT</strong> <Typography.Text type="secondary">（存素材库，可下载）</Typography.Text></span> },
      ]} />
      {mode !== 'none' && <Typography.Paragraph type="secondary" className="explainer-dialog-label">生成完成后编辑器会自动刷新，生成期间请勿修改本项目。</Typography.Paragraph>}
    </Modal>
    <ExplainerResultModal videoId={resultId} zIndex={editorLayer} actions={!page.videoId} onClose={() => setResultId(null)} />
  </>;
}
