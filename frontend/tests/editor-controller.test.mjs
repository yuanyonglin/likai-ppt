import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createEditorController } from '../src/controllers/editor-controller.ts';

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
function setup(t) {
  const engineListeners = new Set(), surfaceListeners = new Set(), calls = [], updates = [];
  const core = { current: 0, count: 2, documentPages: [] };
  let action = () => true;
  const engine = { read: () => ({ ...core }), subscribe: fn => { engineListeners.add(fn); return () => engineListeners.delete(fn); }, call: (...args) => { calls.push(args); return action(...args); }, applyDocument() {}, openSpeechDocument() {} };
  const surfaces = { read: () => ({ dialogs: [], toasts: [] }), subscribe: fn => { surfaceListeners.add(fn); return () => surfaceListeners.delete(fn); } };
  const controller = createEditorController(engine, surfaces); controller.subscribe(value => updates.push(value));
  t.after(() => controller.dispose());
  return { controller, engineListeners, surfaceListeners, calls, updates, core, action: fn => { action = fn; }, emit: () => engineListeners.forEach(fn => fn({})) };
}
test('construction and repeated mounting acquire subscriptions exactly once', t => {
  const h = setup(t); assert.equal(h.engineListeners.size, 0); assert.equal(h.calls.length, 0);
  h.controller.mount(); h.controller.mount(); assert.equal(h.engineListeners.size, 1); assert.equal(h.surfaceListeners.size, 1);
  assert.equal(h.calls.length, 0);
});
test('engine changes update page selection without a DOM mutation', t => {
  const h = setup(t); h.controller.mount(); h.core.current = 1; h.emit(); assert.equal(h.controller.getSnapshot().current, 1);
});
test('document feedback updates directly and equal values do not loop', t => {
  const h = setup(t); h.controller.mount(); h.controller.setDocumentState({ documentPanel: 'outline', documentDirty: true });
  const count = h.updates.length; h.controller.setDocumentState({ documentPanel: 'outline', documentDirty: true });
  assert.equal(h.updates.length, count); assert.equal(h.controller.getSnapshot().documentDirty, true);
});
test('repeated Save shares one in-flight operation', async t => {
  const h = setup(t), pending = deferred(); h.action(() => pending.promise); h.controller.mount();
  const first = h.controller.save(), second = h.controller.save(); assert.equal(first, second);
  await Promise.resolve(); assert.deepEqual(h.calls, [['saveSlide']]); assert.equal(h.controller.getSnapshot().savePending, true);
  pending.resolve(true); assert.equal(await first, true); assert.equal(h.controller.getSnapshot().savePending, false);
});
test('document Save captures the active writer before panel changes', async t => {
  const h = setup(t); h.controller.mount(); let writes = 0;
  h.controller.saveDocument = async () => { writes++; }; const pending = h.controller.save(); h.controller.saveDocument = null;
  await pending; assert.equal(writes, 1); assert.equal(h.calls.length, 0);
});
test('a failed save exposes error and permits a deliberate retry', async t => {
  const h = setup(t); h.controller.mount(); h.action(() => false);
  assert.equal(await h.controller.save(), false); assert.match(h.controller.getSnapshot().commandError, /保存未完成/);
  h.action(() => true); assert.equal(await h.controller.save(), true); assert.equal(h.controller.getSnapshot().commandError, '');
});
test('suspension removes both subscriptions and ignores delayed callbacks', t => {
  const h = setup(t); h.controller.mount(); const late = [...h.surfaceListeners][0]; h.controller.suspend();
  assert.equal(h.engineListeners.size + h.surfaceListeners.size, 0); const updates = h.updates.length; late({});
  assert.equal(h.updates.length, updates); h.controller.call('selectSlide', 1); assert.equal(h.calls.length, 0);
});
test('cached resume reads state without replaying any editing action', t => {
  const h = setup(t); h.controller.mount(); h.controller.suspend(); h.core.current = 1; h.controller.resume(); h.controller.resume();
  assert.equal(h.controller.getSnapshot().current, 1); assert.equal(h.calls.length, 0); assert.equal(h.engineListeners.size, 1);
});
test('a save settling after cached resume releases the busy state', async t => {
  const h = setup(t), pending = deferred(); h.action(() => pending.promise); h.controller.mount();
  const saving = h.controller.save(); await Promise.resolve(); h.controller.suspend(); h.controller.resume(); pending.resolve(true); await saving;
  assert.equal(h.controller.getSnapshot().savePending, false);
});
test('async action errors are observed without an unhandled rejection', async t => {
  const h = setup(t); h.controller.mount(); h.action(() => Promise.reject(new Error('Unavailable')));
  assert.equal(await h.controller.call('openPackageEditor'), false); assert.equal(h.controller.getSnapshot().commandError, 'Unavailable');
});
test('disposed controllers cannot reacquire subscriptions', t => {
  const h = setup(t); h.controller.mount(); h.controller.dispose(); h.controller.resume();
  assert.equal(h.engineListeners.size + h.surfaceListeners.size, 0);
});

function nativeEngine() {
  const context = vm.createContext({ queueMicrotask });
  vm.runInContext(`window = globalThis;
    let slidesData = [{title:'First',slide_id:'one'},{title:'Second',slide_id:'two'}], currentSlideIndex=0, currentMode='preview', selectedSlideIndices=new Set();
    let saves = 0, finishSave, projectOutline = null;
    function editorExportTargetsAllSvg(){return false} function currentSlideIsSvg(){return false}
    function selectSlide(index){currentSlideIndex=index} function setMode(mode){currentMode=mode}
    function updateSelectedSlidesUI(){} function refreshSidebar(){} function updateFunctionButtonsState(){}
    function openAIEditSidebar(){} function closeAIEditSidebar(){}
    function saveSlide(){saves++; return new Promise(resolve=>finishSave=resolve)}
    LandPPTEditorLayout={read:()=>true,subscribe:()=>()=>{}};
  `, context);
  vm.runInContext(readFileSync(new URL('../../src/landppt/web/static/js/pages/project/slides_editor/projectSlidesEditor.engine.js', import.meta.url), 'utf8'), context);
  return context;
}
test('native commands publish changes and restore original engine functions on release', async () => {
  const context = nativeEngine(), engine = context.LandPPTEditorEngine, original = context.selectSlide;
  const snapshots = [], release = engine.subscribe(state => snapshots.push(state));
  engine.call('selectSlide', 1); await Promise.resolve(); assert.equal(snapshots.at(-1).current, 1);
  assert.notEqual(context.selectSlide, original); release(); assert.equal(context.selectSlide, original);
});
test('native port rejects unknown global commands', () => {
  const engine = nativeEngine().LandPPTEditorEngine;
  assert.throws(() => engine.call('fetch', '/unsafe'), /编辑操作不可用/);
});

test('workspace insertion uses the current slide rather than an old context menu target', () => {
  const context = nativeEngine(), engine = context.LandPPTEditorEngine;
  vm.runInContext('let contextMenuSlideIndex = 0; function insertNewSlide(){return contextMenuSlideIndex}', context);
  engine.call('selectSlide', 1);
  assert.equal(engine.call('insertNewSlide'), 1);
});
test('stable slide identifiers preserve document keys after snapshot replacement', () => {
  const context = nativeEngine(), engine = context.LandPPTEditorEngine, first = engine.read().documentPages[0].key;
  vm.runInContext("slidesData = [{title:'Changed',slide_id:'one'}]", context);
  assert.equal(engine.read().documentPages[0].key, first);
});

test('outline metadata provides chapter types without exposing document bodies', () => {
  const context = nativeEngine(), engine = context.LandPPTEditorEngine;
  vm.runInContext("projectOutline = { slides: [{slide_type:'transition', content_points:['private body']}] }; slidesData[1].slide_type='section'", context);
  const pages = engine.read().documentPages;
  assert.equal(pages[0].slideType, 'transition'); assert.equal(pages[1].slideType, 'section');
  assert.equal('content_points' in pages[0], false);
  assert.equal('html_content' in pages[0], false);
});
test('explainer video pages are identified by the html attribute and delete targets the current page', () => {
  const context = nativeEngine(), engine = context.LandPPTEditorEngine;
  vm.runInContext(`slidesData[1].html_content = '<div class="explainer-video-page" data-explainer-video-id="vid-1"><video></video></div>';
    let contextMenuSlideIndex = -1, deleted = null; function deleteSlide(){ deleted = contextMenuSlideIndex; }`, context);
  assert.equal(engine.read().currentVideoId, null); assert.equal(engine.read().currentSlideId, 'one');
  engine.call('selectSlide', 1);
  assert.equal(engine.read().currentVideoId, 'vid-1'); assert.equal(engine.read().currentSlideId, 'two');
  engine.call('deleteSlide'); assert.equal(vm.runInContext('deleted', context), 1);
});
test('native shortcut and toolbar Save cannot duplicate writes', async () => {
  const context = nativeEngine(), engine = context.LandPPTEditorEngine, release = engine.subscribe(() => {});
  const first = engine.call('saveSlide'), second = context.saveSlide(); assert.equal(first, second);
  assert.equal(vm.runInContext('saves', context), 1); vm.runInContext('finishSave(true)', context); await first;
  assert.equal(engine.read().saving, false); release();
});
