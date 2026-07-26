/**
 * Режим «Озвучка» (текст → аудиокнига): переключатель режимов, загрузка .txt,
 * запуск синтеза и приём SSE-прогресса с бэкенда /api/tts/synthesize.
 */
(function () {
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => Array.from(document.querySelectorAll(sel));

    const state = { file: null, busy: false };

    document.addEventListener('DOMContentLoaded', init);

    async function init() {
        setupModeSwitch();
        setupUpload();
        await loadVoices();
        const engSel = $('#tts-engine');
        if (engSel) engSel.addEventListener('change', applyEngine);
        const speed = $('#tts-piper-speed');
        if (speed) {
            speed.addEventListener('input', () => {
                const v = $('#tts-speed-val');
                if (v) v.textContent = parseFloat(speed.value).toFixed(2);
            });
        }
        const cs = $('#tts-cloud-save'); if (cs) cs.addEventListener('click', saveCloudCreds);
        const ct = $('#tts-cloud-test'); if (ct) ct.addEventListener('click', testCloud);
        $('#btn-synthesize').addEventListener('click', synthesize);
    }

    function setupModeSwitch() {
        $$('.mode-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                const mode = btn.dataset.mode;
                $$('.mode-btn').forEach((b) => b.classList.toggle('mode-btn--active', b === btn));
                $('#view-transcribe').hidden = mode !== 'transcribe';
                $('#view-audiobook').hidden = mode !== 'audiobook';
            });
        });
    }

    async function loadVoices() {
        try {
            const res = await fetch('/api/tts/voices');
            const data = await res.json();
            state.engines = data.engines || {
                silero: { speakers: data.speakers || ['baya'], fixed_rate: false },
            };
            const tts = data.tts || {};
            state.tts = tts;
            const eng = $('#tts-engine');
            if (eng) eng.value = data.engine || tts.engine || 'silero';
            const ex = tts.export || {};
            if (ex.format) $('#tts-format').value = ex.format;
            if ((tts.silero || {}).sample_rate) {
                $('#tts-sample-rate').value = String(tts.silero.sample_rate);
            }
            $('#tts-split').checked = ex.split_chapters !== false;
            $('#tts-accent').checked = !tts.accent || tts.accent.enabled !== false;
            $('#tts-use-llm').checked = !!(tts.normalize && tts.normalize.use_llm);
            $('#tts-pause-each').checked = !!tts.pause_each_sentence;
            const ls = (tts.piper && tts.piper.length_scale) || 1.0;
            $('#tts-piper-speed').value = String(ls);
            const sv = $('#tts-speed-val');
            if (sv) sv.textContent = parseFloat(ls).toFixed(2);
            // Предзаполнение облачных ключей (если ранее сохранены в settings).
            const yx = tts.yandex || {}, sb = tts.sber || {};
            const yk = $('#tts-yandex-key'); if (yk) yk.value = yx.api_key || '';
            const yf = $('#tts-yandex-folder'); if (yf) yf.value = yx.folder_id || '';
            const si = $('#tts-sber-id'); if (si) si.value = sb.client_id || '';
            const ss = $('#tts-sber-secret'); if (ss) ss.value = sb.client_secret || '';
            applyEngine();
        } catch (e) {
            console.error('voices load failed', e);
        }
    }

    // Подстраивает форму под выбранный движок: список голосов, видимость частоты
    // (её диктуют Piper и облако) и доступность ударений (ruaccent — только Silero).
    function applyEngine() {
        const engineSel = $('#tts-engine');
        const engine = engineSel ? engineSel.value : 'silero';
        const info = (state.engines && state.engines[engine]) || { speakers: [], fixed_rate: false };
        const tts = state.tts || {};
        const isCloud = engine === 'yandex' || engine === 'sber';

        let saved;
        if (engine === 'piper') saved = tts.piper && tts.piper.speaker;
        else if (engine === 'yandex') saved = tts.yandex && tts.yandex.voice;
        else if (engine === 'sber') saved = tts.sber && tts.sber.voice;
        else if (engine === 'edge') saved = tts.edge && tts.edge.voice;
        else saved = tts.silero && tts.silero.speaker;

        const sel = $('#tts-speaker');
        const speakers = (info.speakers && info.speakers.length) ? info.speakers : ['baya'];
        const hifi = info.hifi || [];
        sel.innerHTML = speakers.map((s) => {
            const mark = hifi.includes(s) ? ' ★' : '';
            return `<option value="${s}">${s}${mark}</option>`;
        }).join('');
        if (saved && speakers.includes(saved)) sel.value = saved;

        const rateField = $('#tts-rate-field');
        if (rateField) rateField.style.display = info.fixed_rate ? 'none' : '';

        const speedField = $('#tts-speed-field');
        if (speedField) speedField.hidden = engine !== 'piper';

        // Ударения (ruaccent) понимает только Silero.
        const accent = $('#tts-accent');
        const note = $('#tts-accent-note');
        const accentApplies = engine === 'silero';
        if (accent) accent.disabled = !accentApplies;
        const accentField = $('#tts-accent-field');
        if (accentField) accentField.style.opacity = accentApplies ? '' : '0.5';
        if (note) note.textContent = accentApplies ? '' : '— только для Silero';

        // Облачные учётные данные.
        const creds = $('#tts-cloud-creds');
        if (creds) {
            creds.hidden = !isCloud;
            creds.querySelectorAll('[data-engine]').forEach((el) => {
                el.style.display = el.dataset.engine === engine ? '' : 'none';
            });
        }
        const hint = $('#tts-cloud-hint');
        if (hint && isCloud) {
            hint.textContent = info.needs_config
                ? '⚠ Ключи не заданы — заполните и сохраните, иначе озвучка не сработает.'
                : 'Ключи заданы. Синтез идёт в облаке (нужен интернет).';
            hint.style.color = info.needs_config ? 'var(--danger, #e53935)' : '';
        }
    }

    // Сохранение облачных ключей в settings (deep-merge не затирает прочие поля).
    async function saveCloudCreds() {
        const engine = $('#tts-engine').value;
        let patch = null;
        if (engine === 'yandex') {
            patch = { tts: { yandex: {
                api_key: ($('#tts-yandex-key').value || '').trim(),
                folder_id: ($('#tts-yandex-folder').value || '').trim(),
            } } };
        } else if (engine === 'sber') {
            patch = { tts: { sber: {
                client_id: ($('#tts-sber-id').value || '').trim(),
                client_secret: ($('#tts-sber-secret').value || '').trim(),
            } } };
        }
        if (!patch) return;
        const hint = $('#tts-cloud-hint');
        try {
            const res = await fetch('/api/settings', {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch),
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            await loadVoices();  // обновит needs_config/голос
            // loadVoices ставит движок из сохранённых настроек (пока ещё silero,
            // т.к. выбор движка персистится только при синтезе) — возвращаем
            // выбор пользователя, иначе список сбрасывается на Silero.
            const es = $('#tts-engine');
            if (es && es.value !== engine) { es.value = engine; applyEngine(); }
            if (hint) { hint.textContent = 'Ключи сохранены.'; hint.style.color = ''; }
        } catch (e) {
            alert('Не удалось сохранить ключи: ' + (e.message || e));
        }
    }

    // Проверка ключа коротким синтезом через /api/tts/test.
    async function testCloud() {
        const engine = $('#tts-engine').value;
        const speaker = $('#tts-speaker').value;
        const hint = $('#tts-cloud-hint');
        if (hint) { hint.textContent = 'Проверка…'; hint.style.color = ''; }
        try {
            const res = await fetch('/api/tts/test', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ engine, speaker }),
            });
            const d = await res.json();
            if (d.ok) {
                if (hint) { hint.textContent = '✓ Ключ работает (' + (d.samples || 0) + ' сэмплов).'; hint.style.color = ''; }
            } else {
                if (hint) { hint.textContent = '✗ ' + (d.error || 'ошибка'); hint.style.color = 'var(--danger, #e53935)'; }
            }
        } catch (e) {
            if (hint) { hint.textContent = '✗ ' + (e.message || e); hint.style.color = 'var(--danger, #e53935)'; }
        }
    }

    function setupUpload() {
        const area = $('#tts-upload-area');
        const input = $('#tts-file-input');
        const meta = $('#tts-upload-meta');

        area.addEventListener('click', () => input.click());
        area.addEventListener('dragover', (e) => { e.preventDefault(); area.classList.add('dragover'); });
        area.addEventListener('dragleave', () => area.classList.remove('dragover'));
        area.addEventListener('drop', (e) => {
            e.preventDefault();
            area.classList.remove('dragover');
            const f = e.dataTransfer.files[0];
            if (f) accept(f);
        });
        input.addEventListener('change', (e) => {
            const f = e.target.files[0];
            if (f) accept(f);
        });

        function accept(f) {
            state.file = f;
            meta.hidden = false;
            meta.textContent = `${f.name} · ${(f.size / 1024).toFixed(1)} KB`;
            if (!$('#tts-title').value.trim()) {
                $('#tts-title').value = f.name.replace(/\.[^.]+$/, '');
            }
            $('#btn-synthesize').disabled = state.busy;
        }
    }

    function setProgress(pct, text) {
        $('#tts-progress').hidden = false;
        $('#tts-progress-fill').style.width = `${Math.max(0, Math.min(100, pct || 0))}%`;
        if (text) $('#tts-progress-text').textContent = text;
    }

    async function synthesize() {
        if (!state.file || state.busy) return;
        state.busy = true;
        const btn = $('#btn-synthesize');
        btn.disabled = true;
        setProgress(0, 'Подготовка…');
        renderBusy();

        const options = {
            title: $('#tts-title').value.trim(),
            author: $('#tts-author').value.trim(),
            engine: $('#tts-engine').value,
            speaker: $('#tts-speaker').value,
            sample_rate: parseInt($('#tts-sample-rate').value, 10),
            format: $('#tts-format').value,
            split_chapters: $('#tts-split').checked,
            accent: $('#tts-accent').checked,
            use_llm: $('#tts-use-llm').checked,
            pause_each_sentence: $('#tts-pause-each').checked,
            length_scale: parseFloat($('#tts-piper-speed').value) || 1.0,
        };
        const fd = new FormData();
        fd.append('file', state.file);
        fd.append('options', JSON.stringify(options));

        try {
            await streamSynthesis(fd, {
                onProgress: (d) => setProgress(d.percent, d.message),
                onDone: (d) => { setProgress(100, d.message || 'Готово'); renderResult(d); },
                onError: (msg) => { $('#tts-progress').hidden = true; renderError(msg); },
            });
        } catch (e) {
            renderError(e.message || String(e));
        } finally {
            state.busy = false;
            btn.disabled = !state.file;
        }
    }

    // SSE через fetch + ReadableStream (тот же протокол, что и stream.js).
    async function streamSynthesis(formData, cb) {
        const res = await fetch('/api/tts/synthesize', { method: 'POST', body: formData });
        if (!res.ok) {
            let m = `HTTP ${res.status}`;
            try { const j = await res.json(); m = j.error || m; } catch (_) { /* ignore */ }
            cb.onError(m);
            return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let idx;
            while ((idx = buffer.indexOf('\n\n')) >= 0) {
                const chunk = buffer.slice(0, idx);
                buffer = buffer.slice(idx + 2);
                const line = chunk.split('\n').find((l) => l.startsWith('data:'));
                if (!line) continue;
                const dataStr = line.slice(5).trim();
                if (!dataStr) continue;
                let d;
                try { d = JSON.parse(dataStr); } catch (_) { continue; }
                if (d.error) { cb.onError(d.error); return; }
                if (d.done) { cb.onDone(d); return; }
                cb.onProgress(d);
            }
        }
    }

    function renderBusy() {
        const root = $('#tts-result');
        root.classList.remove('empty');
        root.innerHTML = '<div class="empty__hint">Идёт озвучивание… можно следить за прогрессом слева.</div>';
    }

    function renderResult(d) {
        const root = $('#tts-result');
        root.classList.remove('empty');
        const files = d.files || [];
        const dir = d.output_dir || '';
        const items = files.map((f) => {
            const full = `${dir}/${f}`;
            const src = `/api/tts/audio?path=${encodeURIComponent(full)}`;
            return `<div class="tts-file">
                <div class="tts-file__name">${escapeHtml(f)}</div>
                <audio controls preload="none" src="${src}"></audio>
            </div>`;
        }).join('');
        root.innerHTML = `
            <div class="tts-done">
                <div class="tts-done__head">
                    <span><strong>Готово:</strong> ${files.length} файл(ов)</span>
                    <button class="btn btn--secondary btn--sm" id="tts-open-folder">Открыть папку</button>
                </div>
                <div class="tts-done__path">${escapeHtml(dir)}</div>
                <div class="tts-files">${items}</div>
            </div>`;
        const open = $('#tts-open-folder');
        if (open) {
            open.addEventListener('click', () => {
                fetch('/api/tts/reveal', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: dir }),
                }).catch(() => {});
            });
        }
    }

    function renderError(msg) {
        const root = $('#tts-result');
        root.classList.remove('empty');
        const text = msg == null ? '' : String(msg);
        root.innerHTML = `
            <div class="tts-error">
                <div class="tts-error__head">
                    <span class="tts-error__title">Ошибка озвучивания</span>
                    <button class="btn btn--secondary btn--sm" id="tts-copy-error">Скопировать</button>
                </div>
                <pre class="tts-error__text" id="tts-error-text">${escapeHtml(text)}</pre>
            </div>`;
        const copy = $('#tts-copy-error');
        if (copy) {
            copy.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(text);
                } catch (_) {
                    // Фолбэк, если Clipboard API недоступен (не-https и т.п.):
                    // выделяем текст, чтобы пользователь скопировал руками.
                    const sel = window.getSelection();
                    const range = document.createRange();
                    range.selectNodeContents($('#tts-error-text'));
                    sel.removeAllRanges();
                    sel.addRange(range);
                }
                const old = copy.textContent;
                copy.textContent = 'Скопировано';
                setTimeout(() => { copy.textContent = old; }, 1500);
            });
        }
    }

    function escapeHtml(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : s;
        return d.innerHTML;
    }
})();
