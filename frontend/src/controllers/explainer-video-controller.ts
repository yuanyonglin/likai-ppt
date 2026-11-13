// Explainer video API (EXPLAINER_VIDEO_CONTRACT.md §3), request shaping and task
// tracking. No runtime imports so the controller tests run under strip-types.
type Json = Record<string, any>;
export type ExplainerSegment = { text: string; visual?: string; bullets?: string[]; start?: number; duration?: number };
export type ExplainerScript = { title?: string; segments: ExplainerSegment[] };
export type ExplainerPlacement = { project_id: string; slide_id: string; mode: 'insert' | 'replace' };
export type ExplainerVideo = {
  id: string; status: string; error?: string | null; title: string; source_type: 'slide' | 'upload' | 'text'; source_ref: Json;
  script?: ExplainerScript | null; video_url?: string; cover_url?: string; duration_ms?: number | null; placements?: ExplainerPlacement[]; stage?: string;
  progress?: number; created_at?: number; updated_at?: number;
};
export type ExplainerSource = { type: 'slide'; project_id: string; slide_id: string } | { type: 'upload'; file_id: string; section_index: number } | { type: 'text'; title: string; text: string };
export type ExplainerOptions = { duration: '1m' | '2m' | '3m'; tts_provider: string; voice: string | null; style: 'follow_ppt' | 'business_blue' | 'dark_tech'; subtitles: boolean };
export type PlacementMode = 'insert' | 'replace' | 'none';
export type ExplainerCreateBody = { source: ExplainerSource; extra: string; options: ExplainerOptions; placement: { mode: PlacementMode } };
export type UploadSection = { index: number; title: string; text: string };
export type ParsedUpload = { file_id: string; filename: string; sections: UploadSection[] };

export const explainerStages = [['parse', '解析内容'], ['script', '扩写讲解稿'], ['tts', '配音'], ['animation', '生成动画'], ['render', '录制合成']] as const;
export const explainerUploadExtensions = ['pdf', 'docx', 'pptx', 'txt', 'md'];
export const explainerDurations = [['1m', '约 1 分钟'], ['2m', '约 2 分钟'], ['3m', '约 3 分钟']] as const;
export const explainerStyles = [['follow_ppt', '画面跟随 PPT 风格'], ['business_blue', '简洁商务蓝'], ['dark_tech', '深色科技']] as const;
// Same providers as the editor narration dialog, minus ComfyUI (needs a reference upload).
const ttsProviders = ['auto', 'cloud_tts', 'edge_tts', 'custom_tts_api'];

/** The only reliable marker of a video page (contract §1). */
export function parseExplainerVideoId(html: unknown): string | null {
  const match = typeof html === 'string' ? /data-explainer-video-id\s*=\s*["']([^"']+)["']/.exec(html) : null;
  return match ? match[1] : null;
}
export const explainerVideoFile = (id: string) => `/api/explainer-videos/${encodeURIComponent(id)}/video.mp4`;
export const explainerCoverFile = (id: string) => `/api/explainer-videos/${encodeURIComponent(id)}/cover.png`;
/** Home page studio opened on one video's progress or result (same link the notification uses). */
export const explainerResultURL = (id: string) => `/dashboard?kind=video&explainer=${encodeURIComponent(id)}`;
export const isExplainerRunning = (video: ExplainerVideo) => video.status === 'pending' || video.status === 'running';
const explainerAge = (video: ExplainerVideo, nowSeconds: number) => nowSeconds - Number(video.updated_at || video.created_at || 0);
/**
 * Still generating. A run interrupted by a restart stays "running" in the database;
 * after 30 minutes without a stage update it is no longer treated as in progress.
 */
export const isExplainerActive = (video: ExplainerVideo, nowSeconds = Date.now() / 1000) => isExplainerRunning(video) && explainerAge(video, nowSeconds) < 30 * 60;
export const newestExplainerFirst = (videos: ExplainerVideo[]) => [...videos].sort((a, b) => Number(b.created_at || 0) - Number(a.created_at || 0));
/** Videos for the home page task list: still generating, or finished in the last day. */
export function recentExplainerVideos(videos: ExplainerVideo[], nowSeconds = Date.now() / 1000, limit = 3) {
  return newestExplainerFirst(videos)
    .filter(video => isExplainerRunning(video) ? isExplainerActive(video, nowSeconds) : (video.status === 'completed' || video.status === 'failed') && explainerAge(video, nowSeconds) < 24 * 3600)
    .slice(0, limit);
}
export const stageIndex = (stage: unknown) => explainerStages.findIndex(([key]) => key === stage);
export function formatClock(seconds: number) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}
/** Segment start times in seconds: explicit `start`, otherwise accumulated durations. */
export function segmentStarts(script: ExplainerScript | null | undefined) {
  let cursor = 0;
  return (script?.segments || []).map(segment => {
    const start = typeof segment.start === 'number' ? segment.start : cursor;
    cursor = start + (Number(segment.duration) || 0);
    return start;
  });
}
export function ttsChoices(config: Json = {}) {
  const labels: Record<string, string> = {
    edge_tts: `Edge-TTS${config.tts_voice_zh ? ' · ' + config.tts_voice_zh : ''}`,
    cloud_tts: `云端语音${config.cloud_tts_voice ? ' · ' + config.cloud_tts_voice : ''}`,
    auto: '自动（已配置云端语音则用云端，否则 Edge-TTS）',
    custom_tts_api: `自定义 TTS${config.custom_tts_api_speaker ? ' · ' + config.custom_tts_api_speaker : ''}`,
  };
  return { value: 'auto', options: ttsProviders.map(value => ({ value, label: labels[value] })) };
}
export function placementOf(video: Pick<ExplainerVideo, 'placements'> | null | undefined, projectId: string, slideId: string | null | undefined) {
  return video?.placements?.find(item => item.project_id === projectId && item.slide_id === slideId) || null;
}
export function sourceLabel(video: Pick<ExplainerVideo, 'source_type' | 'source_ref'>) {
  const ref = video.source_ref || {};
  if (video.source_type === 'slide') return `PPT 页面${ref.project_title ? ' · ' + ref.project_title : ''}${ref.slide_title ? ' · ' + ref.slide_title : ''}`;
  if (video.source_type === 'upload') return `上传文件${ref.filename ? ' · ' + ref.filename : ''}${Number.isInteger(ref.section_index) ? ` · 第 ${ref.section_index + 1} 段` : ''}`;
  return '直接描述';
}
export function placementLabel(video: Pick<ExplainerVideo, 'placements'>) {
  const placements = video.placements || [];
  if (!placements.length) return '未加入 PPT';
  return placements.some(item => item.mode === 'replace') ? '已替换 PPT 页面' : '已作为新页加入 PPT';
}
export function buildCreateBody(input: { source: ExplainerSource; extra?: string; options: ExplainerOptions; placement?: PlacementMode }): ExplainerCreateBody {
  const source = input.source.type === 'text' ? { ...input.source, title: input.source.title.trim(), text: input.source.text.trim() } : input.source;
  return { source, extra: String(input.extra || '').trim(), options: input.options, placement: { mode: input.placement || 'none' } };
}

function errorText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(errorText).join('；');
  if (value && typeof value === 'object') return String((value as Json).msg || (value as Json).message || JSON.stringify(value));
  return '请求失败，请稍后重试';
}

export function createExplainerApi(fetcher: typeof fetch = (...args) => fetch(...args)) {
  async function call(url: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<Json> {
    const form = typeof FormData !== 'undefined' && body instanceof FormData;
    const response = await fetcher(url, {
      method, credentials: 'same-origin', signal,
      headers: { Accept: 'application/json', ...(body === undefined || form ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : form ? body as FormData : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) throw new Error(errorText(data.detail || data.error || data.message || `请求失败（${response.status}）`));
    return data;
  }
  const base = '/api/explainer-videos', path = (id: string) => `${base}/${encodeURIComponent(id)}`;
  return {
    parseUpload(file: File, signal?: AbortSignal) { const body = new FormData(); body.append('file', file); return call(`${base}/parse-upload`, 'POST', body, signal) as Promise<ParsedUpload>; },
    create: (body: ExplainerCreateBody) => call(base, 'POST', body) as Promise<{ video_id: string; task_id: string }>,
    get: (id: string, signal?: AbortSignal) => call(path(id), 'GET', undefined, signal) as Promise<ExplainerVideo>,
    async list(projectId?: string, signal?: AbortSignal) {
      const data = await call(base + (projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''), 'GET', undefined, signal);
      return (Array.isArray(data) ? data : data.videos || data.items || []) as ExplainerVideo[];
    },
    place: (id: string, body: { project_id: string; after_slide_id?: string; replace_slide_id?: string }) => call(`${path(id)}/place`, 'POST', body) as Promise<{ slide_id: string; slide_index: number }>,
    restore: (id: string, body: { project_id: string; slide_id: string }) => call(`${path(id)}/restore`, 'POST', body),
    regenerate: (id: string, script?: ExplainerScript) => call(`${path(id)}/regenerate`, 'POST', script ? { script } : {}) as Promise<{ video_id?: string; task_id?: string }>,
    // Existing background-task polling endpoint; the stage is published in task metadata.
    task: (taskId: string, signal?: AbortSignal) => call(`/api/landppt/tasks/${encodeURIComponent(taskId)}`, 'GET', undefined, signal),
  };
}
export type ExplainerApi = ReturnType<typeof createExplainerApi>;

export type TrackerState = { phase: 'idle' | 'running' | 'completed' | 'failed'; videoId: string; taskId: string; stage: string; percent: number; message: string; video: ExplainerVideo | null; error: string };
const idle: TrackerState = { phase: 'idle', videoId: '', taskId: '', stage: '', percent: 0, message: '', video: null, error: '' };

/** Follows one video's generation task until the video is completed or failed. */
export function createExplainerTracker(api: ExplainerApi, timings = { poll: 2000 }) {
  let state = idle, run = 0, timer: ReturnType<typeof setTimeout> | undefined, active: AbortController | undefined;
  const listeners = new Set<(value: TrackerState) => void>();
  const set = (next: Partial<TrackerState>) => { state = { ...state, ...next }; listeners.forEach(listener => listener(state)); };
  function stop() { run++; clearTimeout(timer); active?.abort(); }
  function progressOf(stage: string, raw: unknown) {
    const reported = Number(raw);
    if (Number.isFinite(reported) && reported > 0) return Math.round(Math.max(0, Math.min(100, reported)));
    const index = stageIndex(stage);
    return index < 0 ? 0 : Math.round(index / explainerStages.length * 100);
  }
  async function poll(token: number) {
    if (token !== run) return;
    active = new AbortController();
    const signal = active.signal;
    try {
      let status: string, stage: string, percent: number, message = '', error = '';
      if (state.taskId) {
        const task = await api.task(state.taskId, signal);
        status = task.status; stage = String(task.metadata?.stage || task.stage || state.stage);
        percent = progressOf(stage, task.progress); message = String(task.message || '');
        error = errorText(task.error || task.result?.error || '');
      } else {
        const video = await api.get(state.videoId, signal);
        status = video.status; stage = String(video.stage || state.stage); percent = progressOf(stage, 0); error = String(video.error || '');
        if (status === 'completed') { if (token === run) set({ phase: 'completed', stage: 'render', percent: 100, video, error: '' }); return; }
      }
      if (token !== run) return;
      if (status === 'failed') { set({ phase: 'failed', error: error || '讲解视频生成失败' }); return; }
      if (status === 'completed') {
        const video = await api.get(state.videoId, signal);
        if (token !== run) return;
        if (video.status === 'failed') set({ phase: 'failed', error: String(video.error || '讲解视频生成失败'), video });
        else set({ phase: 'completed', stage: 'render', percent: 100, video, error: '' });
        return;
      }
      set({ stage, percent, message });
      timer = setTimeout(() => void poll(token), timings.poll);
    } catch (reason) {
      if (token === run && !signal.aborted) set({ phase: 'failed', error: reason instanceof Error ? reason.message : String(reason) });
    }
  }
  function track(videoId: string, taskId = '', video: ExplainerVideo | null = null) {
    stop();
    set({ ...idle, phase: 'running', videoId, taskId, video, stage: 'parse' });
    void poll(run);
  }
  return {
    getSnapshot: () => state,
    subscribe(listener: (value: TrackerState) => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    async start(body: ExplainerCreateBody) {
      stop(); set({ ...idle, phase: 'running', stage: 'parse' });
      const token = run;
      try { const created = await api.create(body); if (token === run) track(created.video_id, created.task_id); }
      catch (reason) { if (token === run) set({ phase: 'failed', error: reason instanceof Error ? reason.message : String(reason) }); }
    },
    track,
    async regenerate(script?: ExplainerScript) {
      const { videoId, video } = state; stop();
      set({ phase: 'running', stage: script ? 'tts' : 'parse', percent: 0, message: '', error: '' });
      const token = run;
      try { const result = await api.regenerate(videoId, script); if (token === run) track(result.video_id || videoId, result.task_id || '', video); }
      catch (reason) { if (token === run) set({ phase: 'failed', error: reason instanceof Error ? reason.message : String(reason) }); }
    },
    reset() { stop(); set(idle); },
    dispose() { stop(); listeners.clear(); },
  };
}
export type ExplainerTracker = ReturnType<typeof createExplainerTracker>;
