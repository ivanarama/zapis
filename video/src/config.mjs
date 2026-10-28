import fs from 'node:fs/promises';
import path from 'node:path';

export const FORMATS = {
  landscape: {width: 1280, height: 720},
  portrait: {width: 720, height: 1280},
};
export const PRESETS = {
  recording: {scale: 0.5, fps: 12, crf: 26},
  draft: {scale: 1, fps: 24, crf: 22},
  full: {scale: 1.5, fps: 24, crf: 19},
};
const kinds = new Set(['title', 'screenshot', 'compare', 'steps', 'flow', 'cta']);
const idPattern = /^[a-z0-9][a-z0-9-]{0,63}$/;

function requireText(value, label, limit = 240) {
  if (typeof value !== 'string' || !value.trim() || value.length > limit) {
    throw new Error(`${label}: нужен непустой текст длиной до ${limit} символов`);
  }
}

export function validateCampaign(campaign) {
  if (!campaign || campaign.schemaVersion !== 1) throw new Error('schemaVersion должен быть равен 1');
  if (!idPattern.test(campaign.id)) throw new Error('id: используйте латиницу, цифры и дефис');
  requireText(campaign.product, 'product', 100);
  requireText(campaign.brand?.name, 'brand.name', 50);
  if (!/^#[0-9a-f]{6}$/i.test(campaign.brand?.accent)) throw new Error('brand.accent: нужен цвет #RRGGBB');
  if (campaign.voice) {
    requireText(campaign.voice.name, 'voice.name', 100);
    if (!Number.isInteger(campaign.voice.rate) || campaign.voice.rate < -10 || campaign.voice.rate > 10) throw new Error('voice.rate: целое число от -10 до 10');
    if (!Number.isFinite(campaign.voice.gainDb) || campaign.voice.gainDb < -12 || campaign.voice.gainDb > 12) throw new Error('voice.gainDb: число от -12 до 12');
  }
  if (!Array.isArray(campaign.scenes) || campaign.scenes.length < 1 || campaign.scenes.length > 12) {
    throw new Error('В сценарии должно быть от 1 до 12 сцен');
  }
  const ids = new Set();
  for (const scene of campaign.scenes) {
    if (!idPattern.test(scene.id) || ids.has(scene.id)) throw new Error('У сцен должны быть уникальные id');
    ids.add(scene.id);
    if (!kinds.has(scene.type)) throw new Error(`Неизвестный тип сцены: ${scene.type}`);
    requireText(scene.title, `${scene.id}.title`, 95);
    if (scene.title.split('\n').length > 3) throw new Error(`${scene.id}: максимум три строки заголовка`);
    requireText(scene.kicker, `${scene.id}.kicker`, 55);
    requireText(scene.body, `${scene.id}.body`, 180);
    requireText(scene.narration, `${scene.id}.narration`, 420);
    if (!Number.isFinite(scene.seconds) || scene.seconds < 2 || scene.seconds > 45) {
      throw new Error(`${scene.id}.seconds: допустимо от 2 до 45 секунд`);
    }
    if (!Array.isArray(scene.evidence) || !scene.evidence.length || scene.evidence.some(e => typeof e !== 'string' || !e.trim())) {
      throw new Error(`${scene.id}: укажите evidence, на чём основано содержание сцены`);
    }
    if (scene.type === 'screenshot') {
      requireText(scene.image, `${scene.id}.image`);
      requireText(scene.imageCaption, `${scene.id}.imageCaption`, 160);
    }
    if (scene.type === 'compare') {
      if (!Array.isArray(scene.cards) || scene.cards.length !== 2) throw new Error(`${scene.id}: нужны две cards`);
      for (const card of scene.cards) {
        requireText(card.label, 'card.label', 40);
        requireText(card.value, 'card.value', 70);
      }
      requireText(scene.disclosure, `${scene.id}.disclosure`, 150);
    }
    if (scene.type === 'steps') {
      if (!Array.isArray(scene.steps) || scene.steps.length < 1 || scene.steps.length > 4) throw new Error(`${scene.id}: нужно от одного до четырёх шагов`);
      for (const step of scene.steps) requireText(step, `${scene.id}.steps`, 80);
    }
    if (scene.type === 'flow') {
      if (!Array.isArray(scene.nodes) || scene.nodes.length < 2 || scene.nodes.length > 4) throw new Error(`${scene.id}: нужно от двух до четырёх узлов схемы`);
      for (const node of scene.nodes) requireText(node, `${scene.id}.nodes`, 50);
    }
    if (scene.revealAt) {
      const count = scene.nodes?.length ?? scene.steps?.length ?? scene.cards?.length;
      if (!Array.isArray(scene.revealAt) || scene.revealAt.length !== count || scene.revealAt.some((n,i,a) => !Number.isFinite(n) || n < 0 || n >= scene.seconds || (i && n < a[i-1]))) throw new Error(`${scene.id}.revealAt: укажите секунды появления по числу элементов, в порядке возрастания`);
    }
    if (scene.type === 'cta') {
      requireText(scene.button, `${scene.id}.button`, 50);
      requireText(scene.contact, `${scene.id}.contact`, 80);
    }
    if (scene.metric) {
      if (!Number.isInteger(scene.metric.value) || scene.metric.value < 0 || scene.metric.value > 9999) throw new Error(`${scene.id}.metric.value: целое число от 0 до 9999`);
      requireText(scene.metric.label, `${scene.id}.metric.label`, 80);
    }
  }
  return campaign;
}

export async function resolveAsset(publicDir, relative) {
  if (typeof relative !== 'string' || !relative || relative.includes('\\') || relative.includes('%') || relative.includes(':') || relative.startsWith('/') || relative.split('/').some(p => !p || p === '.' || p === '..')) {
    throw new Error('Недопустимый путь к файлу: ' + relative);
  }
  const base = await fs.realpath(publicDir);
  const candidate = await fs.realpath(path.resolve(base, relative));
  const within = path.relative(base, candidate);
  if (within.startsWith('..') || path.isAbsolute(within)) throw new Error('Файл находится за пределами public');
  const stat = await fs.stat(candidate);
  if (!stat.isFile()) throw new Error('Ожидался файл: ' + relative);
  return candidate;
}

export async function loadCampaign(root, name = 'edo', publicDir = path.join(root, 'public')) {
  const file = idPattern.test(name) ? path.join(root, 'campaigns', name + '.json') : path.resolve(root, name);
  let campaign;
  try { campaign = JSON.parse(await fs.readFile(file, 'utf8')); }
  catch (error) { throw new Error(`Не удалось прочитать сценарий ${path.relative(root, file)}: ${error.message}`); }
  validateCampaign(campaign);
  for (const scene of campaign.scenes) {
    if (scene.image) await resolveAsset(publicDir, scene.image);
    if (scene.audio) await resolveAsset(publicDir, scene.audio);
  }
  return {campaign, file};
}

export function stamp(seconds, separator = '.') {
  const ms = Math.round(seconds * 1000);
  const hours = Math.floor(ms / 3600000);
  const minutes = Math.floor(ms / 60000) % 60;
  const secs = Math.floor(ms / 1000) % 60;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}${separator}${String(ms % 1000).padStart(3, '0')}`;
}

export function subtitles(scenes, fps, format = 'vtt') {
  let offset = 0;
  const cues = scenes.map((scene, index) => {
    const start = offset;
    offset += scene.frames / fps;
    return (format === 'srt' ? `${index + 1}\n` : '') + `${stamp(start, format === 'srt' ? ',' : '.')} --> ${stamp(offset, format === 'srt' ? ',' : '.')}\n${scene.narration}`;
  });
  return (format === 'vtt' ? 'WEBVTT\n\n' : '') + cues.join('\n\n') + '\n';
}

export function wavDuration(buffer) {
  if (buffer.toString('ascii', 0, 4) !== 'RIFF' || buffer.toString('ascii', 8, 12) !== 'WAVE') throw new Error('Ожидался WAV RIFF/WAVE');
  let rate = 0, size = 0;
  for (let cursor = 12; cursor + 8 <= buffer.length;) {
    const id = buffer.toString('ascii', cursor, cursor + 4);
    const bytes = buffer.readUInt32LE(cursor + 4);
    if (cursor + 8 + bytes > buffer.length) throw new Error('Оборванный WAV');
    if (id === 'fmt ' && bytes >= 16) rate = buffer.readUInt32LE(cursor + 16);
    if (id === 'data') size += bytes;
    cursor += 8 + bytes + bytes % 2;
  }
  if (!rate || !size) throw new Error('В WAV отсутствуют звуковые данные');
  return size / rate;
}
