/**
 * SSE-стриминг через fetch + ReadableStream.
 * Сервер возвращает строки `data: {json}\n\n` с полями {token} | {error} | {done}.
 *
 * window.streamLLM(payload, callbacks) → AbortController
 */
(function () {
    async function streamLLM(payload, callbacks) {
        const ctrl = new AbortController();
        const onToken = callbacks.onToken || (() => {});
        const onDone = callbacks.onDone || (() => {});
        const onError = callbacks.onError || (() => {});

        try {
            const res = await fetch('/api/llm/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                signal: ctrl.signal,
            });

            if (!res.ok) {
                let errMsg = `HTTP ${res.status}`;
                try {
                    const j = await res.json();
                    errMsg = j.error || errMsg;
                } catch (_) { /* ignore */ }
                onError(errMsg);
                return ctrl;
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                // Разбиваем по событиям SSE — двойной перевод строки
                let idx;
                while ((idx = buffer.indexOf('\n\n')) >= 0) {
                    const chunk = buffer.slice(0, idx);
                    buffer = buffer.slice(idx + 2);
                    const line = chunk.split('\n').find((l) => l.startsWith('data:'));
                    if (!line) continue;
                    const dataStr = line.slice(5).trim();
                    if (!dataStr) continue;
                    let data;
                    try { data = JSON.parse(dataStr); } catch (_) { continue; }
                    if (data.error) { onError(data.error); return ctrl; }
                    if (data.done) { onDone(); return ctrl; }
                    if (data.token) onToken(data.token);
                }
            }
            onDone();
        } catch (e) {
            if (e.name === 'AbortError') return ctrl;
            onError(e.message || String(e));
        }
        return ctrl;
    }

    window.streamLLM = streamLLM;
})();
