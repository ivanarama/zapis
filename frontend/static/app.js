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
        currentId: null,   // id открытой сохранённой расшифровки
        aiBlocks: [],       // накопленные ИИ-блоки текущей расшифровки
        library: [],        // лёгкий список сохранённых расшифровок
        diarization: null,  // ответ /api/asr/diarization (доступность, модели)
        warning: null,      // мягкая ошибка последнего прогона (диаризация)
    };

    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        await loadSettings();
        setupTheme();
        setupTabs();
        setupUpload();
        setupEngineSelector();
        await setupDiarization();
        setupTranscribe();
        setupExport();
        setupAIPresets();
        setupCustom();
        setupTranscriptSearch();
        await loadEngines();
        setupStatusPolling();
        await refreshLibrary();
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
                    dotText.textContent = `Загрузка модели в память… (${s.detail || s.engine})`;
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

    // ----- Диаризация (кто говорит) -----

    async function setupDiarization() {
        const box = $('#diarization-box');
        const chk = $('#chk-diarize');
        const field = $('#speakers-field');
        const note = $('#diarization-note');
        try {
            const res = await fetch('/api/asr/diarization');
            const info = await res.json();
            state.diarization = info;
            // Пакета sherpa-onnx нет — галочку не показываем вовсе, чтобы не
            // предлагать заведомо нерабочую функцию.
            if (!info.available) return;

            box.hidden = false;
            chk.checked = !!info.enabled;
            $('#num-speakers').value = info.num_speakers ?? 0;

            const sync = () => {
                field.hidden = !chk.checked;
                const needDownload = chk.checked && !info.models_ready;
                note.hidden = !needDownload;
                if (needDownload) {
                    note.textContent =
                        'Модели (~34 МБ) скачаются с github.com при первом запуске.';
                }
            };
            chk.addEventListener('change', sync);
            sync();
        } catch (e) {
            console.error('diarization info failed', e);
        }
    }

    function diarizationParams(params) {
        const box = $('#diarization-box');
        if (!box || box.hidden) return;
        const on = $('#chk-diarize').checked;
        params.set('diarize', on ? 'true' : 'false');
        if (on) {
            params.set('speakers', String(parseInt($('#num-speakers').value, 10) || 0));
        }
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
        diarizationParams(params);
        $('#progress-text').textContent = params.get('diarize') === 'true'
            ? 'Транскрибация и разметка говорящих…'
            : 'Транскрибация…';

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
            state.currentId = null;
            state.aiBlocks = [];
            // Диаризация могла не получиться — расшифровка при этом валидна,
            // но молчать об отсутствии подписей нельзя.
            state.warning = data.warning || null;
            renderTranscript(state.result);
            renderAiHistory();
            // Сначала сохраняем расшифровку — нужен currentId для сохранения ИИ-блоков.
            await saveNewTranscript(data.result);
            // Теперь безопасно включаем кнопки ИИ.
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
        const notice = state.warning
            ? `<div class="notice">${escapeHtml(state.warning)}</div>`
            : '';
        if (!segments.length) {
            root.innerHTML = notice + '<div class="empty__hint">Пустой результат.</div>';
            return;
        }
        root.innerHTML = notice + segments
            .map((s) => {
                // Подпись показываем только на смене говорящего: сегменты уже
                // разрезаны по репликам, и подпись у каждого дробит чтение.
                const speaker = s.speaker;
                const badge = speaker == null
                    ? ''
                    : `<span class="segment__speaker" data-speaker="${speaker % 8}">Спикер ${speaker + 1}</span>`;
                return `
            <div class="segment" data-start="${s.start}">
                <div class="segment__time">${formatTime(s.start)}</div>
                <div class="segment__body">${badge}<div class="segment__text">${escapeHtml(s.text || '')}</div></div>
            </div>`;
            })
            .join('');
        // Клик на тайминг копирует "M:SS — текст" (с подписью, если она есть)
        root.querySelectorAll('.segment__time').forEach((el) => {
            el.addEventListener('click', () => {
                const parent = el.parentElement;
                const t = el.textContent;
                const speaker = parent.querySelector('.segment__speaker');
                const txt = parent.querySelector('.segment__text').textContent;
                const body = speaker ? `${speaker.textContent}: ${txt}` : txt;
                navigator.clipboard.writeText(`${t} — ${body}`).catch(() => {});
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
        const FILENAMES = { txt: 'transcript.txt', srt: 'subtitles.srt', vtt: 'subtitles.vtt' };
        $$('[data-export]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                if (!state.result) return;
                const fmt = btn.dataset.export;
                const filename = FILENAMES[fmt] || `transcript.${fmt}`;
                try {
                    const res = await fetch(`/api/export/${fmt}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(state.result),
                    });
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        alert('Ошибка экспорта: ' + (err.error || res.statusText));
                        return;
                    }
                    const content = await res.text();
                    // Desktop (pywebview): нативный диалог «Сохранить как» —
                    // браузерный blob + <a download> во встроённом WebView не работает.
                    if (window.pywebview && window.pywebview.api && window.pywebview.api.save_as) {
                        const r = await window.pywebview.api.save_as(filename, content);
                        if (r && r.ok) {
                            flash(btn, 'Сохранено ✓');
                        } else if (r && r.error) {
                            alert('Не удалось сохранить файл: ' + r.error);
                        }
                        return;
                    }
                    // Fallback: обычный браузер (запуск без pywebview, отладка).
                    const blob = new Blob([content], {
                        type: res.headers.get('Content-Type') || 'text/plain',
                    });
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = filename;
                    link.click();
                    URL.revokeObjectURL(url);
                    flash(btn, 'Сохранено ✓');
                } catch (e) {
                    alert('Ошибка экспорта: ' + e.message);
                }
            });
        });
    }

    function flash(btn, text) {
        const original = btn.textContent;
        btn.textContent = text;
        setTimeout(() => { btn.textContent = original; }, 1200);
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

    // Создаёт DOM-карточку ИИ-блока и возвращает {block, body}.
    function createAiBlock(title) {
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
        return { block, body };
    }

    function runStream(payload, title) {
        const { body } = createAiBlock(title);

        let acc = '';
        window.streamLLM(payload, {
            onToken: (t) => {
                acc += t;
                body.textContent = acc;
            },
            onDone: () => {
                body.classList.remove('streaming');
                persistAiBlock(title, acc);
            },
            onError: (msg) => {
                body.classList.remove('streaming');
                body.classList.add('ai-block__error');
                body.textContent = msg;
            },
        });
    }

    // Перерисовывает историю ИИ-блоков из state.aiBlocks (хронологический порядок).
    function renderAiHistory() {
        const history = $('#ai-history');
        history.innerHTML = '';
        if (!state.aiBlocks.length) {
            history.innerHTML =
                '<div class="empty"><div class="empty__hint">' +
                'Сначала получите транскрипт, затем нажмите кнопку или задайте свой вопрос.' +
                '</div></div>';
            return;
        }
        state.aiBlocks.forEach((b) => {
            const { body } = createAiBlock(b.title || 'ИИ-ответ');
            body.classList.remove('streaming');
            body.textContent = b.body || '';
        });
    }

    // Дописывает завершённый ИИ-блок к сохранённой расшифровке.
    async function persistAiBlock(title, content) {
        if (!content) return;
        state.aiBlocks.push({ title, body: content, created_at: Date.now() / 1000 });
        if (!state.currentId) return;
        try {
            await fetch(`/api/transcripts/${state.currentId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ai_blocks: state.aiBlocks }),
            });
            refreshLibrary();
        } catch (e) {
            console.error('save ai block failed', e);
        }
    }

    // ----- Библиотека сохранённых расшифровок -----

    async function saveNewTranscript(result) {
        const baseName = state.file
            ? state.file.name.replace(/\.[^.]+$/, '')
            : 'Расшифровка';
        try {
            const res = await fetch('/api/transcripts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: baseName,
                    source: state.file ? state.file.name : '',
                    engine: $('#select-engine').value,
                    language: $('#select-language').value,
                    result,
                    ai_blocks: [],
                }),
            });
            const data = await res.json();
            if (res.ok && data.transcript) {
                state.currentId = data.transcript.id;
                state.aiBlocks = [];
                await refreshLibrary();
            }
        } catch (e) {
            console.error('save transcript failed', e);
        }
    }

    async function refreshLibrary() {
        try {
            const res = await fetch('/api/transcripts');
            const data = await res.json();
            state.library = data.transcripts || [];
        } catch (e) {
            console.error('library load failed', e);
            state.library = [];
        }
        renderLibrary();
    }

    function renderLibrary() {
        const list = $('#library-list');
        list.innerHTML = '';
        if (!state.library.length) {
            list.innerHTML =
                '<div class="library-empty">Пока нет сохранённых расшифровок. ' +
                'Транскрибируйте файл — он появится здесь.</div>';
            return;
        }
        state.library.forEach((item) => {
            const row = document.createElement('div');
            row.className = 'library-item';
            if (item.id === state.currentId) row.classList.add('library-item--active');
            row.dataset.id = item.id;
            const metaParts = [formatDate(item.created_at), item.engine];
            if (item.duration) metaParts.push(formatTime(item.duration));
            if (item.ai_count) metaParts.push(`ИИ: ${item.ai_count}`);
            row.innerHTML = `
                <div class="library-item__main">
                    <div class="library-item__name">${escapeHtml(item.name)}</div>
                    <div class="library-item__meta">${escapeHtml(metaParts.filter(Boolean).join(' · '))}</div>
                </div>
                <button class="library-item__btn library-item__btn--rename" title="Переименовать">✎</button>
                <button class="library-item__btn library-item__btn--del" title="Удалить">×</button>`;
            row.querySelector('.library-item__main')
                .addEventListener('click', () => openTranscript(item.id));
            row.querySelector('.library-item__btn--rename')
                .addEventListener('click', (e) => {
                    e.stopPropagation();
                    startRename(row, item);
                });
            row.querySelector('.library-item__btn--del')
                .addEventListener('click', (e) => {
                    e.stopPropagation();
                    deleteTranscript(item.id);
                });
            list.appendChild(row);
        });
    }

    async function openTranscript(id) {
        try {
            const res = await fetch(`/api/transcripts/${id}`);
            if (!res.ok) {
                alert('Не удалось загрузить расшифровку');
                return;
            }
            const rec = await res.json();
            state.result = rec.result || {};
            state.currentId = rec.id;
            state.aiBlocks = rec.ai_blocks || [];
            state.warning = null;  // предупреждение относилось к прошлому прогону
            renderTranscript(state.result);
            renderAiHistory();
            $('#transcript-search').value = '';
            $('#export-card').hidden = false;
            $('#btn-custom-ask').disabled = false;
            $$('.preset-btn').forEach((b) => (b.disabled = false));
            renderLibrary();
            $('.tab[data-tab="transcript"]').click();
        } catch (e) {
            alert('Ошибка: ' + e.message);
        }
    }

    async function deleteTranscript(id) {
        if (!confirm('Удалить эту расшифровку безвозвратно?')) return;
        try {
            await fetch(`/api/transcripts/${id}`, { method: 'DELETE' });
            if (state.currentId === id) state.currentId = null;
            refreshLibrary();
        } catch (e) {
            console.error('delete transcript failed', e);
        }
    }

    function startRename(row, item) {
        const nameEl = row.querySelector('.library-item__name');
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'library-item__rename-input';
        input.value = item.name;
        nameEl.replaceWith(input);
        input.focus();
        input.select();

        let done = false;
        const commit = async () => {
            if (done) return;
            done = true;
            const newName = input.value.trim() || item.name;
            if (newName !== item.name) {
                try {
                    await fetch(`/api/transcripts/${item.id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name: newName }),
                    });
                } catch (e) {
                    console.error('rename failed', e);
                }
            }
            refreshLibrary();
        };
        input.addEventListener('blur', commit);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') input.blur();
            if (e.key === 'Escape') {
                done = true;
                renderLibrary();
            }
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

    function formatDate(ts) {
        if (!ts) return '';
        const d = new Date(ts * 1000);
        const pad = (n) => String(n).padStart(2, '0');
        return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
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
