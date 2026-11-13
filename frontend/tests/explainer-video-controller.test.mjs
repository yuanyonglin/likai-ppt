import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildCreateBody, createExplainerApi, createExplainerTracker, explainerResultURL, formatClock, parseExplainerVideoId,
  isExplainerActive, newestExplainerFirst, recentExplainerVideos,
  placementLabel, placementOf, segmentStarts, sourceLabel, stageIndex, ttsChoices,
} from '../src/controllers/explainer-video-controller.ts';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check) {
  const start = Date.now();
  while (!check()) { if (Date.now() - start > 1500) throw new Error('condition did not settle'); await sleep(1); }
}
const json = (value, status = 200) => Response.json(value, { status });
const video = (extra = {}) => ({ id: 'v1', status: 'completed', title: '改善前后对比', source_type: 'slide', source_ref: { project_id: 'p1', slide_id: 's8' },
  script: { title: 'x', segments: [{ text: '一', duration: 10 }, { text: '二', duration: 12 }] }, video_url: '/api/explainer-videos/v1/video.mp4', placements: [], ...extra });

// Mock backend: routes keyed by "METHOD path"; responses may be arrays consumed in order.
function backend(routes) {
  const requests = [];
  const fetcher = async (url, init = {}) => {
    const method = init.method || 'GET';
    requests.push({ url, method, body: typeof init.body === 'string' ? JSON.parse(init.body) : init.body, headers: init.headers });
    const route = routes[`${method} ${url}`];
    if (!route) return json({ detail: `unexpected ${method} ${url}` }, 404);
    const value = Array.isArray(route) ? route.length > 1 ? route.shift() : route[0] : route;
    return value instanceof Response ? value : json(value);
  };
  return { requests, api: createExplainerApi(fetcher) };
}

test('video page id is read from the html attribute only', () => {
  assert.equal(parseExplainerVideoId('<div class="explainer-video-page" data-explainer-video-id="a-1"><video></video></div>'), 'a-1');
  assert.equal(parseExplainerVideoId("<div data-explainer-video-id='b2'>"), 'b2');
  assert.equal(parseExplainerVideoId('<div class="explainer-video-page">'), null);
  assert.equal(parseExplainerVideoId(undefined), null);
});

test('helpers format segments, stages, voices and placements', () => {
  assert.deepEqual(segmentStarts({ segments: [{ text: 'a', duration: 10 }, { text: 'b', duration: 5 }, { text: 'c', start: 30 }] }), [0, 10, 30]);
  assert.deepEqual(segmentStarts(null), []);
  assert.equal(formatClock(72.6), '1:12');
  assert.equal(stageIndex('tts'), 2); assert.equal(stageIndex('unknown'), -1);
  const voices = ttsChoices({ unattended_tts_provider: 'cloud_tts', cloud_tts_voice: 'male-qn-jingying' });
  assert.equal(voices.value, 'auto');
  assert.ok(voices.options.some(item => item.label === '云端语音 · male-qn-jingying'));
  assert.deepEqual(voices.options.map(item => item.value), ['auto', 'cloud_tts', 'edge_tts', 'custom_tts_api']);
  const placed = video({ placements: [{ project_id: 'p1', slide_id: 's8', mode: 'replace' }] });
  assert.equal(placementOf(placed, 'p1', 's8').mode, 'replace');
  assert.equal(placementOf(placed, 'p1', 's9'), null);
  assert.equal(placementLabel(placed), '已替换 PPT 页面');
  assert.equal(placementLabel(video()), '未加入 PPT');
  assert.equal(sourceLabel({ source_type: 'upload', source_ref: { filename: 'a.pptx', section_index: 6 } }), '上传文件 · a.pptx · 第 7 段');
});

test('create body follows the contract shape', () => {
  const options = { duration: '2m', tts_provider: 'cloud_tts', voice: null, style: 'follow_ppt', subtitles: true };
  assert.deepEqual(buildCreateBody({ source: { type: 'text', title: ' 主题 ', text: ' 内容 ' }, extra: '  面向班组长 ', options }),
    { source: { type: 'text', title: '主题', text: '内容' }, extra: '面向班组长', options, placement: { mode: 'none' } });
  assert.deepEqual(buildCreateBody({ source: { type: 'slide', project_id: 'p1', slide_id: 's8' }, options, placement: 'replace' }).placement, { mode: 'replace' });
});

test('api sends multipart uploads and json bodies to contract endpoints', async () => {
  const { api, requests } = backend({
    'POST /api/explainer-videos/parse-upload': { file_id: 'f1', filename: 'a.pptx', sections: [{ index: 0, title: '封面', text: '' }] },
    'POST /api/explainer-videos/v1/place': { slide_id: 's9', slide_index: 8 },
    'GET /api/explainer-videos?project_id=p%201': { videos: [video()] },
    'POST /api/explainer-videos/v1/restore': { success: false, detail: '没有备份' },
  });
  const parsed = await api.parseUpload(new File(['x'], 'a.pptx'));
  assert.equal(parsed.sections[0].title, '封面');
  assert.ok(requests[0].body instanceof FormData);
  assert.equal(requests[0].headers['Content-Type'], undefined);
  assert.deepEqual(await api.place('v1', { project_id: 'p1', after_slide_id: 's8' }), { slide_id: 's9', slide_index: 8 });
  assert.deepEqual(requests[1].body, { project_id: 'p1', after_slide_id: 's8' });
  assert.equal((await api.list('p 1'))[0].id, 'v1');
  await assert.rejects(api.restore('v1', { project_id: 'p1', slide_id: 's8' }), /没有备份/);
});

test('tracker follows task stages and loads the finished video', async t => {
  const { api, requests } = backend({
    'POST /api/explainer-videos': { video_id: 'v1', task_id: 't1' },
    'GET /api/landppt/tasks/t1': [
      { status: 'running', progress: 0, metadata: { stage: 'script' } },
      { status: 'running', progress: 64, metadata: { stage: 'tts' }, message: '逐段配音' },
      { status: 'completed', progress: 100 },
    ],
    'GET /api/explainer-videos/v1': video(),
  });
  const tracker = createExplainerTracker(api, { poll: 1 });
  t.after(() => tracker.dispose());
  const seen = [];
  tracker.subscribe(state => seen.push({ phase: state.phase, stage: state.stage, percent: state.percent }));
  await tracker.start(buildCreateBody({ source: { type: 'slide', project_id: 'p1', slide_id: 's8' }, options: { duration: '1m', tts_provider: 'edge_tts', voice: null, style: 'follow_ppt', subtitles: true } }));
  await until(() => tracker.getSnapshot().phase === 'completed');
  assert.equal(requests[0].body.source.slide_id, 's8');
  assert.ok(seen.some(item => item.stage === 'script' && item.percent === 20));
  assert.ok(seen.some(item => item.stage === 'tts' && item.percent === 64));
  const final = tracker.getSnapshot();
  assert.equal(final.video.id, 'v1'); assert.equal(final.percent, 100);
});

test('tracker reports failures from the task and from create', async t => {
  const { api } = backend({
    'POST /api/explainer-videos': [json({ detail: '额度不足' }, 402), { video_id: 'v2', task_id: 't2' }],
    'GET /api/landppt/tasks/t2': { status: 'failed', error: '配音服务不可用' },
  });
  const tracker = createExplainerTracker(api, { poll: 1 });
  t.after(() => tracker.dispose());
  const body = buildCreateBody({ source: { type: 'text', title: 'a', text: 'b' }, options: { duration: '1m', tts_provider: 'edge_tts', voice: null, style: 'follow_ppt', subtitles: false } });
  await tracker.start(body);
  assert.equal(tracker.getSnapshot().phase, 'failed'); assert.equal(tracker.getSnapshot().error, '额度不足');
  await tracker.start(body);
  await until(() => tracker.getSnapshot().phase === 'failed');
  assert.equal(tracker.getSnapshot().error, '配音服务不可用');
});

test('tracker resumes from the video record and regenerates with an edited script', async t => {
  const { api, requests } = backend({
    'GET /api/explainer-videos/v1': [video({ status: 'running', stage: 'animation' }), video(), video({ title: '新稿' })],
    'POST /api/explainer-videos/v1/regenerate': { task_id: 't3' },
    'GET /api/landppt/tasks/t3': { status: 'completed' },
  });
  const tracker = createExplainerTracker(api, { poll: 1 });
  t.after(() => tracker.dispose());
  tracker.track('v1');
  await until(() => tracker.getSnapshot().phase === 'completed');
  const script = { segments: [{ text: '改过的第一段' }] };
  await tracker.regenerate(script);
  await until(() => tracker.getSnapshot().phase === 'completed' && tracker.getSnapshot().video.title === '新稿');
  assert.deepEqual(requests.find(item => item.url.endsWith('/regenerate')).body, { script });
});

test('a newer run cancels the previous poll loop', async t => {
  let polls = 0;
  const { api } = backend({ 'GET /api/explainer-videos/old': video({ id: 'old', status: 'running' }), 'GET /api/explainer-videos/new': video({ id: 'new' }) });
  const original = api.get; api.get = (id, signal) => { if (id === 'old') polls++; return original(id, signal); };
  const tracker = createExplainerTracker(api, { poll: 1 });
  t.after(() => tracker.dispose());
  tracker.track('old');
  await until(() => polls >= 2);
  tracker.track('new');
  await until(() => tracker.getSnapshot().phase === 'completed');
  const settled = polls; await sleep(10);
  assert.equal(polls, settled);
  assert.equal(tracker.getSnapshot().video.id, 'new');
});

test('home page lists running videos and ones finished within a day, newest first', () => {
  const now = 1_000_000;
  const row = (id, status, created, updated = created) => ({ id, status, title: id, source_type: 'text', source_ref: {}, created_at: created, updated_at: updated });
  const list = [
    row('old-done', 'completed', now - 2 * 86400),
    row('done', 'completed', now - 600),
    row('failed', 'failed', now - 300),
    row('running', 'running', now - 60, now - 10),
    row('stuck', 'running', now - 7200, now - 3600), // interrupted by a restart
    row('pending', 'pending', now - 5),
  ];
  assert.deepEqual(recentExplainerVideos(list, now).map(video => video.id), ['pending', 'running', 'failed']);
  assert.deepEqual(recentExplainerVideos(list, now, 10).map(video => video.id), ['pending', 'running', 'failed', 'done']);
  assert.equal(explainerResultURL('a b'), '/dashboard?kind=video&explainer=a%20b');
  assert.deepEqual(newestExplainerFirst(list).map(video => video.id), ['pending', 'running', 'failed', 'done', 'stuck', 'old-done']);
  assert.deepEqual(list.filter(video => isExplainerActive(video, now)).map(video => video.id), ['running', 'pending']);
});
