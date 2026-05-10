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
            renderASR();
            renderLLM();
            renderPrompts();
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
            $('.prompt-system', node).value = cur.system || '';
            $('.prompt-system', node).placeholder = def.system || '';
            $('.prompt-user', node).value = cur.user_template || '';
            $('.prompt-user', node).placeholder = def.user_template || '';
            root.appendChild(node);
        });

        // Custom system — отдельной карточкой
        const customNode = tmpl.content.firstElementChild.cloneNode(true);
        customNode.dataset.key = 'custom_system';
        $('.prompt-card__title', customNode).textContent = 'Свободный сценарий — system';
        $('.prompt-system', customNode).style.display = 'none';
        customNode.querySelector('.field:first-of-type').style.display = 'none';
        $('.prompt-user', customNode).value = currentPrompts.custom_system || '';
        $('.prompt-user', customNode).placeholder = promptDefaults.custom_system || '';
        const userLabel = customNode.querySelectorAll('.field span')[1];
        if (userLabel) userLabel.textContent = 'System prompt для свободных вопросов';
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
                    system: $('.prompt-system', card).value,
                    user_template: $('.prompt-user', card).value,
                };
            }
        });
        return out;
    }

    function renderAppearance() {
        $('#settings-theme').value = (currentSettings.app && currentSettings.app.theme) || 'dark';
    }

    async function saveAll() {
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
            },
            llm: {
                ...(currentSettings.llm || {}),
                profiles: collectProfiles(),
                temperature: parseFloat($('#settings-temperature').value) || 0.3,
                max_tokens: parseInt($('#settings-max-tokens').value, 10) || 4096,
            },
            prompts: collectPrompts(),
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
            // Применить тему сразу
            document.body.dataset.theme = newSettings.app.theme;
            // Перенастроить активный движок, если поменялся
            if (window.applyEngineFromSettings) {
                await window.applyEngineFromSettings(newSettings.asr.engine);
            }
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
        $('#btn-save-settings').addEventListener('click', saveAll);
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
