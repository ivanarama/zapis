/**
 * Settings modal: 4 вкладки (ASR, LLM, Промпты, Внешний вид).
 * Открытие/закрытие, переключение вкладок, рендер форм, сохранение.
 */
(function () {
    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

    const PRESET_KEYS = [
        ['youtube_description', 'YouTube описание'],
        ['youtube_timecodes', 'YouTube таймкоды'],
        ['telegram_post', 'Telegram пост'],
        ['article', 'Статья'],
    ];

    let currentSettings = null;
    let currentPrompts = null;
    let promptDefaults = null;
    let diarizationInfo = null;  // доступность sherpa-onnx, список моделей, статус

    function open() {
        $('#settings-modal').hidden = false;
        loadAll();
    }
    function close() {
        $('#settings-modal').hidden = true;
    }

    async function loadAll() {
        try {
            const [s, p] = await Promise.all([
                fetch('/api/settings').then((r) => r.json()),
                fetch('/api/prompts').then((r) => r.json()),
            ]);
            currentSettings = s;
            currentPrompts = p.current;
            promptDefaults = p.defaults;
            await refreshDiarizationInfo();
            renderASR();
            renderLLM();
            renderPrompts();
            renderTTS();
            renderAppearance();
        } catch (e) {
            console.error('Settings load failed:', e);
        }
    }

    function renderASR() {
        const asr = currentSettings.asr || {};
        $('#settings-engine').value = asr.engine || 'gigaam';
        $('#settings-whisper-model').value = (asr.whisper && asr.whisper.model) || 'small';
        const langSel = $('#settings-language');
        const langs = window.ASR_LANGUAGES || ['ru', 'en'];
        langSel.innerHTML = langs.map((l) => `<option value="${l}">${l}</option>`).join('');
        langSel.value = asr.language || 'ru';
        renderDiarization(asr.diarization || {});
    }

    function renderDiarization(d) {
        $('#settings-diarization-enabled').checked = !!d.enabled;
        $('#settings-diarization-speakers').value = d.num_speakers ?? 0;
        $('#settings-diarization-threshold').value = d.threshold ?? 0.5;

        const sel = $('#settings-diarization-model');
        const models = (diarizationInfo && diarizationInfo.models) || {};
        const names = Object.keys(models);
        const current = d.embedding_model || names[0] || '';
        // Модель из настроек может отсутствовать в списке (правили файл руками) —
        // добавляем её как есть, чтобы сохранение не подменяло чужой выбор.
        if (current && !names.includes(current)) names.unshift(current);
        sel.innerHTML = names
            .map((n) => `<option value="${n}">${models[n] || n}</option>`)
            .join('');
        sel.value = current;

        const shift = $('#settings-diarization-shift');
        const shiftValue = String(d.window_shift_ratio ?? 0.3);
        // Значение могли выставить руками в settings.json — не подменяем его
        // ближайшим из списка, а показываем как есть.
        if (!Array.from(shift.options).some((o) => o.value === shiftValue)) {
            shift.insertAdjacentHTML(
                'afterbegin',
                `<option value="${shiftValue}">Своё значение: ${shiftValue}</option>`,
            );
        }
        shift.value = shiftValue;

        updateDiarizationState();
    }

    function updateDiarizationState() {
        const label = $('#diarization-models-state');
        const btn = $('#btn-download-diarization');
        if (!diarizationInfo || !diarizationInfo.available) {
            label.textContent = (diarizationInfo && diarizationInfo.install_hint) || '';
            btn.disabled = true;
            return;
        }
        const st = diarizationInfo.status || {};
        if (st.status === 'error') {
            label.textContent = st.error || 'Ошибка';
            btn.disabled = false;
        } else if (st.status === 'loading') {
            label.textContent = 'Скачиваю модели…';
            btn.disabled = true;
        } else if (diarizationInfo.models_ready) {
            label.textContent = 'Модели на месте.';
            btn.disabled = true;
        } else {
            label.textContent = 'Модели ещё не скачаны (~34 МБ).';
            btn.disabled = false;
        }
    }

    async function refreshDiarizationInfo() {
        try {
            const res = await fetch('/api/asr/diarization');
            diarizationInfo = await res.json();
        } catch (e) {
            console.error('diarization info failed', e);
            diarizationInfo = null;
        }
    }

    async function downloadDiarizationModels() {
        // Настройки сохраняем до скачивания: качать надо ту модель, которую
        // пользователь только что выбрал в списке, а не сохранённую ранее.
        await saveAll({ keepOpen: true });
        const res = await fetch('/api/asr/diarization/download', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            alert('Ошибка: ' + (data.error || res.statusText));
            return;
        }
        // Опрашиваем статус, пока идёт загрузка: она занимает десятки секунд.
        const poll = async () => {
            await refreshDiarizationInfo();
            updateDiarizationState();
            const st = (diarizationInfo && diarizationInfo.status) || {};
            if (st.status === 'loading') setTimeout(poll, 1500);
        };
        poll();
    }

    function renderLLM() {
        const llm = currentSettings.llm || {};
        const list = $('#profiles-list');
        list.innerHTML = '';
        (llm.profiles || []).forEach((p, idx) => list.appendChild(buildProfileCard(p, idx)));
        $('#settings-temperature').value = llm.temperature ?? 0.3;
        $('#settings-max-tokens').value = llm.max_tokens ?? 4096;
    }

    function buildProfileCard(profile, idx) {
        const tmpl = $('#profile-template');
        const node = tmpl.content.firstElementChild.cloneNode(true);
        node.dataset.idx = idx;
        $('.profile-name', node).value = profile.name || `profile-${idx + 1}`;
        $('.profile-provider', node).value = profile.api_provider || 'openai';
        $('.profile-url', node).value = profile.base_url || '';
        $('.profile-key', node).value = profile.api_key || '';
        $('.profile-models', node).value = (profile.models || []).join('\n');

        $('.profile-remove', node).addEventListener('click', () => {
            node.remove();
        });
        $('.profile-up', node).addEventListener('click', () => {
            const prev = node.previousElementSibling;
            if (prev) node.parentNode.insertBefore(node, prev);
        });
        $('.profile-down', node).addEventListener('click', () => {
            const next = node.nextElementSibling;
            if (next) node.parentNode.insertBefore(next, node);
        });
        return node;
    }

    function collectProfiles() {
        return $$('.profile-card', $('#profiles-list'))
            .map((card) => ({
                name: $('.profile-name', card).value.trim() || 'profile',
                api_provider: $('.profile-provider', card).value,
                base_url: $('.profile-url', card).value.trim(),
                api_key: $('.profile-key', card).value,
                models: $('.profile-models', card).value
                    .split('\n').map((m) => m.trim()).filter(Boolean),
            }))
            .filter((p) => p.models.length > 0);
    }

    function renderPrompts() {
        const root = $('#prompts-editor');
        root.innerHTML = '';
        const tmpl = $('#prompt-template');
        PRESET_KEYS.forEach(([key, title]) => {
            const node = tmpl.content.firstElementChild.cloneNode(true);
            node.dataset.key = key;
            $('.prompt-card__title', node).textContent = title;
            const cur = currentPrompts[key] || {};
            const def = promptDefaults[key] || {};
            $('.prompt-user', node).value = cur.user_template || '';
            $('.prompt-user', node).placeholder = def.user_template || '';
            root.appendChild(node);
        });

        // Custom system — отдельной карточкой
        const customNode = tmpl.content.firstElementChild.cloneNode(true);
        customNode.dataset.key = 'custom_system';
        $('.prompt-card__title', customNode).textContent = 'Свободный сценарий — system prompt';
        $('.prompt-user', customNode).value = currentPrompts.custom_system || '';
        $('.prompt-user', customNode).placeholder = promptDefaults.custom_system || '';
        root.appendChild(customNode);
    }

    function collectPrompts() {
        const out = {};
        $$('.prompt-card', $('#prompts-editor')).forEach((card) => {
            const key = card.dataset.key;
            if (key === 'custom_system') {
                out.custom_system = $('.prompt-user', card).value;
            } else {
                out[key] = {
                    user_template: $('.prompt-user', card).value,
                };
            }
        });
        return out;
    }

    function renderTTS() {
        const tts = currentSettings.tts || {};
        const sil = tts.silero || {};
        const pp = tts.piper || {};
        const ex = tts.export || {};
        const pz = tts.pauses || {};
        const nz = tts.normalize || {};
        const ac = tts.accent || {};
        $('#settings-tts-engine').value = tts.engine || 'silero';
        $('#settings-tts-piper-speaker').value = pp.speaker || 'ru_RU-ruslan-medium';
        $('#settings-tts-piper-speed').value = pp.length_scale ?? 1.0;
        $('#settings-tts-pause-each').checked = !!tts.pause_each_sentence;
        $('#settings-tts-speaker').value = sil.speaker || 'baya';
        $('#settings-tts-rate').value = String(sil.sample_rate || 48000);
        $('#settings-tts-format').value = ex.format || 'mp3';
        $('#settings-tts-bitrate').value = ex.bitrate ?? 128000;
        $('#settings-tts-split').checked = ex.split_chapters !== false;
        $('#settings-tts-accent').checked = ac.enabled !== false;
        $('#settings-tts-accent-size').value = ac.model_size || 'tiny';
        $('#settings-tts-use-llm').checked = !!nz.use_llm;
        $('#settings-tts-pause-sentence').value = pz.sentence ?? 300;
        $('#settings-tts-pause-paragraph').value = pz.paragraph ?? 700;
        $('#settings-tts-pause-chapter').value = pz.chapter ?? 1500;
        $('#settings-tts-normalize-prompt').value =
            (currentPrompts.tts_normalize && currentPrompts.tts_normalize.system) || '';
    }

    function collectTTS() {
        return {
            ...(currentSettings.tts || {}),
            engine: $('#settings-tts-engine').value,
            pause_each_sentence: $('#settings-tts-pause-each').checked,
            silero: {
                ...((currentSettings.tts && currentSettings.tts.silero) || {}),
                speaker: $('#settings-tts-speaker').value,
                sample_rate: parseInt($('#settings-tts-rate').value, 10) || 48000,
            },
            piper: {
                ...((currentSettings.tts && currentSettings.tts.piper) || {}),
                speaker: $('#settings-tts-piper-speaker').value,
                length_scale: parseFloat($('#settings-tts-piper-speed').value) || 1.0,
            },
            export: {
                ...((currentSettings.tts && currentSettings.tts.export) || {}),
                format: $('#settings-tts-format').value,
                bitrate: parseInt($('#settings-tts-bitrate').value, 10) || 128000,
                split_chapters: $('#settings-tts-split').checked,
            },
            normalize: { use_llm: $('#settings-tts-use-llm').checked },
            accent: {
                enabled: $('#settings-tts-accent').checked,
                model_size: $('#settings-tts-accent-size').value,
            },
            pauses: {
                sentence: parseInt($('#settings-tts-pause-sentence').value, 10) || 0,
                paragraph: parseInt($('#settings-tts-pause-paragraph').value, 10) || 0,
                chapter: parseInt($('#settings-tts-pause-chapter').value, 10) || 0,
            },
        };
    }

    function renderAppearance() {
        $('#settings-theme').value = (currentSettings.app && currentSettings.app.theme) || 'dark';
    }

    async function saveAll(options) {
        const keepOpen = !!(options && options.keepOpen === true);
        const promptsObj = collectPrompts();
        promptsObj.tts_normalize = { system: $('#settings-tts-normalize-prompt').value };

        const newSettings = {
            ...currentSettings,
            app: {
                ...(currentSettings.app || {}),
                theme: $('#settings-theme').value,
            },
            asr: {
                ...(currentSettings.asr || {}),
                engine: $('#settings-engine').value,
                language: $('#settings-language').value,
                whisper: {
                    ...((currentSettings.asr && currentSettings.asr.whisper) || {}),
                    model: $('#settings-whisper-model').value,
                },
                diarization: {
                    ...((currentSettings.asr && currentSettings.asr.diarization) || {}),
                    enabled: $('#settings-diarization-enabled').checked,
                    num_speakers: parseInt($('#settings-diarization-speakers').value, 10) || 0,
                    threshold: parseFloat($('#settings-diarization-threshold').value) || 0.5,
                    embedding_model: $('#settings-diarization-model').value,
                    window_shift_ratio:
                        parseFloat($('#settings-diarization-shift').value) || 0.3,
                },
            },
            llm: {
                ...(currentSettings.llm || {}),
                profiles: collectProfiles(),
                temperature: parseFloat($('#settings-temperature').value) || 0.3,
                max_tokens: parseInt($('#settings-max-tokens').value, 10) || 4096,
            },
            prompts: promptsObj,
            tts: collectTTS(),
        };

        try {
            const res = await fetch('/api/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(newSettings),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                alert('Ошибка сохранения: ' + (data.error || res.statusText));
                return;
            }
            currentSettings = newSettings;
            // Применить тему сразу
            document.body.dataset.theme = newSettings.app.theme;
            // Перенастроить активный движок, если поменялся
            if (window.applyEngineFromSettings) {
                await window.applyEngineFromSettings(newSettings.asr.engine);
            }
            if (keepOpen) return;
            close();
        } catch (e) {
            alert('Ошибка: ' + e.message);
        }
    }

    function setupModalTabs() {
        $$('.modal-tab').forEach((tab) => {
            tab.addEventListener('click', () => {
                $$('.modal-tab').forEach((t) => t.classList.remove('modal-tab--active'));
                $$('.modal-panel').forEach((p) => p.classList.remove('modal-panel--active'));
                tab.classList.add('modal-tab--active');
                const target = tab.dataset.modalTab;
                $(`[data-modal-panel="${target}"]`).classList.add('modal-panel--active');
            });
        });
    }

    function setup() {
        $('#btn-settings').addEventListener('click', open);
        $$('[data-close]', $('#settings-modal')).forEach((el) =>
            el.addEventListener('click', close),
        );
        $('#btn-save-settings').addEventListener('click', () => saveAll());
        $('#btn-download-diarization').addEventListener('click', downloadDiarizationModels);
        $('#btn-add-profile').addEventListener('click', () => {
            const list = $('#profiles-list');
            const idx = list.children.length;
            const card = buildProfileCard({
                name: `profile-${idx + 1}`,
                api_provider: 'openai',
                base_url: 'https://api.openai.com/v1',
                api_key: '',
                models: [],
            }, idx);
            list.appendChild(card);
        });
        setupModalTabs();
    }

    document.addEventListener('DOMContentLoaded', setup);
    window.openSettings = open;
})();
