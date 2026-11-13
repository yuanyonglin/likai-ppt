import { useEffect, useState } from 'react';
import { Alert, App, Button, Card, Input, Radio, Segmented, Select, Space, Spin, Switch, Tag, Typography, Upload } from 'antd';
import { EditOutlined, FileTextOutlined, InboxOutlined, PaperClipOutlined, VideoCameraOutlined } from '@ant-design/icons';
import { request } from '../api';
import { SlideDocument } from './slide-document';
import { ExplainerTrackerView, SlidePicker, explainerApi, useExplainerTracker } from './explainer-video';
import {
  buildCreateBody, explainerDurations, explainerStyles, explainerUploadExtensions, ttsChoices,
  type ExplainerOptions, type ExplainerSource, type ParsedUpload,
} from '../controllers/explainer-video-controller';

type SourceKind = 'slide' | 'upload' | 'text';
type Picked = { projectId: string; projectTitle: string; slideId: string; slideTitle: string; index: number; html: string };
const pairs = (items: readonly (readonly [string, string])[]) => items.map(([value, label]) => ({ value, label }));

/** Shared TTS provider choice; same provider list and default source as the editor narration. */
export function useTtsChoices() {
  const [choices, setChoices] = useState(() => ttsChoices());
  useEffect(() => {
    const controller = new AbortController();
    request('/api/config/generation_params', 'GET', undefined, controller.signal).then(result => setChoices(ttsChoices(result.config || {}))).catch(() => { /* Keep the built-in provider list. */ });
    return () => controller.abort();
  }, []);
  return choices;
}

function setResumeParam(videoId: string) {
  const url = new URL(location.href);
  if (videoId) url.searchParams.set('explainer', videoId); else url.searchParams.delete('explainer');
  history.replaceState(history.state, '', url);
}

/** Home / create page "讲解视频" mode. Does not touch the PPT outline/todo flow. */
export function ExplainerStudio({ onVideo }: { onVideo?: (videoId: string) => void } = {}) {
  const { message } = App.useApp();
  const [state, tracker] = useExplainerTracker();
  const tts = useTtsChoices();
  const [source, setSource] = useState<SourceKind>('slide');
  const [picked, setPicked] = useState<Picked | null>(null), [picking, setPicking] = useState(false);
  const [file, setFile] = useState<File | null>(null), [parsed, setParsed] = useState<ParsedUpload | null>(null), [parsing, setParsing] = useState(false), [parseError, setParseError] = useState(''), [section, setSection] = useState<number | null>(null);
  const [title, setTitle] = useState(''), [text, setText] = useState(''), [extra, setExtra] = useState('');
  const [options, setOptions] = useState<Omit<ExplainerOptions, 'tts_provider'> & { tts_provider?: string }>({ duration: '1m', voice: null, style: 'follow_ppt', subtitles: true });
  const [crumb, setCrumb] = useState(''), [heading, setHeading] = useState('');
  useEffect(() => { const id = new URLSearchParams(location.search).get('explainer'); if (id) tracker.track(id); }, [tracker]);
  useEffect(() => { if (state.videoId) { setResumeParam(state.videoId); onVideo?.(state.videoId); } }, [state.videoId, onVideo]);

  async function parse(next: File) {
    const extension = next.name.split('.').pop()?.toLowerCase() || '';
    if (!explainerUploadExtensions.includes(extension)) { message.warning(`不支持的文件类型：${next.name}`); return false; }
    if (next.size > 100 * 1024 * 1024) { message.warning(`文件超过 100MB：${next.name}`); return false; }
    setFile(next); setParsed(null); setSection(null); setParseError(''); setParsing(true);
    try { setParsed(await explainerApi.parseUpload(next)); } catch (reason) { setParseError((reason as Error).message); } finally { setParsing(false); }
    return false;
  }
  const chosen: { source: ExplainerSource; title: string; crumb: string } | null =
    source === 'slide' ? picked && { source: { type: 'slide', project_id: picked.projectId, slide_id: picked.slideId }, title: picked.slideTitle, crumb: `${picked.projectTitle} · 第 ${picked.index + 1} 页` }
    : source === 'upload' ? parsed && section != null ? { source: { type: 'upload', file_id: parsed.file_id, section_index: section }, title: parsed.sections.find(item => item.index === section)?.title || parsed.filename, crumb: `上传文件 · ${parsed.filename}` } : null
    : title.trim() ? { source: { type: 'text', title, text }, title: title.trim(), crumb: '直接描述' } : null;
  function generate() {
    if (!chosen) return;
    setCrumb(chosen.crumb); setHeading(chosen.title);
    void tracker.start(buildCreateBody({ source: chosen.source, extra, options: { ...options, tts_provider: options.tts_provider || tts.value } }));
  }
  function back() { tracker.reset(); setResumeParam(''); }

  if (state.phase !== 'idle') return <Space orientation="vertical" size="middle" className="explainer-studio-run">
    <ExplainerTrackerView state={state} tracker={tracker} title={heading || '讲解视频'} crumb={crumb} onBack={back} />
    {state.phase !== 'running' && <Button onClick={back}>再生成一个</Button>}
  </Space>;

  return <Card className="explainer-studio" size="small">
    <Space wrap><Typography.Text type="secondary">讲解什么：</Typography.Text>
      <Segmented aria-label="讲解来源" value={source} onChange={value => setSource(value as SourceKind)} options={[
        { value: 'slide', label: 'PPT 中的某一页', icon: <FileTextOutlined aria-hidden /> },
        { value: 'upload', label: '上传文件', icon: <PaperClipOutlined aria-hidden /> },
        { value: 'text', label: '直接描述', icon: <EditOutlined aria-hidden /> },
      ]} /></Space>
    {source === 'slide' && (picked ? <div className="explainer-picked">
      <SlideDocument html={picked.html} title="已选页面预览" />
      <div><Typography.Text type="secondary">{picked.projectTitle} · 第 {picked.index + 1} 页</Typography.Text><strong>{picked.slideTitle}</strong><Typography.Text type="secondary">读取本页内容与已有讲稿；生成后可「加入 PPT」</Typography.Text></div>
      <Button onClick={() => setPicking(true)}>更换</Button>
    </div> : <div><Button onClick={() => setPicking(true)}>＋ 选择一页 PPT</Button></div>)}
    {source === 'upload' && (!file ? <Upload.Dragger accept={explainerUploadExtensions.map(ext => '.' + ext).join(',')} showUploadList={false} beforeUpload={parse}>
      <InboxOutlined aria-hidden className="create-upload-icon" /><p>拖入文件，或点击选择</p><p className="subtle">解析后选择要讲的那一页 / 那一节，一条视频只讲一段内容</p>
      <Space wrap>{['PDF', 'Word', 'PPTX', 'TXT / MD'].map(item => <Tag key={item}>{item}</Tag>)}</Space>
    </Upload.Dragger> : <Space orientation="vertical" className="explainer-upload">
      <Space wrap><PaperClipOutlined aria-hidden /><strong>{file.name}</strong><Typography.Text type="secondary">{(file.size / 1024 / 1024).toFixed(1)} MB</Typography.Text>
        <Typography.Text type="secondary" role="status">{parsing ? <><Spin size="small" /> 正在解析…</> : parsed ? `解析完成 · ${parsed.sections.length} 段 · 选择要讲的一段` : ''}</Typography.Text>
        <Button size="small" disabled={parsing} onClick={() => { setFile(null); setParsed(null); setSection(null); setParseError(''); }}>更换文件</Button></Space>
      {parseError && <Alert type="error" showIcon title="文件解析失败" description={parseError} action={<Button onClick={() => void parse(file)}>重试</Button>} />}
      {parsed && <Radio.Group className="explainer-sections" aria-label="选择要讲的一段" value={section} onChange={event => setSection(event.target.value)}
        options={parsed.sections.map(item => ({ value: item.index, label: <span><Typography.Text type="secondary">{item.index + 1}.</Typography.Text> {item.title || item.text.slice(0, 40) || '未命名段落'}</span> }))} />}
    </Space>)}
    {source === 'text' && <>
      <Input aria-label="讲解主题" size="large" value={title} onChange={event => setTitle(event.target.value)} placeholder="讲解主题，例如：异常响应线上闭环是怎么跑起来的" />
      <Input.TextArea aria-label="讲解内容描述" autoSize={{ minRows: 3, maxRows: 10 }} value={text} onChange={event => setText(event.target.value)} placeholder="描述要讲的内容、要点、数据或受众（越具体越好）" />
    </>}
    <Input.TextArea aria-label="补充讲解方向" autoSize={{ minRows: 2, maxRows: 6 }} value={extra} onChange={event => setExtra(event.target.value)} placeholder="补充讲解方向（选填），例如：面向车间班组长，多讲落地做法" />
    <div className="explainer-studio-options">
      <Select aria-label="视频时长" value={options.duration} options={pairs(explainerDurations)} onChange={duration => setOptions(current => ({ ...current, duration }))} popupMatchSelectWidth={false} />
      <Select aria-label="配音" value={options.tts_provider || tts.value} options={tts.options} onChange={tts_provider => setOptions(current => ({ ...current, tts_provider }))} popupMatchSelectWidth={false} />
      <Select aria-label="画面风格" value={options.style} options={pairs(explainerStyles)} onChange={style => setOptions(current => ({ ...current, style }))} popupMatchSelectWidth={false} />
      <Space size={6}><Switch aria-label="字幕" size="small" checked={options.subtitles} onChange={subtitles => setOptions(current => ({ ...current, subtitles }))} />字幕</Space>
      <Button type="primary" className="explainer-go" icon={<VideoCameraOutlined aria-hidden />} disabled={!chosen} onClick={generate}>生成讲解视频</Button>
    </div>
    <SlidePicker open={picking} sourceMode title="选择要讲解的页面" initialProjectId={picked?.projectId} onCancel={() => setPicking(false)} onPick={({ project, slide, index }) => {
      setPicked({ projectId: project.project_id, projectTitle: project.title || '项目', slideId: slide.slide_id, slideTitle: slide.title || `第 ${index + 1} 页`, index, html: slide.html_content || '' });
      setPicking(false);
    }} />
  </Card>;
}
