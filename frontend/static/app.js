/**
 * Главный поток UI: загрузка файлов, транскрибация, рендер транскрипта,
 * пресеты ИИ и custom-сценарий.
 */
(function () {
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => Array.from(document.querySelectorAll(sel));

    const PRESET_TITLES = {
        youtube_description: 'YouTube описание',
        youtube_timecodes: 'YouTube таймкоды',
        telegram_post: 'Telegram пост',
        article: 'Статья',
    };

    const state = {
        file: null,
        result: null,
        settings: null,
        engineLanguages: { gigaam: ['ru'], whisper: ['auto', 'en', 'ru'] },
        activeEngine: 'gigaam',
        statusReady: false,
        statusPolling: false,
    };

    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        await loadSettings();
        setupTheme();
        setupTabs();
        setupUpload();
        setupEngineSelector();
        setupTranscribe();
        setupExport();
        setupAIPresets();
        setupCustom();
        setupTranscriptSearch();
        await loadEngines();
        setupStatusPolling();
    }

    async function loadSettings() {
        try {
            const res = await fetch('/api/settings');
            state.settings = await res.json();
            state.activeEngine = (state.settings.asr && state.settings.asr.engine) || 'gigaam';
        } catch (e) {
            console.error('settings load failed', e);
            state.settings = {};
        }
    }

    function setupTheme() {
        const theme = (state.settings.app && state.settings.app.theme) || 'dark';
        document.body.dataset.theme = theme;
        if (state.settings.app && state.settings.app.title) {
            $('#app-title').textContent = state.settings.app.title;
            document.title = state.settings.app.title;
        }
    }

    function setupTabs() {
        $$('.tab').forEach((tab) => {
            tab.addEventListener('click', () => {
                $$('.tab').forEach((t) => t.classList.remove('tab--active'));
                $$('.tab-panel').forEach((p) => p.classList.remove('tab-panel--active'));
                tab.classList.add('tab--active');
                $(`[data-panel="${tab.dataset.tab}"]`).classList.add('tab-panel--active');
            });
        });
    }

    async function loadEngines() {
        try {
            const res = await fetch('/api/asr/engines');
            const data = await res.json();
            data.engines.forEach((e) => {
                state.engineLanguages[e.name] = e.languages;
            });
            // Объединение для settings.js
            const all = new Set();
            Object.values(state.engineLanguages).forEach((arr) => arr.forEach((l) => all.add(l)));
            window.ASR_LANGUAGES = Array.from(all);
            // Заполнить селектор языка
            updateLanguageSelect();
            $('#select-engine').value = state.activeEngine;
        } catch (e) {
            console.error('engines load failed', e);
        }
    }

    function setupEngineSelector() {
        $('#select-engine').addEventListener('change', async (e) => {
            const newEngine = e.target.value;
            state.activeEngine = newEngine;
            updateLanguageSelect();
            try {
                const res = await fetch('/api/asr/engine', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        engine: newEngine,
                        language: $('#select-language').value,
                    }),
                });
                if (res.ok) {
                    state.statusReady = false;
                    pollStatus();
                }
            } catch (err) {
                console.error('set engine failed', err);
            }
        });
        $('#select-language').addEventListener('change', () => updateTranscribeBtn());
    }

    // Внешний хук для settings.js (применить движок при сохранении настроек)
    window.applyEngineFromSettings = async function (engine) {
        state.activeEngine = engine;
        $('#select-engine').value = engine;
        updateLanguageSelect();
        state.statusReady = false;
        pollStatus();
    };

    function updateLanguageSelect() {
        const sel = $('#select-language');
        const langs = state.engineLanguages[state.activeEngine] || ['ru'];
        const prev = sel.value;
        sel.innerHTML = langs.map((l) => `<option value="${l}">${l}</option>`).join('');
        const desired = (state.settings.asr && state.settings.asr.language) || langs[0];
        sel.value = langs.includes(prev) ? prev : (langs.includes(desired) ? desired : langs[0]);
    }

    function copyErrorToClipboard(el) {
        const text = el.querySelector('.status__text').textContent;
        navigator.clipboard.writeText(text).then(() => {
            el.classList.add('status--copied');
            el.title = 'Скопировано!';
            setTimeout(() => {
                el.classList.remove('status--copied');
                el.title = 'Нажмите, чтобы скопировать ошибку';
            }, 1500);
        });
    }

    let _restartPoll;

    function setupStatusPolling() {
        const statusEl = $('#asr-status');
        const dotText = statusEl.querySelector('.status__text');
        const installBtn = $('#btn-install-gigaam');
        let installStarted = false;

        statusEl.addEventListener('click', () => {
            if (statusEl.classList.contains('status--error')) copyErrorToClipboard(statusEl);
        });

        installBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (installStarted) return;
            installStarted = true;
            installBtn.disabled = true;
            installBtn.textContent = 'Скачивание…';
            statusEl.classList.remove('status--needs-install');
            statusEl.classList.add('status--installing');
            try {
                await fetch('/api/asr/install', { method: 'POST' });
            } catch { /* сервер может перезапускаться */ }
            state.statusPolling = false;
            _restartPoll();
        });

        async function pollStatus() {
            if (state.statusPolling) return;
            state.statusPolling = true;

            async function tick() {
                try {
                    const res = await fetch('/api/asr/status');
                    const s = await res.json();
                    statusEl.classList.remove('status--ready', 'status--loading', 'status--error', 'status--needs-install', 'status--installing');
                    installBtn.hidden = true;
                    if (s.status === 'ready') {
                        statusEl.classList.add('status--ready');
                        dotText.textContent = `Готово · ${s.detail || s.engine}`;
                        state.statusReady = true;
                        state.statusPolling = false;
                        updateTranscribeBtn();
                        return;
                    }
                    if (s.status === 'idle') {
                        statusEl.classList.add('status--ready');
                        dotText.textContent = `${s.detail || s.engine} · загрузится при первом запуске`;
                        state.statusReady = true;
                        state.statusPolling = false;
                        updateTranscribeBtn();
                        return;
                    }
                    if (s.status === 'error') {
                        statusEl.classList.add('status--error');
                        dotText.textContent = `Ошибка: ${s.error}`;
                        state.statusReady = false;
                        state.statusPolling = false;
                        updateTranscribeBtn();
                        return;
                    }
                    if (s.status === 'needs_install') {
                        statusEl.classList.add('status--needs-install');
                        dotText.textContent = 'Модель не установлена';
                        installBtn.hidden = false;
                        installBtn.disabled = false;
                        installBtn.textContent = 'Скачать';
                        installStarted = false;
                        state.statusReady = false;
                        state.statusPolling = false;
                        updateTranscribeBtn();
                        return;
                    }
                    statusEl.classList.add('status--loading');
                    dotText.textContent = `Загрузка модели… (${s.detail || s.engine})`;
                    state.statusReady = false;
                    updateTranscribeBtn();
                    setTimeout(tick, 1500);
                } catch (e) {
                    statusEl.classList.remove('status--ready', 'status--loading', 'status--needs-install', 'status--installing');
                    statusEl.classList.add('status--error');
                    dotText.textContent = 'Сервер недоступен';
                    setTimeout(tick, 3000);
                }
            }
            tick();
        }

        _restartPoll = pollStatus;
        pollStatus();
    }

    function pollStatus() {
        state.statusPolling = false;
        if (_restartPoll) _restartPoll();
    }

    function setupUpload() {
        const area = $('#upload-area');
        const input = $('#file-input');
        const meta = $('#upload-meta');

        area.addEventListener('click', () => input.click());
        area.addEventListener('dragover', (e) => {
            e.preventDefault();
            area.classList.add('dragover');
        });
        area.addEventListener('dragleave', () => area.classList.remove('dragover'));
        area.addEventListener('drop', (e) => {
            e.preventDefault();
            area.classList.remove('dragover');
            const f = e.dataTransfer.files[0];
            if (f) acceptFile(f);
        });
        input.addEventListener('change', (e) => {
            const f = e.target.files[0];
            if (f) acceptFile(f);
        });

        function acceptFile(f) {
            state.file = f;
            meta.hidden = false;
            meta.textContent = `${f.name} · ${formatSize(f.size)}`;
            updateTranscribeBtn();
        }
    }

    function updateTranscribeBtn() {
        const btn = $('#btn-transcribe');
        btn.disabled = !state.file || !state.statusReady;
    }

    function setupTranscribe() {
        $('#btn-transcribe').addEventListener('click', transcribe);
    }

    async function transcribe() {
        if (!state.file) return;
        const progress = $('#progress');
        const btn = $('#btn-transcribe');
        progress.hidden = false;
        btn.disabled = true;

        const fd = new FormData();
        fd.append('file', state.file);

        const params = new URLSearchParams({
            engine: $('#select-engine').value,
            language: $('#select-language').value,
        });

        try {
            const res = await fetch(`/api/transcribe?${params.toString()}`, {
                method: 'POST',
                body: fd,
            });
            const data = await res.json();
            if (!res.ok) {
                alert('Ошибка: ' + (data.error || res.statusText));
                return;
            }
            state.result = data.result;
            renderTranscript(state.result);
            $('#export-card').hidden = false;
            $('#btn-custom-ask').disabled = false;
            $$('.preset-btn').forEach((b) => (b.disabled = false));
            // Авто-переключение на вкладку транскрипта
            $('.tab[data-tab="transcript"]').click();
        } catch (e) {
            alert('Ошибка: ' + e.message);
        } finally {
            progress.hidden = true;
            btn.disabled = false;
        }
    }

    function renderTranscript(result) {
        const root = $('#transcript');
        root.classList.remove('empty');
        $('#transcript-toolbar').hidden = false;
        const segments = result.segments || [];
        if (!segments.length) {
            root.innerHTML = '<div class="empty__hint">Пустой результат.</div>';
            return;
        }
        root.innerHTML = segments
            .map(
                (s) => `
            <div class="segment" data-start="${s.start}">
                <div class="segment__time">${formatTime(s.start)}</div>
                <div class="segment__text">${escapeHtml(s.text || '')}</div>
            </div>`,
            )
            .join('');
        // Клик на тайминг копирует "M:SS — текст"
        root.querySelectorAll('.segment__time').forEach((el) => {
            el.addEventListener('click', () => {
                const parent = el.parentElement;
                const t = el.textContent;
                const txt = parent.querySelector('.segment__text').textContent;
                navigator.clipboard.writeText(`${t} — ${txt}`).catch(() => {});
                el.style.color = 'var(--ok)';
                setTimeout(() => (el.style.color = ''), 800);
            });
        });
    }

    function setupTranscriptSearch() {
        const search = $('#transcript-search');
        search.addEventListener('input', () => {
            const q = search.value.trim().toLowerCase();
            const segs = $$('#transcript .segment');
            if (!q) {
                segs.forEach((s) => {
                    s.classList.remove('match');
                    const t = s.querySelector('.segment__text');
                    t.innerHTML = escapeHtml(t.textContent);
                });
                return;
            }
            segs.forEach((s) => {
                const text = s.querySelector('.segment__text').textContent;
                const match = text.toLowerCase().includes(q);
                s.classList.toggle('match', match);
                if (match) {
                    const re = new RegExp(`(${escapeRegex(q)})`, 'gi');
                    s.querySelector('.segment__text').innerHTML =
                        escapeHtml(text).replace(re, '<mark>$1</mark>');
                } else {
                    s.querySelector('.segment__text').innerHTML = escapeHtml(text);
                }
            });
        });
        $('#btn-copy-transcript').addEventListener('click', () => {
            if (!state.result) return;
            navigator.clipboard.writeText(state.result.text || '').catch(() => {});
        });
    }

    function setupExport() {
        $$('[data-export]').forEach((btn) => {
            btn.addEventListener('click', () => {
                if (!state.result) return;
                const fmt = btn.dataset.export;
                const text = encodeURIComponent(JSON.stringify(state.result));
                const link = document.createElement('a');
                link.href = `/api/export/${fmt}?text=${text}`;
                link.download = `transcript.${fmt}`;
                link.click();
            });
        });
    }

    function setupAIPresets() {
        $$('.preset-btn').forEach((btn) => {
            btn.disabled = true;
            btn.addEventListener('click', () => {
                if (!state.result) return;
                runStream({
                    preset: btn.dataset.preset,
                    transcript: state.result.text || '',
                    segments: state.result.segments || [],
                }, PRESET_TITLES[btn.dataset.preset] || btn.dataset.preset);
            });
        });
    }

    function setupCustom() {
        $('#btn-custom-ask').addEventListener('click', () => {
            const q = $('#custom-prompt').value.trim();
            if (!q || !state.result) return;
            runStream({
                custom_prompt: q,
                transcript: state.result.text || '',
            }, `Свободный вопрос: ${q.slice(0, 60)}${q.length > 60 ? '…' : ''}`);
        });
    }

    function runStream(payload, title) {
        const history = $('#ai-history');
        if (history.querySelector('.empty')) history.innerHTML = '';

        const block = document.createElement('div');
        block.className = 'ai-block';
        block.innerHTML = `
            <div class="ai-block__head">
                <span class="ai-block__title">${escapeHtml(title)}</span>
                <button class="btn btn--ghost btn--sm">Копировать</button>
            </div>
            <div class="ai-block__body streaming"></div>
        `;
        history.prepend(block);
        const body = block.querySelector('.ai-block__body');
        const copyBtn = block.querySelector('button');
        copyBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(body.textContent).catch(() => {});
            copyBtn.textContent = 'Скопировано';
            setTimeout(() => (copyBtn.textContent = 'Копировать'), 1500);
        });

        let acc = '';
        window.streamLLM(payload, {
            onToken: (t) => {
                acc += t;
                body.textContent = acc;
            },
            onDone: () => body.classList.remove('streaming'),
            onError: (msg) => {
                body.classList.remove('streaming');
                body.classList.add('ai-block__error');
                body.textContent = msg;
            },
        });
    }

    // ----- utils -----
    function formatTime(seconds) {
        if (seconds >= 3600) {
            const h = Math.floor(seconds / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            const s = Math.floor(seconds % 60);
            return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
        }
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return `${m}:${String(s).padStart(2, '0')}`;
    }

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
    }

    function escapeHtml(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    function escapeRegex(s) {
        return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }
})();
