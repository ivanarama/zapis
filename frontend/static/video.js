/* Native Zapis video workflow. Text and titles are always inserted as text. */
(function () {
    const $ = selector => document.querySelector(selector);
    const state = {campaign:null, engines:{}, settings:{}, busy:false, available:false, timer:null};
    const key = 'zapis-video-draft-v1';
    const labels = {screenshot:'Реальный экран',flow:'Анимированная схема',title:'Заголовок',steps:'Шаги',compare:'Сравнение',cta:'Предложение'};
    const node = (tag, text, className) => { const el=document.createElement(tag); if(text!==undefined) el.textContent=text; if(className) el.className=className; return el; };
    async function api(url, options={}) {
        const response=await fetch('/api/video/'+url,options);
        const data=await response.json();
        if(!response.ok) throw new Error(typeof data.detail==='string' ? data.detail : JSON.stringify(data.detail));
        return data;
    }
    const jsonPost = data => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    function error(message='') { $('#video-error').textContent=message; $('#video-error').hidden=!message; }
    function persist() {
        if(!state.campaign) return;
        $('#video-json').value=JSON.stringify(state.campaign,null,2);
        try { localStorage.setItem(key,JSON.stringify(state.campaign)); } catch {}
    }
    function field(label, value, change, multiline=false) {
        const wrapper=node('label',undefined,'field'); wrapper.append(node('span',label));
        const input=node(multiline?'textarea':'input'); input.value=value||''; if(multiline) input.rows=2;
        input.addEventListener('input',()=>{change(input.value);persist();}); wrapper.append(input);return wrapper;
    }
    function drawCampaign() {
        $('#video-product').value=state.campaign.product||'';
        const list=$('#video-scenes'); list.replaceChildren();
        state.campaign.scenes.forEach((scene,index)=>{
            const card=node('article',undefined,'video-scene');
            const header=node('header'); header.append(node('span',`${String(index+1).padStart(2,'0')} · ${labels[scene.type]||scene.type}`),node('span',`${scene.seconds} с минимум`));card.append(header);
            card.append(field('Текст в кадре',scene.title,value=>{scene.title=value;},true));
            card.append(field('Пояснение',scene.body,value=>{scene.body=value;},true));
            card.append(field('Текст диктора',scene.narration,value=>{scene.narration=value;},true));
            const controls=node('div',undefined,'video-row');
            for(const [label,accept,type] of [['Скриншот','image/png,image/jpeg,image/webp','image'],['Готовая озвучка','.wav','audio']]) {
                const button=node('label',label,'btn btn--ghost'); const input=node('input'); input.type='file';input.accept=accept;input.hidden=true;
                input.addEventListener('change',async()=>{
                    if(!input.files.length) return;
                    const data=new FormData();data.append('file',input.files[0]);button.setAttribute('aria-busy','true');
                    try { const uploaded=await api('assets',{method:'POST',body:data});scene[type]=uploaded.asset;
                        if(type==='image') {scene.type='screenshot';scene.imageCaption=scene.imageCaption||'Скриншот проекта';}
                        persist();drawCampaign();error();
                    } catch(e) {error(e.message);} finally {button.removeAttribute('aria-busy');}
                });button.append(input);controls.append(button);
            }
            if(scene.audio) {const clear=node('button','Использовать синтез','btn btn--ghost');clear.addEventListener('click',()=>{delete scene.audio;persist();drawCampaign();});controls.append(clear);}
            card.append(controls);
            if(scene.image) {
                const image=node('img');image.src='/api/video/asset?path='+encodeURIComponent(scene.image);image.alt=scene.imageCaption||'Скриншот сцены';image.className='video-source-image';card.append(image);
                card.append(node('p',scene.image,'video-image-name'));
            }
            if(scene.audio) card.append(node('p','Озвучка из файла: '+scene.audio,'video-image-name'));
            list.append(card);
        });persist();
    }
    function voice() {return {engine:$('#video-engine').value,speaker:$('#video-speaker').value,normalize:$('#video-normalize').checked,accent:$('#video-accent').checked,length_scale:Number($('#video-speed').value)};}
    function saveVoice() {try{localStorage.setItem('zapis-video-voice-v1',JSON.stringify(voice()));}catch{}}
    function showSample(result, play=false) {
        const sample=node('div',undefined,'video-sample');
        sample.append(node('p',`${result.engine} · ${result.speaker} · ${result.seconds} с`));
        const audio=node('audio');audio.controls=true;audio.preload='none';audio.src=result.audio;sample.append(audio);
        const use=node('button','Выбрать этот голос','btn btn--ghost');
        use.addEventListener('click',()=>{$('#video-engine').value=result.engine;applyEngine(result.speaker);saveVoice();});
        sample.append(use);$('#video-voice-samples').prepend(sample);
        if(play)audio.play().catch(()=>{});
    }
    function applyEngine(preferred) {
        const engine=$('#video-engine').value;
        const speakers=state.engines[engine]?.speakers||[];
        const select=$('#video-speaker');select.replaceChildren(...speakers.map(s=>{const o=node('option',s);o.value=s;return o;}));
        const configured=state.settings[engine]||{};
        const candidate=preferred||configured.speaker||configured.voice;
        if(speakers.includes(candidate)) select.value=candidate;
        $('#video-speed-field').hidden=engine!=='piper';
        $('#video-accent').disabled=engine!=='silero';
        $('#video-voice-hint').textContent=({silero:'При первом использовании загрузится модель Silero.',piper:'Голос загрузится один раз, затем работает без интернета. Значение больше 1 замедляет речь.',edge:'Нужен интернет. Доступность зависит от сервиса Edge.',yandex:'Используются ключ и настройки SpeechKit из раздела «Озвучка». Запросы оплачиваются по тарифу провайдера.',sber:'Используются ключи SaluteSpeech из раздела «Озвучка». Запросы оплачиваются по тарифу провайдера.'})[engine];
    }
    function busy(value) {
        state.busy=value;
        $('#video-storyboard').disabled=value||!state.available;
        $('#video-render').disabled=value||!state.available;
        $('#video-preview').disabled=value;
    }
    function showResult(job) {
        const container=$('#video-outputs');container.replaceChildren();
        for(const output of job.outputs||[]) {
            const card=node('article',undefined,'video-output');
            card.append(node('h3',`${output.format==='portrait'?'Вертикальный':'Горизонтальный'} · ${output.seconds.toFixed(1)} с`));
            if(output.video) {const video=node('video');video.controls=true;video.preload='metadata';video.src=output.video;video.poster=output.poster;const track=node('track');track.kind='subtitles';track.src=output.subtitles;track.srclang='ru';track.label='Русский';video.append(track);card.append(video);}
            else {const image=node('img');image.src=output.poster;image.alt='Первый кадр раскадровки';card.append(image);}
            const links=node('div',undefined,'video-row');
            for(const [label,url] of [['Все сцены',output.review],['Скачать MP4',output.video],['Сценарий',output.scenario],['Субтитры',output.subtitles]]) {
                if(!url) continue;const a=node('a',label);a.href=url;a.target='_blank';a.rel='noopener';links.append(a);
            }card.append(links);container.append(card);
        }$('#video-result').hidden=!(job.outputs||[]).length;
    }
    async function history() {
        const data=await api('jobs');const list=$('#video-history');list.replaceChildren();
        if(!data.jobs.length) list.append(node('p','Сборок пока нет. Начните с раскадровки.','hint'));
        for(const job of data.jobs) {
            const row=node('article',undefined,'video-history-item');
            const first=job.outputs?.[0];if(first) {const image=node('img');image.src=first.poster;image.alt='';row.append(image);}
            const text=node('div');text.append(node('p',job.product),node('small',`${new Date(job.created_at).toLocaleString('ru-RU')} · ${job.command==='render'?'Ролик':'Раскадровка'} · ${job.status==='done'?'Готово':job.status==='error'?'Ошибка':job.status==='interrupted'?'Прервано':'Сборка'}`));row.append(text);
            const open=node('button','Открыть','btn btn--ghost');open.addEventListener('click',()=>{showResult(job);error(job.status==='error'?job.message:'');$('#video-progress').textContent=job.message;if(job.status==='done')$('#video-result').scrollIntoView({behavior:'smooth',block:'start'});});row.append(open);list.append(row);
        }
        return data.jobs;
    }
    async function poll(identifier) {
        try {
            const job=await api('jobs/'+identifier);$('#video-progress').textContent=job.message;
            if(['done','error','interrupted'].includes(job.status)) {
                busy(false);if(job.status!=='done')error(job.message);else showResult(job);await history();return;
            }
            state.timer=setTimeout(()=>poll(identifier),1800);
        }catch(e){busy(false);error(e.message);}
    }
    async function start(command) {
        if(state.busy)return;
        error();busy(true);$('#video-progress').textContent='Проверяем сценарий и файлы…';
        try {const job=await api('jobs',jsonPost({campaign:state.campaign,command,preset:$('#video-preset').value,format:$('#video-format').value,voice:voice()}));saveVoice();await poll(job.id);}
        catch(e){busy(false);error(e.message);$('#video-progress').textContent='';}
    }
    document.addEventListener('DOMContentLoaded',async()=>{
        $('#video-product').addEventListener('input',()=>{if(state.campaign){state.campaign.product=$('#video-product').value;persist();}});
        $('#video-load-example').addEventListener('click',async()=>{try{if(state.campaign)localStorage.setItem(key+'-previous',JSON.stringify(state.campaign));state.campaign=await api('examples/'+$('#video-example').value);drawCampaign();error();}catch(e){error(e.message);}});
        $('#video-apply-json').addEventListener('click',()=>{try{const campaign=JSON.parse($('#video-json').value);if(!Array.isArray(campaign.scenes))throw new Error('В сценарии нужен массив scenes.');state.campaign=campaign;drawCampaign();error();}catch(e){error(e.message);}});
        $('#video-import-json').addEventListener('change',async event=>{try{const file=event.target.files[0];if(!file)return;const data=JSON.parse(await file.text());if(!Array.isArray(data.scenes))throw new Error('В сценарии нужен массив scenes.');state.campaign=data;drawCampaign();error();}catch(e){error(e.message);}});
        $('#video-download-json').addEventListener('click',()=>{if(!state.campaign)return;const url=URL.createObjectURL(new Blob([JSON.stringify(state.campaign,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download=(state.campaign.id||'campaign')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
        $('#video-engine').addEventListener('change',()=>{applyEngine();saveVoice();});$('#video-speaker').addEventListener('change',saveVoice);
        $('#video-speed').addEventListener('input',()=>{$('#video-speed-label').textContent=Number($('#video-speed').value).toFixed(2);saveVoice();});
        $('#video-preview').addEventListener('click',async()=>{
            error();const button=$('#video-preview');button.disabled=true;button.textContent='Создаём пробу…';
            try{const result=await api('preview',jsonPost({text:$('#video-preview-text').value,voice:voice()}));saveVoice();showSample(result,true);}
            catch(e){error(e.message);}finally{button.disabled=false;button.textContent='Прослушать пробу';}
        });
        $('#video-storyboard').addEventListener('click',()=>start('storyboard'));$('#video-render').addEventListener('click',()=>start('render'));
        if(location.hash==='#video')$('.mode-btn[data-mode="video"]').click();
        try{
            const [status,voices]=await Promise.all([api('status'),fetch('/api/tts/voices').then(r=>r.json())]);
            state.available=status.available;state.engines=voices.engines||{};state.settings=voices.tts||{};
            $('#video-health').textContent=status.available?'Инструменты готовы':status.help;
            let preferred;try{preferred=JSON.parse(localStorage.getItem('zapis-video-voice-v1'));}catch{}
            $('#video-engine').value=preferred?.engine||voices.engine||'piper';applyEngine(preferred?.speaker);
            if(preferred){$('#video-normalize').checked=preferred.normalize;$('#video-accent').checked=preferred.accent;$('#video-speed').value=preferred.length_scale;$('#video-speed-label').textContent=Number(preferred.length_scale).toFixed(2);}
            try{const saved=JSON.parse(localStorage.getItem(key));if(Array.isArray(saved?.scenes))state.campaign=saved;}catch{}
            state.campaign=state.campaign||await api('examples/edo-short');drawCampaign();busy(false);
            const samples=await api('previews');samples.previews.slice().reverse().forEach(sample=>showSample(sample));
            const jobs=await history();const running=jobs.find(j=>['running','queued'].includes(j.status));if(running){busy(true);poll(running.id);}else if(jobs[0]?.status==='done')showResult(jobs[0]);
        }catch(e){error(e.message);}
    });
})();
