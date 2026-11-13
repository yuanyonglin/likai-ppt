// Explicit port to the established editing engines. Presentation data remains
// owned by those engines; application controls never infer it from hidden DOM.
(() => {
    const listeners = new Set();
    const documentKeys = new WeakMap(), stableKeys = new Map();
    let sequence = 0, queued = false, aiOpen = false, aiWidth = 480, saving = null;
    const originals = new Map();
    // 讲解视频页以 HTML 上的 data-explainer-video-id 为准（EXPLAINER_VIDEO_CONTRACT.md §1）
    const videoIdOf = html => (typeof html === 'string' ? /data-explainer-video-id\s*=\s*["']([^"']+)["']/.exec(html) : null)?.[1] || null;
    const commands = new Set([
        'setMode', 'selectSlide', 'saveSlide', 'toggleQuickEditMode', 'startSlideshow', 'insertNewSlide',
        'openAIEditSidebar', 'closeAIEditSidebar', 'showSlideOutline', 'autoRepairSlideLayout',
        'autoGenerateSlideImages', 'quickRegenerateSlide', 'regenerateSelectedSlides', 'regenerateAllSlides',
        'showChangeTemplateDialog', 'showShareDialog', 'showSpeechScriptDialog', 'openNativeChatDialog', 'deleteSlide',
        'exportSlidesToPptxClient', 'exportSvgPagesToPptx', 'exportToPPTX', 'exportToPPTXAsImages',
        'exportToPDF', 'downloadHTML', 'applyMainFrameScale', 'applyThumbnailPreviewScaleToAll', 'resizeCodeMirror', 'refreshQuickAiPopoverLayout',
    ]);
    function pages() {
        const counts = new Map();
        slidesData.forEach(slide => { if (slide.slide_id) counts.set(slide.slide_id, (counts.get(slide.slide_id) || 0) + 1); });
        const result = slidesData.map((slide, index) => {
            if (!documentKeys.has(slide)) documentKeys.set(slide, counts.get(slide.slide_id) === 1 && stableKeys.get(slide.slide_id) || `editor-page-${++sequence}`);
            const outline = projectOutline?.slides?.[index];
            return { key: documentKeys.get(slide), slideId: slide.slide_id || null, title: slide.title || '', slideType: outline?.slide_type || outline?.type || slide.slide_type || slide.type || 'content' };
        });
        stableKeys.clear();
        result.forEach(page => { if (counts.get(page.slideId) === 1) stableKeys.set(page.slideId, page.key); });
        return result;
    }
    function read() {
        const svgExport = editorExportTargetsAllSvg();
        return {
            mode: currentMode, current: currentSlideIndex, count: slidesData.length,
            title: slidesData[currentSlideIndex]?.title || '', selected: selectedSlideIndices.size,
            currentSlideId: slidesData[currentSlideIndex]?.slide_id || null, currentVideoId: videoIdOf(slidesData[currentSlideIndex]?.html_content),
            documentPages: pages(), previewPages: slidesData.map(slide => ({ title: slide.title, html: slide.html_content })), svg: currentSlideIsSvg(), saving: !!saving,
            sidebar: window.LandPPTEditorLayout.read(), aiOpen, aiWidth,
            packageContent: !!window.LandPPTPackageEditor?.available,
            nativeExport: svgExport, vectorExport: svgExport, clientExport: !svgExport,
        };
    }
    function changed() {
        if (queued || !listeners.size) return;
        queued = true;
        queueMicrotask(() => { queued = false; listeners.forEach(listener => listener(read())); });
    }
    function observeEngines() {
        const mutations = ['setMode', 'selectSlide', 'updateSelectedSlidesUI', 'refreshSidebar',
            'updateFunctionButtonsState', 'openAIEditSidebar', 'closeAIEditSidebar', 'saveSlide'];
        mutations.forEach(name => {
            const original = window[name];
            if (typeof original !== 'function') return;
            const wrapped = function (...args) {
                if (name === 'saveSlide' && saving) return saving;
                let result;
                try { result = original.apply(this, args); }
                finally {
                    if (name === 'openAIEditSidebar') aiOpen = true;
                    if (name === 'closeAIEditSidebar') aiOpen = false;
                    changed();
                }
                if (result?.then) {
                    const pending = result.then(value => { changed(); return value; }, error => { changed(); throw error; });
                    if (name === 'saveSlide') {
                        saving = pending.finally(() => { saving = null; changed(); }); changed(); return saving;
                    }
                    return pending;
                }
                return result;
            };
            originals.set(name, { original, wrapped }); window[name] = wrapped;
        });
    }
    window.LandPPTEditorEngine = {
        read,
        subscribe(listener) {
            if (!listeners.size) observeEngines();
            listeners.add(listener);
            const layout = window.LandPPTEditorLayout.subscribe(changed);
            return () => {
                listeners.delete(listener); layout();
                if (!listeners.size) {
                    originals.forEach(({ original, wrapped }, name) => { if (window[name] === wrapped) window[name] = original; });
                    originals.clear();
                }
            };
        },
        changed,
        setAssistantWidth(width) { if (Number.isFinite(width) && width > 0) { aiWidth = width; changed(); } },
        call(name, ...args) {
            if (name === 'toggleSidebar') return window.LandPPTEditorLayout.toggle();
            if (name === 'openPackageEditor') return window.LandPPTPackageEditor?.open();
            if (name === 'insertNewSlide') contextMenuSlideIndex = Math.max(-1, currentSlideIndex);
            if (name === 'deleteSlide') contextMenuSlideIndex = currentSlideIndex;
            if (!commands.has(name) || typeof window[name] !== 'function') throw new Error(`编辑操作不可用：${name}`);
            return window[name](...args);
        },
        applyDocument(key, snapshot) {
            const index = pages().findIndex(page => page.key === key);
            if (index < 0 || index !== snapshot.slide_index) return;
            const slide = slidesData[index];
            if (slide.slide_id && snapshot.slide_id !== slide.slide_id) return;
            if (snapshot.slide_id) slide.slide_id = snapshot.slide_id;
            if (!projectOutline) projectOutline = { slides: [] };
            if (!Array.isArray(projectOutline.slides)) projectOutline.slides = [];
            projectOutline.slides[index] = snapshot.outline; window.projectOutline = projectOutline;
            if (snapshot.outline.title) slide.title = snapshot.outline.title;
            ['slide_type', 'content_points', 'description'].forEach(field => { if (field in snapshot.outline) slide[field] = snapshot.outline[field]; });
            const title = document.querySelector(`.slide-thumbnail[data-slide-index="${index}"] .slide-title`);
            if (title) { const badge = title.querySelector('.slide-mode-badge'); title.textContent = `${index + 1}. ${slide.title}`; if (badge) title.appendChild(badge); }
            changed();
        },
        openSpeechDocument(language, preview) {
            currentSpeechLanguage = language;
            const selector = document.getElementById('speechLanguage'); if (selector) selector.value = language;
            if (preview) showCurrentSpeechScripts(); else showSpeechScriptDialog();
        },
    };
})();
