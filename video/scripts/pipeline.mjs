import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {loadCampaign, resolveAsset, PRESETS, FORMATS, wavDuration, subtitles} from '../src/config.mjs';
import {makeReview} from './review.mjs';

const hash = data => crypto.createHash('sha256').update(data).digest('hex');
export function browserExecutable() {
  return process.env.REMOTION_BROWSER_EXECUTABLE || null;
}
export async function findBrowser() {
  if (browserExecutable()) {
    await fs.access(browserExecutable());
    return browserExecutable();
  }
  const candidates = process.platform === 'win32' ? [
    path.join(process.env['PROGRAMFILES(X86)'] || 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe'),
    path.join(process.env.PROGRAMFILES || 'C:/Program Files', 'Microsoft/Edge/Application/msedge.exe'),
    path.join(process.env.PROGRAMFILES || 'C:/Program Files', 'Google/Chrome/Application/chrome.exe'),
  ] : ['/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'];
  for (const file of candidates) {
    try { await fs.access(file); return file; } catch {}
  }
  return null;
}

async function prepare(root, loaded, preset, voice, runtimePublic, publicRoot) {
  const campaign = structuredClone(loaded.campaign);
  campaign.fps = preset.fps;
  const audioDir = path.join(runtimePublic, 'audio');
  const files = new Set(['assets/fonts/NotoSans.ttf', ...campaign.scenes.filter(s => s.image).map(s => s.image)]);
  for (const name of files) {
    const target = path.join(runtimePublic, name);
    await fs.mkdir(path.dirname(target), {recursive: true});
    await fs.copyFile(await resolveAsset(publicRoot, name), target);
  }
  for (const scene of campaign.scenes) {
    if (voice === 'none') delete scene.audio;
    if (scene.audio) {
      await fs.mkdir(audioDir, {recursive: true});
      await fs.copyFile(await resolveAsset(publicRoot, scene.audio), path.join(audioDir, scene.id + '.wav'));
      scene.audio = 'audio/' + scene.id + '.wav';
    }
    let seconds = scene.seconds;
    if (scene.audio) {
      const source = await resolveAsset(runtimePublic, scene.audio);
      const duration = wavDuration(await fs.readFile(source));
      seconds = Math.max(seconds, duration + .55);
    }
    scene.frames = Math.ceil(seconds * campaign.fps);
    scene.seconds = scene.frames / campaign.fps;
  }
  return campaign;
}

export async function generate(root, options) {
  const {command, campaign: name = 'edo', preset: presetName = 'draft', format = 'landscape'} = options;
  const started = performance.now();
  const publicRoot = options.publicDir || path.join(root, 'public');
  const workRoot = options.workDir || path.join(root, '.work');
  const buildRoot = options.buildDir || path.join(root, 'build');
  const loaded = await loadCampaign(root, name, publicRoot);
  const preset = PRESETS[presetName];
  if (!preset) throw new Error('Неизвестный preset: ' + presetName);
  const formats = format === 'all' ? Object.keys(FORMATS) : [format];
  if (formats.some(f => !FORMATS[f])) throw new Error('Формат: landscape, portrait или all');
  const voice = command === 'storyboard' ? 'none' : (options.voice || 'files');
  if (!['none', 'files'].includes(voice)) throw new Error('voice: files или none. Озвучку создаёт Zapis');
  const runId = new Date().toISOString().replace(/[:.]/g, '-') + '-' + crypto.randomBytes(2).toString('hex');
  console.log(`\n${command === 'render' ? 'Сборка ролика' : 'Раскадровка'}: ${loaded.campaign.product}`);
  console.log(`Режим: ${presetName}, ${preset.fps} кадров/с, формат ${format}`);
  const runtimePublic = path.join(workRoot, 'runtime-public', runId);
  const campaign = await prepare(root, loaded, preset, voice, runtimePublic, publicRoot);
  const {bundle} = await import('@remotion/bundler');
  const {openBrowser, selectComposition, renderStill, renderMedia, getVideoMetadata} = await import('@remotion/renderer');
  const serveUrl = await bundle({entryPoint: path.join(root, 'src/index.jsx'), publicDir: runtimePublic, outDir: path.join(workRoot, 'bundles', runId)});
  const browser = await openBrowser('chrome', {browserExecutable: await findBrowser()});
  const engineFiles = ['package.json', 'package-lock.json'];
  for (const directory of ['src','scripts']) for (const name of (await fs.readdir(path.join(root,directory))).sort()) if (/\.(?:mjs|jsx|ps1)$/.test(name)) engineFiles.push(directory+'/'+name);
  const engineHash = hash(Buffer.concat(await Promise.all(engineFiles.map(file => fs.readFile(path.join(root, file))))));
  const jobs = [];
  try {
    for (const aspect of formats) {
      const directory = ['runs', runId, campaign.id, aspect].join('/');
      const output = path.join(buildRoot, directory);
      await fs.mkdir(path.join(output, 'frames'), {recursive: true});
      const inputProps = {campaign, format: aspect};
      const composition = await selectComposition({serveUrl, id: 'Video-' + aspect, inputProps, puppeteerInstance: browser});
      const frames = [];
      let cursor = 0;
      for (const [index, scene] of campaign.scenes.entries()) {
        const filename = `frames/${String(index + 1).padStart(2, '0')}-${scene.id}.png`;
        const representative = scene.revealAt ? Math.round((Math.max(...scene.revealAt)+.8)*campaign.fps) : Math.round(campaign.fps*2);
        await renderStill({serveUrl, composition, inputProps, output: path.join(output, filename), frame: cursor + Math.min(scene.frames-10, representative), scale: preset.scale, puppeteerInstance: browser});
        frames.push(filename);
        cursor += scene.frames;
      }
      await fs.copyFile(path.join(output, frames[0]), path.join(output, 'poster.png'));
      const inputs = [];
      const assets = new Set(['assets/fonts/NotoSans.ttf']);
      for (const scene of campaign.scenes) {
        if (scene.image) assets.add(scene.image);
        if (scene.audio) assets.add(scene.audio);
      }
      for (const asset of assets) {
        const file = await resolveAsset(runtimePublic, asset);
        inputs.push({path: asset, sha256: hash(await fs.readFile(file))});
      }
      for (const scene of campaign.scenes.filter(s => s.audio)) {
        await fs.mkdir(path.join(output, 'audio'), {recursive: true});
        await fs.copyFile(await resolveAsset(runtimePublic, scene.audio), path.join(output, 'audio', scene.id + '.wav'));
      }
      const seconds = campaign.scenes.reduce((n, s) => n + s.frames, 0) / campaign.fps;
      const manifest = {
        schema: 'zapis-video/v1', runId, campaign: campaign.id, product: campaign.product,
        command, preset: presetName, format: aspect,
        width: Math.round(FORMATS[aspect].width * preset.scale), height: Math.round(FORMATS[aspect].height * preset.scale), fps: campaign.fps, seconds,
        sourceSha256: hash(await fs.readFile(loaded.file)), engineSha256: engineHash,
        voice: {enabled: campaign.scenes.some(s => s.audio), provider: options.tts?.engine || voice, name: options.tts?.speaker || null},
        assets: inputs, renderedVideo: command === 'render', artifacts: [],
      };
      if (command === 'render') {
        let last = -1;
        console.log(`${aspect}: ${seconds.toFixed(1)} с, ${manifest.width} × ${manifest.height}`);
        await renderMedia({serveUrl, composition, inputProps, outputLocation: path.join(output, 'video.mp4'), codec: 'h264', pixelFormat: 'yuv420p', audioCodec: 'aac', audioBitrate: '160k', crf: preset.crf, scale: preset.scale, concurrency: 2, puppeteerInstance: browser, onProgress: ({progress}) => {
          const percent = Math.floor(progress * 10) * 10;
          if (percent > last) {last = percent; console.log(`Рендер: ${percent}%`);}
        }});
        if (manifest.voice.enabled) {
          console.log('Проверяем звук и выравниваем громкость...');
          manifest.voice.normalization = await (await import('./audio.mjs')).normalizeNarration(path.join(output, 'video.mp4'));
        }
        const actual = await getVideoMetadata(path.join(output, 'video.mp4'));
        if (actual.width !== manifest.width || actual.height !== manifest.height || Math.abs(actual.durationInSeconds - seconds) > .12) throw new Error('Размер или длительность готового видео не соответствуют сценарию');
      }
      await fs.writeFile(path.join(output, 'scenario.json'), JSON.stringify(campaign, null, 2) + '\n');
      await fs.writeFile(path.join(output, 'subtitles.vtt'), subtitles(campaign.scenes, campaign.fps));
      await fs.writeFile(path.join(output, 'subtitles.srt'), subtitles(campaign.scenes, campaign.fps, 'srt'));
      await fs.writeFile(path.join(output, 'narration.txt'), campaign.scenes.map(s => `${s.kicker}\n${s.narration}\n`).join('\n'));
      manifest.elapsedSeconds = Math.round((performance.now() - started) / 1000);
      const names = ['poster.png', 'scenario.json', 'subtitles.vtt', 'subtitles.srt', 'narration.txt', ...frames, ...(command === 'render' ? ['video.mp4'] : []), ...campaign.scenes.filter(s => s.audio).map(s => `audio/${s.id}.wav`)];
      for (const name of names) manifest.artifacts.push({file: name, sha256: hash(await fs.readFile(path.join(output, name)))});
      await fs.writeFile(path.join(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
      await fs.writeFile(path.join(output, 'review.html'), makeReview(campaign, manifest, frames));
      jobs.push({directory, command, product: campaign.product, format: aspect, preset: presetName, seconds, voice: manifest.voice.enabled});
      console.log('Результат: ' + directory + '/review.html');
    }
    const latest = {runId, campaign: campaign.id, jobs};
    await fs.writeFile(path.join(buildRoot, 'latest.json'), JSON.stringify(latest, null, 2) + '\n');
    let history = [];
    try { history = JSON.parse(await fs.readFile(path.join(buildRoot, 'history.json'), 'utf8')); } catch {}
    await fs.writeFile(path.join(buildRoot, 'history.json'), JSON.stringify([...history, ...jobs], null, 2) + '\n');
    console.log(`Готово за ${Math.round((performance.now() - started) / 1000)} с.`);
    return latest;
  } finally { await browser.close({silent: true}); }
}
