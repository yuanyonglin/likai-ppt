// Presentation contents, frame scaling and cache stay outside React.
// React owns visible controls; this controller publishes snapshots and actions.
(() => {
    'use strict';
    const config = JSON.parse(document.getElementById('antdData').textContent);
    let lastUpdatedAt = Number(config.updatedAt) || 0;
    let panel = null, uiHidden = false, loading = false, subtitle = '';
    let refreshInterval = null, refreshBusy = false, disposed = false;
    let pendingSlideIndex = null, cacheEpoch = 0;
    function getState() {
        return { index: currentSlideIndex, slides: slidesData, total: totalSlides,
            autoPlaying: isAutoPlaying || narrationEnabled, fullscreen: isFullscreenMode,
            narration: narrationEnabled, language: narrationLanguage, subtitle,
            panel, uiHidden, loading, transitioning: isTransitioning, cache: getCacheStats() };
    }
    function emitState() {
        if (!disposed) window.dispatchEvent(new CustomEvent('landppt:presentation-state', { detail: getState() }));
    }
    function setPanel(value) {
        panel = value;
        if (panel) { setUiHidden(false); clearTimeout(uiHideTimer); }
        else scheduleUiHide();
        emitState();
    }
    function holdUi(hold) {
        uiHoverCount = Math.max(0, uiHoverCount + (hold ? 1 : -1));
        if (hold) { setUiHidden(false); clearTimeout(uiHideTimer); }
        else scheduleUiHide();
    }
    function isInteractiveTarget(target) {
        return target?.nodeType === 1 && !!target.closest('button,a,input,select,textarea,[role="combobox"],[role="dialog"],[role="menu"],.ant-select-dropdown,[contenteditable="true"]');
    }
    function changeLanguage(value) {
        if (!narrationAvailableLanguages.includes(value) || value === narrationLanguage) return;
        const wasPlaying = narrationEnabled;
        stopNarration();
        narrationLanguage = value;
        narrationCuesCache.clear();
        narrationCurrentCues = [];
        narrationCurrentCueIndex = 0;
        setNarrationSubtitle('');
        if (wasPlaying) { narrationEnabled = true; playNarrationForSlide(currentSlideIndex); }
        emitState();
    }
    // 全局变量
    let currentSlideIndex = 0;
    let totalSlides = config.slidesCount;
    let slidesData = config.slidesData;
    let autoPlayInterval = null;
    let isAutoPlaying = false;


    const SLIDE_BASE_WIDTH = 1280;
    const SLIDE_BASE_HEIGHT = 720;

    let slideDisplayResizeObserver = null;

    // UI auto-hide (avoid overlays blocking slides)
    const UI_AUTO_HIDE_MS = 2200;
    let uiHideTimer = null;
    let uiHoverCount = 0;

    function setUiHidden(hidden) {
        if (panel || uiHoverCount > 0 || document.activeElement?.closest('.presentation-toolbar') && document.activeElement.matches(':focus-visible')) hidden = false;
        document.body.classList.toggle('ui-hidden', Boolean(hidden));
        uiHidden = Boolean(hidden);
        emitState();
    }

    function scheduleUiHide() {
        if (uiHideTimer) clearTimeout(uiHideTimer);
        uiHideTimer = setTimeout(() => {
            if (uiHoverCount > 0) return;
            if (panel) return;
            setUiHidden(true);
        }, UI_AUTO_HIDE_MS);
    }

    function revealUiTemporarily() {
        setUiHidden(false);
        scheduleUiHide();
    }

    function bindUiAutoHide() {
        document.addEventListener('mousemove', revealUiTemporarily, { passive: true });
        document.addEventListener('mousedown', revealUiTemporarily, { passive: true });
        document.addEventListener('touchstart', revealUiTemporarily, { passive: true });
        document.addEventListener('keydown', revealUiTemporarily, { passive: true });
        setUiHidden(false);
        scheduleUiHide();
    }

    function getSlideFrameBaseSize(frame) {
        if (!frame || !frame.contentDocument) {
            return { width: SLIDE_BASE_WIDTH, height: SLIDE_BASE_HEIGHT };
        }

        try {
            const doc = frame.contentDocument;
            const body = doc.body;
            const html = doc.documentElement;
            const bodyStyle = body ? getComputedStyle(body) : null;
            const htmlStyle = html ? getComputedStyle(html) : null;
            const bodyRect = body ? body.getBoundingClientRect() : null;
            const htmlRect = html ? html.getBoundingClientRect() : null;
            const width = parseFloat(bodyStyle?.width || '0')
                || parseFloat(htmlStyle?.width || '0')
                || bodyRect?.width
                || htmlRect?.width
                || SLIDE_BASE_WIDTH;
            const height = parseFloat(bodyStyle?.height || '0')
                || parseFloat(htmlStyle?.height || '0')
                || bodyRect?.height
                || htmlRect?.height
                || SLIDE_BASE_HEIGHT;
            return {
                width: width > 0 ? width : SLIDE_BASE_WIDTH,
                height: height > 0 ? height : SLIDE_BASE_HEIGHT,
            };
        } catch (e) {
            return { width: SLIDE_BASE_WIDTH, height: SLIDE_BASE_HEIGHT };
        }
    }

    function buildUnionBox(elements, baseSize, padding = 0) {
        if (!elements || elements.length === 0) return null;
        let left = Infinity;
        let top = Infinity;
        let right = -Infinity;
        let bottom = -Infinity;

        elements.forEach((el) => {
            const rect = el.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) return;
            left = Math.min(left, rect.left);
            top = Math.min(top, rect.top);
            right = Math.max(right, rect.right);
            bottom = Math.max(bottom, rect.bottom);
        });

        if (!Number.isFinite(left) || !Number.isFinite(top) || !Number.isFinite(right) || !Number.isFinite(bottom)) {
            return null;
        }

        const clampedLeft = Math.max(0, left - padding);
        const clampedTop = Math.max(0, top - padding);
        const clampedRight = Math.min(baseSize.width, right + padding);
        const clampedBottom = Math.min(baseSize.height, bottom + padding);

        return {
            left: clampedLeft,
            top: clampedTop,
            width: Math.max(1, clampedRight - clampedLeft),
            height: Math.max(1, clampedBottom - clampedTop),
        };
    }

    function collectMeaningfulSlideElements(elements, baseSize) {
        const baseArea = Math.max(1, baseSize.width * baseSize.height);
        return (elements || []).filter((el) => {
            if (!el || !el.getBoundingClientRect) return false;
            const rect = el.getBoundingClientRect();
            if (!rect || rect.width < 24 || rect.height < 24) return false;

            const style = getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') < 0.05) {
                return false;
            }

            const area = rect.width * rect.height;
            if (area >= baseArea * 0.95) return false;

            const tag = (el.tagName || '').toUpperCase();
            if (['HEADER', 'MAIN', 'FOOTER', 'SECTION', 'ARTICLE', 'ASIDE', 'NAV'].includes(tag)) {
                return true;
            }

            const textLength = ((el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim()).length;
            const hasText = textLength >= 12;
            const hasMedia = !!el.querySelector('img, svg, canvas, video, picture');
            const className = typeof el.className === 'string' ? el.className : '';
            const nameHint = `${el.id || ''} ${className}`.toLowerCase();
            const looksDecorative = /(grid-bg|energy-line|background|backdrop|glow|noise|particle|spark|beam|halo|flare|ornament|decor)/.test(nameHint)
                && !hasText
                && !hasMedia;

            if (looksDecorative) return false;

            const hasStructuralHint = /(container|content|card|panel|box|hero|title|subtitle|footer|header|objective|presenter|agenda|summary|layout|column|row|grid|list|chart)/.test(nameHint);
            if (hasStructuralHint && area <= baseArea * 0.9) return true;
            if (hasMedia && area >= baseArea * 0.005) return true;
            return hasText && area >= baseArea * 0.004;
        });
    }

    function getAdaptiveContentBox(frame, baseSize) {
        if (!frame || !frame.contentDocument) return null;

        try {
            const doc = frame.contentDocument;
            const body = doc.body;
            if (!body) return null;

            const directCandidates = collectMeaningfulSlideElements(Array.from(body.children || []), baseSize);
            let contentBox = buildUnionBox(directCandidates, baseSize, 28);

            const looksLikeFullCanvas = !contentBox
                || (
                    contentBox.width >= baseSize.width * 0.96
                    && contentBox.height >= baseSize.height * 0.96
                );

            if (looksLikeFullCanvas) {
                const descendantPool = Array.from(
                    body.querySelectorAll('header, main, footer, section, article, aside, nav, img, svg, canvas, video, picture, [class], [id]')
                ).slice(0, 180);
                const descendantCandidates = collectMeaningfulSlideElements(descendantPool, baseSize);
                const descendantBox = buildUnionBox(descendantCandidates, baseSize, 18);
                if (descendantBox) {
                    contentBox = descendantBox;
                }
            }

            if (!contentBox) return null;

            const baseArea = Math.max(1, baseSize.width * baseSize.height);
            const contentArea = contentBox.width * contentBox.height;
            const isEffectivelyFullSlide = contentArea >= baseArea * 0.94
                && contentBox.width >= baseSize.width * 0.98
                && contentBox.height >= baseSize.height * 0.95;

            return isEffectivelyFullSlide ? null : contentBox;
        } catch (e) {
            return null;
        }
    }

    function applySlideFrameScale(frame) {
        if (!frame) return;
        const container = frame.parentElement || document.querySelector('.slide-display');
        if (!container) return;

        const rect = container.getBoundingClientRect();
        const containerWidth = rect && rect.width ? rect.width : container.clientWidth;
        const containerHeight = rect && rect.height ? rect.height : container.clientHeight;
        const baseSize = getSlideFrameBaseSize(frame);
        const isFullscreen = document.body.classList.contains('fullscreen-mode');
        const targetBox = isFullscreen
            ? (getAdaptiveContentBox(frame, baseSize) || { left: 0, top: 0, width: baseSize.width, height: baseSize.height })
            : { left: 0, top: 0, width: baseSize.width, height: baseSize.height };
        const scaleX = containerWidth / targetBox.width;
        const scaleY = containerHeight / targetBox.height;
        const scale = Math.min(scaleX, scaleY);
        const left = (containerWidth - targetBox.width * scale) / 2 - targetBox.left * scale;
        const top = (containerHeight - targetBox.height * scale) / 2 - targetBox.top * scale;

        frame.style.width = `${baseSize.width}px`;
        frame.style.height = `${baseSize.height}px`;
        frame.style.left = `${left}px`;
        frame.style.top = `${top}px`;
        frame.style.transform = `scale(${scale})`;
        frame.style.transformOrigin = 'top left';
        frame.style.willChange = 'transform, top, left, opacity';
    }

    function scheduleSlideFrameScale(frame = currentVisibleFrame) {
        if (!frame) return;
        applySlideFrameScale(frame);
        requestAnimationFrame(() => {
            if (frame.isConnected) {
                applySlideFrameScale(frame);
            }
        });
    }

    function applySlideIframeOverrides(frame) {
        if (!frame) return;
        try {
            const doc = frame.contentDocument;
            if (!doc) return;

            // Forward pointer activity from inside iframe to the parent UI auto-hide controller.
            // (Mouse/touch events inside an iframe don't bubble to the parent document.)
            try {
                const flag = 'lpUiAutoHideBound';
                if (!doc.documentElement.dataset[flag]) {
                    doc.documentElement.dataset[flag] = '1';
                    doc.addEventListener('mousemove', () => revealUiTemporarily(), { passive: true });
                    doc.addEventListener('mousedown', () => revealUiTemporarily(), { passive: true });
                    doc.addEventListener('touchstart', () => revealUiTemporarily(), { passive: true });
                    doc.addEventListener('keydown', handlePresentationKey, true);
                }
            } catch (e) {
                // ignore
            }
        } catch (e) {
            // Ignore cross-origin or document access errors (should be same-origin for srcdoc).
        }
    }

    window.addEventListener('resize', function () {
        if (currentVisibleFrame) {
            scheduleSlideFrameScale(currentVisibleFrame);
        }
    });

    // 讲解（音频 + 字幕）
    let narrationEnabled = false;
    let narrationAvailable = config.hasSpeechScripts;
    let narrationAudioEl = null;
    let narrationLanguage = 'zh';
    let narrationCuesCache = new Map(); // slide_index -> { duration_ms, cues[] }
    let narrationCurrentCues = [];
    let narrationCurrentCueIndex = 0;
    let narrationRevision = 0;
    let narrationSubtitleSyncRafId = 0;
    const narrationAvailableLanguages = config.narrationLanguages;
    const narrationIsSharedView = config.isShared;
    const narrationShareToken = config.shareToken;
    const narrationApiBase = (narrationIsSharedView && narrationShareToken)
        ? `/api/share/${encodeURIComponent(narrationShareToken)}`
        : `/api/projects/${encodeURIComponent(config.projectId)}`;

    // 缓存机制相关变量
    let slideFrameCache = new Map(); // 缓存已渲染的iframe: slideIndex -> iframe元素
    let currentVisibleFrame = null; // 当前显示的iframe
    let isTransitioning = false; // 是否正在切换中
    let loadingSlides = new Set(); // 正在加载的幻灯片索引
    let frameIdCounter = 0; // iframe ID计数器

    // 初始化演示
    document.addEventListener('DOMContentLoaded', function () {
        initializePresentation();
        updateControls();
        initNarration();
        bindUiAutoHide();

        // 只有非分享视图才启动定期检查数据更新
        // 分享视图数据已从数据库实时读取，无需定期刷新
        const display = document.querySelector('.slide-display');
        if (display && window.ResizeObserver) {
            slideDisplayResizeObserver = new ResizeObserver(() => {
                if (currentVisibleFrame) scheduleSlideFrameScale(currentVisibleFrame);
            });
            slideDisplayResizeObserver.observe(display);
        }

        const isSharedView = config.isShared;
        if (!isSharedView) {
            startDataRefreshCheck();
        }
    });

    // 初始化演示系统
    function initializePresentation() {
        // 加载第一张幻灯片
        loadSlideWithCache(0, true);

        // 预加载前几张幻灯片以提升体验
        setTimeout(() => {
            preloadAdjacentSlides(0);
        }, 1000);
    }

    function showNarrationToast(message, type = 'info') {
        window.dispatchEvent(new CustomEvent('landppt:presentation-message', { detail: { message: String(message), type } }));
    }

    function setNarrationSubtitle(text) {
        const content = (text || '').toString().replace(/\s+/g, ' ').trim();
        if (subtitle !== content) { subtitle = content; emitState(); }
    }

    function updateNarrationButton() { emitState();
    }

    async function loadNarrationCues(slideIndex) {
        const language = narrationLanguage;
        const key = `${language}:${Number(slideIndex)}`;
        if (narrationCuesCache.has(key)) return narrationCuesCache.get(key);

        const autogenQuery = narrationIsSharedView ? '' : '&autogen=1';
        const resp = await fetch(
            `${narrationApiBase}/narration/cues/${Number(slideIndex)}?language=${encodeURIComponent(language)}${autogenQuery}`,
            { credentials: 'same-origin' }
        );
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data || data.success !== true) {
            const msg = (data && (data.detail || data.error || data.message)) || `HTTP ${resp.status}`;
            throw new Error(msg);
        }

        const cues = Array.isArray(data.cues) ? data.cues : [];
        const normalized = cues
            .map((c) => ({
                start_ms: Number(c.start_ms || c.start || 0),
                end_ms: Number(c.end_ms || c.end || 0),
                text: (c.text || '').toString()
            }))
            .filter((c) => c.text && c.end_ms > c.start_ms)
            .sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms);

        const entry = { duration_ms: Number(data.duration_ms || 0), cues: normalized };
        narrationCuesCache.set(key, entry);
        return entry;
    }

    function setCurrentNarrationCues(entry) {
        narrationCurrentCues = (entry && Array.isArray(entry.cues)) ? entry.cues : [];
        narrationCurrentCueIndex = 0;
        setNarrationSubtitle('');
    }

    function stopNarrationSubtitleSyncLoop() {
        if (narrationSubtitleSyncRafId) {
            cancelAnimationFrame(narrationSubtitleSyncRafId);
            narrationSubtitleSyncRafId = 0;
        }
    }

    function startNarrationSubtitleSyncLoop() {
        stopNarrationSubtitleSyncLoop();
        const tick = () => {
            if (!narrationEnabled || !narrationAudioEl) {
                narrationSubtitleSyncRafId = 0;
                return;
            }
            updateNarrationSubtitleFromAudioTime();
            if (!narrationAudioEl.paused && !narrationAudioEl.ended) {
                narrationSubtitleSyncRafId = requestAnimationFrame(tick);
            } else {
                narrationSubtitleSyncRafId = 0;
            }
        };
        narrationSubtitleSyncRafId = requestAnimationFrame(tick);
    }

    function updateNarrationSubtitleFromAudioTime() {
        if (!narrationEnabled || !narrationAudioEl) return;
        const cues = narrationCurrentCues || [];
        if (!cues.length) {
            setNarrationSubtitle('');
            return;
        }
        const tMs = Math.max(0, Math.floor((narrationAudioEl.currentTime || 0) * 1000));

        // Clamp index to a valid range.
        if (narrationCurrentCueIndex < 0) narrationCurrentCueIndex = 0;
        if (narrationCurrentCueIndex >= cues.length) narrationCurrentCueIndex = cues.length - 1;

        // Fast forward
        while (narrationCurrentCueIndex < cues.length && tMs >= (cues[narrationCurrentCueIndex].end_ms || 0)) {
            narrationCurrentCueIndex++;
        }
        // Rewind
        if (narrationCurrentCueIndex >= cues.length) narrationCurrentCueIndex = cues.length - 1;
        while (narrationCurrentCueIndex > 0 && tMs < (cues[narrationCurrentCueIndex].start_ms || 0)) {
            narrationCurrentCueIndex--;
        }

        const cue = cues[narrationCurrentCueIndex];
        if (cue && tMs >= cue.start_ms && tMs < cue.end_ms) {
            setNarrationSubtitle(cue.text);
        } else {
            setNarrationSubtitle('');
        }
    }

    function preloadNarrationAhead(fromIndex) {
        const base = Number(fromIndex);
        for (let i = 1; i <= 2; i++) {
            const idx = base + i;
            if (idx >= 0 && idx < totalSlides) {
                loadNarrationCues(idx).catch(() => { });
            }
        }
    }

    function stopNarration() {
        narrationRevision += 1;
        narrationEnabled = false;
        stopNarrationSubtitleSyncLoop();
        updateNarrationButton();
        setNarrationSubtitle('');
        if (narrationAudioEl) {
            try {
                narrationAudioEl.pause();
                narrationAudioEl.currentTime = 0;
                narrationAudioEl.removeAttribute('src');
                narrationAudioEl.load();
            } catch (_) { }
        }
    }

    async function playNarrationForSlide(index) {
        if (!narrationEnabled) return;
        const revision = ++narrationRevision;
        let entry = null;
        try {
            entry = await loadNarrationCues(index);
            if (!narrationEnabled || revision !== narrationRevision) return;
        } catch (e) {
            if (revision !== narrationRevision) return;
            stopNarration();
            showNarrationToast(`无法加载字幕/音频（${narrationLanguage}）：${e && e.message ? e.message : e}`, 'error');
            return;
        }
        setCurrentNarrationCues(entry);
        if (!narrationAudioEl) {
            narrationAudioEl = new Audio();
            narrationAudioEl.preload = 'auto';
            narrationAudioEl.addEventListener('timeupdate', updateNarrationSubtitleFromAudioTime);
            narrationAudioEl.addEventListener('seeking', updateNarrationSubtitleFromAudioTime);
            narrationAudioEl.addEventListener('play', () => {
                updateNarrationSubtitleFromAudioTime();
                startNarrationSubtitleSyncLoop();
            });
            narrationAudioEl.addEventListener('playing', startNarrationSubtitleSyncLoop);
            narrationAudioEl.addEventListener('seeked', () => {
                updateNarrationSubtitleFromAudioTime();
                if (!narrationAudioEl.paused) startNarrationSubtitleSyncLoop();
            });
            narrationAudioEl.addEventListener('pause', () => {
                stopNarrationSubtitleSyncLoop();
                updateNarrationSubtitleFromAudioTime();
            });
            narrationAudioEl.addEventListener('waiting', stopNarrationSubtitleSyncLoop);
            narrationAudioEl.addEventListener('ended', () => {
                stopNarrationSubtitleSyncLoop();
                if (!narrationEnabled) return;
                if (currentSlideIndex < totalSlides - 1) {
                    nextSlide();
                } else {
                    stopNarration();
                }
            });
            narrationAudioEl.addEventListener('error', () => {
                if (!narrationEnabled) return;
                stopNarration();
                showNarrationToast('讲解音频加载失败（可能未生成音频）', 'warning');
            });
        }

        try {
            narrationAudioEl.pause();
            narrationAudioEl.currentTime = 0;
        } catch (_) { }
        stopNarrationSubtitleSyncLoop();

        const autogenQuery = narrationIsSharedView ? '' : '&autogen=1';
        const url = `${narrationApiBase}/narration/audio/${index}?language=${encodeURIComponent(narrationLanguage)}${autogenQuery}`;
        narrationAudioEl.src = url;
        try {
            await narrationAudioEl.play();
            if (!narrationEnabled || revision !== narrationRevision) return;
            updateNarrationSubtitleFromAudioTime();
            startNarrationSubtitleSyncLoop();
            preloadNarrationAhead(index);
        } catch (e) {
            if (revision !== narrationRevision) return;
            stopNarration();
            showNarrationToast('浏览器限制自动播放，请点击“讲解”按钮开始', 'warning');
        }
    }

    function onSlideChangedForNarration(index) {
        if (!narrationEnabled) return;
        // slide change should follow narration (manual next/prev also restarts audio)
        playNarrationForSlide(index);
    }

    async function toggleNarration() {
        if (!narrationAvailable) {
            showNarrationToast('暂无演讲稿，无法讲解', 'warning');
            return;
        }
        if (narrationEnabled) {
            stopNarration();
            return;
        }

        // avoid conflicts with timed auto play
        if (isAutoPlaying) {
            toggleAutoPlay();
        }

        narrationEnabled = true;
        updateNarrationButton();
        await playNarrationForSlide(currentSlideIndex);
    }

    function initNarration() {
        const qs = new URLSearchParams(window.location.search);
        const wantedLang = qs.get('language') === 'en' ? 'en' : 'zh';
        narrationLanguage = narrationAvailableLanguages.includes(wantedLang) ? wantedLang : narrationAvailableLanguages[0] || wantedLang;
        updateNarrationButton();
        if (qs.get('narration') === '1' && narrationAvailable) setTimeout(toggleNarration, 600);
    }

    // 定期检查数据更新
    function startDataRefreshCheck() {
        // 判断是否是分享视图，使用对应的API端点
        const isShared = config.isShared;
        const shareToken = config.shareToken;

        // 分享视图使用分享API，否则使用项目API
        const apiUrl = isShared && shareToken
            ? `/api/share/${encodeURIComponent(shareToken)}/slides-data`
            : `/api/projects/${encodeURIComponent(config.projectId)}/slides-data`;

        refreshInterval = setInterval(async () => {
            if (refreshBusy) return;
            refreshBusy = true;
            try {
                const response = await fetch(apiUrl, { credentials: 'same-origin' });
                if (!response.ok) {
                    // 分享视图的API调用失败时静默处理（可能是未登录）
                    return;
                }
                const data = await response.json();

                if (data.status === 'success' && data.updated_at > lastUpdatedAt && Array.isArray(data.slides_data) && data.slides_data.length) {
                    lastUpdatedAt = data.updated_at;
                    // 数据已更新，刷新幻灯片数据
                    slidesData = data.slides_data;
                    totalSlides = slidesData.length;
                    currentSlideIndex = Math.min(currentSlideIndex, totalSlides - 1);
                    narrationCuesCache.clear();

                    // 清空所有缓存
                    clearAllCache();

                    // 重新加载当前幻灯片
                    loadSlideWithCache(currentSlideIndex, true);

                    // 更新缩略图
                    updateThumbnailsContent();

                    // 显示更新提示
                    showUpdateNotification();
                }
            } catch (error) {
                // 检查数据更新失败，静默处理
            } finally { refreshBusy = false; }
        }, 10000); // 每10秒检查一次
    }

    // 更新缩略图内容
    function updateThumbnailsContent() { emitState();
    }

    // 显示更新通知
    function showUpdateNotification() { showNarrationToast('幻灯片内容已更新', 'success');
    }

    // 使用缓存机制加载幻灯片
    function loadSlideWithCache(index, isInitial = false) {
        if (disposed || index < 0 || index >= totalSlides || isTransitioning) return;

        // 如果是相同的幻灯片，直接返回
        if (index === currentSlideIndex && !isInitial) return;

        isTransitioning = true;
        pendingSlideIndex = index;
        emitState();

        // 检查是否已缓存
        if (slideFrameCache.has(index)) {
            // 使用缓存的iframe
            const cachedFrame = slideFrameCache.get(index);
            switchToFrame(cachedFrame, index);
        } else {
            // 创建新的iframe并渲染
            createAndLoadFrame(index, isInitial);
        }
    }

    // 创建并加载新的iframe
    function createAndLoadFrame(index, isInitial = false) {
        if (loadingSlides.has(index)) return; // 防止重复加载

        loadingSlides.add(index);
        const epoch = cacheEpoch;

        if (isInitial) {
            showLoadingOverlay();
        }

        const frameId = `slideFrame_${frameIdCounter++}`;
        const iframe = document.createElement('iframe');
        iframe.id = frameId;
        iframe.className = 'slide-frame hidden not-loaded';
        iframe.title = `PPT Slide ${index + 1}`;

        // 设置内容
        if (slidesData[index] && slidesData[index].html_content) {
            iframe.srcdoc = slidesData[index].html_content;

            // 监听加载完成
            iframe.onload = function () {
                if (disposed || epoch !== cacheEpoch) { iframe.remove(); return; }
                // 缓存iframe
                slideFrameCache.set(index, iframe);
                loadingSlides.delete(index);

                // 更新样式
                iframe.classList.remove('not-loaded');
                iframe.classList.add('cached');
                applySlideIframeOverrides(iframe);
                scheduleSlideFrameScale(iframe);

                // 延迟一点确保内容完全渲染
                setTimeout(() => {
                    if (epoch === cacheEpoch && pendingSlideIndex === index) switchToFrame(iframe, index);
                }, 100);
            };

            iframe.onerror = function () {
                if (epoch !== cacheEpoch) return;
                loadingSlides.delete(index);
                if (pendingSlideIndex === index) { pendingSlideIndex = null; isTransitioning = false; hideLoadingOverlay(); }
                emitState();
                isTransitioning = false;
                hideLoadingOverlay();
            };

            // 添加到容器
            document.getElementById('slideFrameContainer').appendChild(iframe);
        } else {
            loadingSlides.delete(index);
            isTransitioning = false;
            hideLoadingOverlay();
        }
    }

    // 切换到指定的iframe
    function switchToFrame(targetFrame, index) {
        // 隐藏当前显示的iframe
        if (currentVisibleFrame) {
            currentVisibleFrame.classList.remove('visible');
            currentVisibleFrame.classList.add('hidden');
        }

        // 显示目标iframe
        targetFrame.classList.remove('hidden');
        targetFrame.classList.add('visible');
        applySlideIframeOverrides(targetFrame);
        scheduleSlideFrameScale(targetFrame);

        // 更新状态
        currentVisibleFrame = targetFrame;
        currentSlideIndex = index;
        pendingSlideIndex = null;
        isTransitioning = false;

        // 更新UI
        updateControls();
        updateThumbnails();
        hideLoadingOverlay();
        onSlideChangedForNarration(index);

        // 预加载相邻幻灯片
        setTimeout(() => {
            preloadAdjacentSlides(index);
            // 管理缓存大小
            manageCacheSize();
        }, 300);
    }

    // 预加载相邻幻灯片
    function preloadAdjacentSlides(currentIndex) {
        const preloadIndices = [];

        // 预加载策略：当前页前后各2页
        for (let i = Math.max(0, currentIndex - 2); i <= Math.min(totalSlides - 1, currentIndex + 2); i++) {
            if (i !== currentIndex && !slideFrameCache.has(i) && !loadingSlides.has(i)) {
                preloadIndices.push(i);
            }
        }

        // 分批预加载，避免同时加载太多
        preloadIndices.forEach((index, i) => {
            setTimeout(() => {
                preloadSlide(index);
            }, i * 200); // 每200ms预加载一个
        });
    }

    // 预加载单个幻灯片
    function preloadSlide(index) {
        if (disposed || index < 0 || index >= totalSlides || slideFrameCache.has(index) || loadingSlides.has(index)) {
            return;
        }

        loadingSlides.add(index);
        const epoch = cacheEpoch;

        const frameId = `slideFrame_${frameIdCounter++}`;
        const iframe = document.createElement('iframe');
        iframe.id = frameId;
        iframe.className = 'slide-frame hidden not-loaded';
        iframe.title = `PPT Slide ${index + 1}`;

        if (slidesData[index] && slidesData[index].html_content) {
            iframe.srcdoc = slidesData[index].html_content;

            iframe.onload = function () {
                if (disposed || epoch !== cacheEpoch) { iframe.remove(); return; }
                slideFrameCache.set(index, iframe);
                loadingSlides.delete(index);

                iframe.classList.remove('not-loaded');
                iframe.classList.add('cached');
                applySlideIframeOverrides(iframe);
                scheduleSlideFrameScale(iframe);
                if (pendingSlideIndex === index) switchToFrame(iframe, index);
                emitState();
            };

            iframe.onerror = function () {
                if (epoch !== cacheEpoch) return;
                loadingSlides.delete(index);
                if (pendingSlideIndex === index) { pendingSlideIndex = null; isTransitioning = false; hideLoadingOverlay(); }
                emitState();
            };

            // 添加到容器
            document.getElementById('slideFrameContainer').appendChild(iframe);
        } else {
            loadingSlides.delete(index);
        }
    }

    // 显示加载动画
    function showLoadingOverlay() {
        const loadingOverlay = document.getElementById('loadingOverlay');
        loadingOverlay.classList.add('show');
        loading = true; emitState();
    }

    // 隐藏加载动画
    function hideLoadingOverlay() {
        const loadingOverlay = document.getElementById('loadingOverlay');
        loadingOverlay.classList.remove('show');
        loading = false; emitState();
    }

    // 清空所有缓存
    function clearAllCache() {
        cacheEpoch += 1;
        isTransitioning = false;
        pendingSlideIndex = null;
        document.getElementById('slideFrameContainer').replaceChildren();
        // 移除所有缓存的iframe
        slideFrameCache.forEach((iframe, index) => {
            if (iframe.parentNode) {
                iframe.parentNode.removeChild(iframe);
            }
        });

        // 清空缓存映射
        slideFrameCache.clear();
        loadingSlides.clear();
        currentVisibleFrame = null;
    }

    // 获取缓存统计信息
    function getCacheStats() {
        return {
            cachedSlides: slideFrameCache.size,
            loadingSlides: loadingSlides.size,
            totalSlides: totalSlides,
            cacheRatio: (totalSlides ? slideFrameCache.size / totalSlides * 100 : 0).toFixed(1) + '%'
        };
    }

    // 智能缓存管理 - 限制缓存数量以节省内存
    function manageCacheSize() {
        const maxCacheSize = Math.min(10, totalSlides); // 最多缓存10个幻灯片

        if (slideFrameCache.size > maxCacheSize) {
            // 移除距离当前页面最远的缓存
            const sortedIndices = Array.from(slideFrameCache.keys()).sort((a, b) => {
                const distA = Math.abs(a - currentSlideIndex);
                const distB = Math.abs(b - currentSlideIndex);
                return distB - distA; // 距离远的排在前面
            });

            // 移除最远的缓存
            const toRemove = sortedIndices.slice(0, slideFrameCache.size - maxCacheSize + 1);
            toRemove.forEach(index => {
                const iframe = slideFrameCache.get(index);
                if (iframe && iframe !== currentVisibleFrame) {
                    if (iframe.parentNode) {
                        iframe.parentNode.removeChild(iframe);
                    }
                    slideFrameCache.delete(index);
                }
            });
        }
    }

    // 兼容旧的loadSlide函数
    function loadSlide(index) {
        loadSlideWithCache(index);
    }

    // 更新控制按钮状态
    function updateControls() { emitState();
    }

    // 更新缩略图状态
    function updateThumbnails() { emitState();
    }

    // 上一页
    function previousSlide() {
        if (currentSlideIndex > 0 && !isTransitioning) {
            loadSlideWithCache(currentSlideIndex - 1);
        }
    }

    // 下一页
    function nextSlide() {
        if (currentSlideIndex < totalSlides - 1 && !isTransitioning) {
            loadSlideWithCache(currentSlideIndex + 1);
        }
    }

    // 跳转到指定幻灯片
    function goToSlide(index) {
        if (!isTransitioning && index !== currentSlideIndex && index >= 0 && index < totalSlides) {
            loadSlideWithCache(index);
        }
    }

    // 切换自动播放：有讲解时边讲边翻页（讲完一页自动下一页），否则定时翻页
    function toggleAutoPlay() {
        if (isAutoPlaying) {
            clearInterval(autoPlayInterval);
            isAutoPlaying = false;
        } else if (narrationAvailable) {
            toggleNarration();
            return;
        } else if (totalSlides > 1 && currentSlideIndex < totalSlides - 1) {
            stopNarration();
            isAutoPlaying = true;
            autoPlayInterval = setInterval(() => {
                if (currentSlideIndex < totalSlides - 1) nextSlide();
                else toggleAutoPlay();
            }, 5000);
        }
        emitState();
    }

    // 全屏模式控制相关
    let isFullscreenMode = false;

    // 切换全屏
    function toggleFullscreen() {
        if (!document.fullscreenElement) {
            if (!document.documentElement.requestFullscreen) { showNarrationToast('当前浏览器不支持全屏', 'warning'); return; }
            document.documentElement.requestFullscreen().catch(() => showNarrationToast('无法进入全屏，请重试或使用浏览器全屏功能', 'warning'));
        } else document.exitFullscreen?.();
    }

    // 切换缩略图显示
    function toggleThumbnails() { setPanel(panel === 'slides' ? null : 'slides');
    }

    // 切换键盘快捷键提示
    function toggleKeyboardHints() { setPanel(panel === 'keys' ? null : 'keys');
    }

    // 切换缓存状态显示

    function toggleCacheStatus() { setPanel(panel === 'cache' ? null : 'cache');
    }

    // 更新缓存状态显示
    function updateCacheStatusDisplay() { emitState();
    }

    // 退出演示
    function exitPresentation() {
        // 停止自动播放
        if (isAutoPlaying) {
            toggleAutoPlay();
        }
        stopNarration();

        // 先退出浏览器全屏，再次触发才退出演示
        if (document.fullscreenElement) {
            document.exitFullscreen();
            return;
        }

        // 尝试关闭浏览器标签页（仅对脚本打开的窗口有效）
        window.close();

        // 无法关闭时回退到项目页面或上一页
        setTimeout(function () {
            if (!narrationIsSharedView) {
                window.location.href = `/projects/${encodeURIComponent(config.projectId)}`;
            } else if (window.history.length > 1) {
                window.history.back();
            }
        }, 150);
    }

    // 键盘事件处理（防抖）
    let keyboardDebounce = false;
    function handlePresentationKey(e) {
        revealUiTemporarily();
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        if (panel) {
            if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); setPanel(null); }
            return;
        }
        if (isInteractiveTarget(e.target) || isTransitioning) return;

        switch (e.key) {
            case 'ArrowLeft':
                e.preventDefault();
                if (!keyboardDebounce) {
                    keyboardDebounce = true;
                    previousSlide();
                    setTimeout(() => keyboardDebounce = false, 200);
                }
                break;
            case 'ArrowRight':
            case ' ':
                e.preventDefault();
                if (!keyboardDebounce) {
                    keyboardDebounce = true;
                    nextSlide();
                    setTimeout(() => keyboardDebounce = false, 200);
                }
                break;
            case 'f':
            case 'F':
                e.preventDefault();
                toggleFullscreen();
                break;
            case 't':
            case 'T':
                e.preventDefault();
                toggleThumbnails();
                break;
            case 'h':
            case 'H':
                e.preventDefault();
                toggleKeyboardHints();
                break;
            case 'c':
            case 'C':
                e.preventDefault();
                toggleCacheStatus();
                break;
            case 'n':
            case 'N':
                e.preventDefault();
                toggleNarration();
                break;
            case 'Escape':
                e.preventDefault();
                exitPresentation();
                break;
            case 'Home':
                e.preventDefault();
                if (!keyboardDebounce) {
                    keyboardDebounce = true;
                    goToSlide(0);
                    setTimeout(() => keyboardDebounce = false, 200);
                }
                break;
            case 'End':
                e.preventDefault();
                if (!keyboardDebounce) {
                    keyboardDebounce = true;
                    goToSlide(totalSlides - 1);
                    setTimeout(() => keyboardDebounce = false, 200);
                }
                break;
        }
    }
    document.addEventListener('keydown', handlePresentationKey, true);

    // 鼠标滚轮事件（防抖）- 优化灵敏度
    let wheelDebounce = false;
    let wheelTimeout = null;
    document.addEventListener('wheel', function (e) {
        if (panel || isInteractiveTarget(e.target)) return;

        if (isTransitioning || wheelDebounce) return;

        e.preventDefault();
        e.stopPropagation();
        wheelDebounce = true;

        // 向下滚动翻到下一页，向上滚动翻到上一页
        if (e.deltaY > 0) {
            nextSlide();
        } else if (e.deltaY < 0) {
            previousSlide();
        }

        // 使用较短的防抖时间，提升响应速度
        setTimeout(() => wheelDebounce = false, 400);
    }, { passive: false, capture: true });

    // 触摸事件支持（移动设备）
    let touchStartX = 0;
    let touchEndX = 0;
    let touchAllowed = false;
    let touchDebounce = false;

    document.addEventListener('touchstart', function (e) {
        touchAllowed = !isTransitioning && !panel && !isInteractiveTarget(e.target);
        if (!touchAllowed) return;
        touchStartX = e.changedTouches[0].screenX;
    });

    document.addEventListener('touchend', function (e) {
        if (!touchAllowed || panel || isTransitioning || touchDebounce) return;
        touchEndX = e.changedTouches[0].screenX;
        handleSwipe();
    });

    function handleSwipe() {
        const swipeThreshold = 50;
        const diff = touchStartX - touchEndX;

        if (Math.abs(diff) > swipeThreshold) {
            touchDebounce = true;

            if (diff > 0) {
                // 向左滑动，下一页
                nextSlide();
            } else {
                // 向右滑动，上一页
                previousSlide();
            }

            setTimeout(() => touchDebounce = false, 300);
        }
    }

    // 全屏状态变化监听
    document.addEventListener('fullscreenchange', function () {
        isFullscreenMode = Boolean(document.fullscreenElement);
        document.body.classList.toggle('fullscreen-mode', isFullscreenMode);
        if (currentVisibleFrame) scheduleSlideFrameScale(currentVisibleFrame);
        revealUiTemporarily();
        emitState();
    });

    // 防止右键菜单
    document.addEventListener('contextmenu', function (e) {
        e.preventDefault();
    }, true);

    // 防止点击事件干扰键盘事件
    document.addEventListener('click', function (e) {
        // 确保焦点在文档上，以便键盘事件正常工作
        if (document.activeElement && document.activeElement.tagName === 'IFRAME') {
            document.body.focus();
        }
    }, true);

    // 页面加载完成后立即获取焦点
    window.addEventListener('load', function () {
        document.body.setAttribute('tabindex', '-1');
        if (document.activeElement === document.body) document.body.focus();
    });

    // 页面可见性变化时暂停自动播放
    document.addEventListener('visibilitychange', function () {
        if (document.hidden && isAutoPlaying) {
            toggleAutoPlay();
        }
    });

    window.LandPPTPresentation = { getState, previousSlide, nextSlide, goToSlide,
        toggleAutoPlay, toggleFullscreen, toggleNarration, changeLanguage,
        toggleThumbnails, toggleKeyboardHints, toggleCacheStatus, setPanel,
        exitPresentation, holdUi, revealUiTemporarily, getSlideFrameBaseSize };
    window.addEventListener('pagehide', () => {
        stopNarration();
        clearInterval(autoPlayInterval); clearInterval(refreshInterval);
        clearTimeout(uiHideTimer); slideDisplayResizeObserver?.disconnect();
        disposed = true; cacheEpoch += 1;
    });
    window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
})();
