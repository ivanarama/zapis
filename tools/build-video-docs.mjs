import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
for (const name of ['article', 'recording']) {
  const file = path.join(root, 'docs/video', name + '.md');
  // The article mixes original 1x release screenshots with browser captures.
  // Use natural widths, bounded by the article column, so the 1C table stays readable.
  const result = spawnSync(process.execPath, [path.join(root, 'tools/format_article.cjs'), file, '--image-scale', '1'], {encoding: 'utf8'});
  if (result.status !== 0) throw new Error(result.stderr || result.stdout);
  console.log(result.stdout.trim());
}
await fs.rename(path.join(root, 'docs/video/recording_preview.html'), path.join(root, 'docs/video/recording.html'));
const guidePath = path.join(root, 'docs/video/recording.html');
const guide = await fs.readFile(guidePath, 'utf8');
await fs.writeFile(guidePath, guide.replace('Локальный предпросмотр. В редактор Инфостарта вставляется отдельный файл *_infostart.html.', 'Zapis · Сценарий записи процесса. Статья находится в article_preview.html.'));
for (const name of ['recording_infostart.html', 'recording_images.json']) await fs.unlink(path.join(root, 'docs/video', name));
