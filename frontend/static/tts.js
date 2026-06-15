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
            const speakers = data.speakers || ['baya'];
            const tts = data.tts || {};
            const sel = $('#tts-speaker');
            sel.innerHTML = speakers.map((s) => `<option value="${s}">${s}</option>`).join('');
            const sil = tts.silero || {};
            if (sil.speaker) sel.value = sil.speaker;
            if (sil.sample_rate) $('#tts-sample-rate').value = String(sil.sample_rate);
            const ex = tts.export || {};
            if (ex.format) $('#tts-format').value = ex.format;
            $('#tts-split').checked = ex.split_chapters !== false;
            $('#tts-use-llm').checked = !!(tts.normalize && tts.normalize.use_llm);
        } catch (e) {
            console.error('voices load failed', e);
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
            speaker: $('#tts-speaker').value,
            sample_rate: parseInt($('#tts-sample-rate').value, 10),
            format: $('#tts-format').value,
            split_chapters: $('#tts-split').checked,
            use_llm: $('#tts-use-llm').checked,
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
        root.innerHTML = `<div class="tts-error">${escapeHtml(msg)}</div>`;
    }

    function escapeHtml(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : s;
        return d.innerHTML;
    }
})();
