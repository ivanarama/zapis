const fs = require('fs');
const path = require('path');
const {createHash} = require('node:crypto');

const args = process.argv.slice(2);
if (!args.length || args[0] === '--help') {
  console.log('node format_article.cjs article.md [--repo-root DIR --repo-url URL] [--image-scale 2]');
  process.exit(args.length ? 0 : 1);
}
const sourcePath = path.resolve(args.shift());
const options = {};
while (args.length) {
  const key = args.shift();
  if (!['--repo-root','--repo-url','--image-scale'].includes(key) || !args.length) throw new Error('Неизвестный или неполный аргумент: '+key);
  options[key] = args.shift();
}
if (Boolean(options['--repo-root']) !== Boolean(options['--repo-url'])) throw new Error('Укажите оба --repo-root и --repo-url');
const imageScale = Number(options['--image-scale'] || 1);
if (!(imageScale > 0)) throw new Error('Неверный --image-scale');
const imageManifest = [];

if (!fs.existsSync(sourcePath)) {
  throw new Error(`Файл не найден: ${sourcePath}`);
}

if (!/\.md$/i.test(sourcePath)) {
  throw new Error('Ожидается путь к Markdown-файлу с расширением .md.');
}

const targetPath = sourcePath.replace(/\.md$/i, '_infostart.html');
const previewPath = sourcePath.replace(/\.md$/i, '_preview.html');
const manifestPath = sourcePath.replace(/\.md$/i, '_images.json');

const source = fs.readFileSync(sourcePath, 'utf8').replace(/\r\n/g, '\n');
const sourceLines = source.split('\n');

/*
 * В исходнике есть служебный блок для полей публикации, затем основной H1.
 * В HTML для поля «Основной текст» заголовок, служебный блок и план иллюстраций
 * не включаются: их редактор Infostart принимает отдельно.
 */
const h1Indexes = sourceLines
  .map((line, index) => (line.startsWith('# ') ? index : -1))
  .filter((index) => index >= 0);

if (h1Indexes.length === 0) {
  throw new Error('Не найден заголовок основного текста статьи.');
}

const articleHeadingIndex = h1Indexes.length >= 2 ? h1Indexes[1] : h1Indexes[0];
const bodyStart = articleHeadingIndex + 1;
const illustrationPlan = sourceLines.findIndex(
  (line, index) => index > bodyStart && line === '## План иллюстраций для редактора',
);
const bodyEnd = illustrationPlan >= 0 ? illustrationPlan : sourceLines.length;
const lines = sourceLines.slice(bodyStart, bodyEnd);
const articleTitle = sourceLines[articleHeadingIndex].replace(/^#\s+/, '');

const INLINE_CODE_STYLE =
  'background: #f0f0f0; padding: 2px 6px; border-radius: 3px;';
const TABLE_CODE_STYLE =
  'background: #fff0f6; color: #d63384; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-family: monospace;';
const TABLE_WRAPPER_STYLE =
  'max-width: 1000px; margin: 20px auto; font-family: sans-serif; background: #ffffff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.1); border: 1px solid #eee;';
const TABLE_STYLE = 'width: 100%; border-collapse: collapse; text-align: left;';
const TABLE_HEAD_ROW_STYLE =
  'background-color: #f8f9fa; border-bottom: 2px solid #dee2e6;';
const TABLE_HEAD_CELL_STYLE =
  'padding: 18px 24px; color: #495057; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;';
const TABLE_ROW_STYLE = 'border-bottom: 1px solid #f0f0f0;';
const TABLE_CELL_STYLE = 'padding: 20px 24px; line-height: 1.5; color: #333;';
const SPACER = '<p>&nbsp;</p>';
const supportedCodeLanguages = new Set(['bash', 'yaml', 'bsl', 'go', 'sql']);

function escapeHtml(value) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function replaceTypography(value) {
  return value
    .replace(/—/g, '&mdash;')
    .replace(/«/g, '&laquo;')
    .replace(/»/g, '&raquo;')
    .replace(/\.\.\./g, '&hellip;')
    .replace(/→/g, '&rarr;')
    .replace(/–/g, '&ndash;');
}

function escapeText(value) {
  return replaceTypography(escapeHtml(value));
}

function formatEmphasis(value) {
  return value
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>');
}

function inlineMarkdown(value, options = {}) {
  const fragments = [];
  const codeStyle = options.tableCell ? TABLE_CODE_STYLE : INLINE_CODE_STYLE;

  let result = value.replace(/`([^`]+)`/g, (_, code) => {
    const token = `@@FRAGMENT_${fragments.length}@@`;
    fragments.push(`<code style="${codeStyle}">${escapeHtml(code)}</code>`);
    return token;
  });

  result = result.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
    const token = `@@FRAGMENT_${fragments.length}@@`;
    const labelHtml = formatEmphasis(escapeText(label));
    fragments.push(`<a href="${escapeHtml(publicLink(href))}">${labelHtml}</a>`);
    return token;
  });

  result = formatEmphasis(escapeText(result));

  // Links can contain an earlier code fragment in their label.
  for (let index=fragments.length-1; index>=0; index--) {
    result = result.replace(`@@FRAGMENT_${index}@@`, fragments[index]);
  }

  return result;
}

function publicLink(href) {
  if (!options['--repo-root'] || /^(?:[a-z][a-z\d+.-]*:|\/\/|#)/i.test(href)) return href;
  const [file, ...fragment] = href.split('#');
  const resolved = path.resolve(path.dirname(sourcePath),file);
  const relative = path.relative(path.resolve(options['--repo-root']),resolved);
  if (relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('Ссылка выходит из репозитория: '+href);
  const base = fs.existsSync(resolved) && fs.statSync(resolved).isDirectory()
    ? options['--repo-url'].replace('/blob/','/tree/') : options['--repo-url'];
  return base.replace(/\/?$/,'/') + relative.split(path.sep).map(encodeURIComponent).join('/') + (fragment.length ? '#'+fragment.join('#') : '');
}

function isTableSeparator(line) {
  if (!line.trim().startsWith('|')) return false;
  const cells = line
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((cell) => cell.trim());
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function parseTableRow(line) {
  const row = line.trim().replace(/^\||\|$/g, '');
  const cells = [];
  let cell = '', inCode = false;
  for (let i=0; i<row.length; i++) {
    if (row[i] === '\\' && row[i+1] === '|') {cell += '|'; i++; continue;}
    if (row[i] === '`') inCode = !inCode;
    if (row[i] === '|' && !inCode) {cells.push(cell.trim()); cell='';}
    else cell += row[i];
  }
  cells.push(cell.trim());
  return cells;
}

function beginsBlock(line, nextLine = '') {
  return (
    line.trim() === '' ||
    /^#{2,3}\s/.test(line) ||
    /^```/.test(line) ||
    /^>\s?/.test(line) ||
    /^-\s/.test(line) ||
    /^\d+\.\s/.test(line) ||
    /^!\[[^\]]*\]\([^)]+\)$/.test(line.trim()) ||
    /^<iframe\b/i.test(line.trim()) ||
    (line.trim().startsWith('|') && isTableSeparator(nextLine))
  );
}

function renderTable(header, rows) {
  const output = [];
  output.push(`<div style="${TABLE_WRAPPER_STYLE}">`);
  output.push(`<table style="${TABLE_STYLE}">`);
  output.push('    <thead>');
  output.push(`        <tr style="${TABLE_HEAD_ROW_STYLE}">`);
  header.forEach((cell) => {
    output.push(
      `            <th style="${TABLE_HEAD_CELL_STYLE}">${inlineMarkdown(cell, { tableCell: true })}</th>`,
    );
  });
  output.push('        </tr>');
  output.push('    </thead>');
  output.push('    <tbody>');
  rows.forEach((row, rowIndex) => {
    const rowStyle = rowIndex === rows.length - 1 ? '' : ` style="${TABLE_ROW_STYLE}"`;
    output.push(`        <tr${rowStyle}>`);
    row.forEach((cell) => {
      output.push(
        `            <td style="${TABLE_CELL_STYLE}">${inlineMarkdown(cell, { tableCell: true })}</td>`,
      );
    });
    output.push('        </tr>');
  });
  output.push('    </tbody>');
  output.push('</table>');
  output.push('</div>');
  return output;
}

function hasFollowingContent(fromIndex) {
  return lines.slice(fromIndex).some((line) => line.trim() && line.trim() !== '---');
}

const html = [];
html.push('<!--');
html.push('  HTML-фрагмент для режима «Источник» в поле «Основной текст» Infostart.');
html.push('  Заголовок и анонс заполняются в отдельных полях публикации.');
html.push('  Комментарии ВСТАВИТЬ ВИДЕО/ИЛЛЮСТРАЦИЮ отмечают места медиаматериалов.');
html.push('-->');

let contentStarted = false;
let index = 0;
while (index < lines.length) {
  const line = lines[index];
  const trimmed = line.trim();

  if (!trimmed) {
    if (
      contentStarted &&
      html[html.length - 1] !== SPACER &&
      hasFollowingContent(index + 1)
    ) {
      html.push(SPACER);
    }
    index += 1;
    continue;
  }

  if (trimmed === '---') {
    index += 1;
    continue;
  }

  const heading = line.match(/^(#{2,3})\s+(.+)$/);
  if (heading) {
    html.push(`<p><strong>${inlineMarkdown(heading[2])}</strong></p>`);
    contentStarted = true;
    index += 1;
    continue;
  }

  const fence = line.match(/^```([^\s]*)\s*$/);
  if (fence) {
    const language = fence[1].toLowerCase();
    const code = [];
    index += 1;
    while (index < lines.length && !/^```/.test(lines[index])) {
      code.push(lines[index]);
      index += 1;
    }
    if (index >= lines.length) throw new Error('Незакрытый блок кода: '+language);
    index += 1;
    const classAttribute = supportedCodeLanguages.has(language)
      ? ` class="language-${language}"`
      : '';
    html.push('<pre>');
    html.push(`<code${classAttribute}>${escapeHtml(code.join('\n'))}</code></pre>`);
    contentStarted = true;
    continue;
  }

  if (/^>\s?/.test(line)) {
    const quoteLines = [];
    while (index < lines.length && /^>\s?/.test(lines[index])) {
      quoteLines.push(lines[index].replace(/^>\s?/, '').trim());
      index += 1;
    }

    const firstLine = quoteLines[0] || '';
    const spoilerHeading = firstLine.match(/^\*\*([^*]+)\*\*$/);
    if (spoilerHeading && quoteLines.length > 1) {
      html.push('<div class="spoiler">');
      html.push('<div class="spoiler-title">');
      html.push('<div class="spoiler-toggle show-icon">&nbsp;</div>');
      html.push(`&nbsp;<strong>${escapeText(spoilerHeading[1])}</strong></div>`);
      html.push('<div class="spoiler-content" style="display: none;">');
      html.push(`<p>${inlineMarkdown(quoteLines.slice(1).join(' '))}</p>`);
      html.push('</div>');
      html.push('</div>');
    } else if (/^\*\*(Видео|Иллюстрация)/.test(firstLine)) {
      const note = quoteLines.join(' ').replace(/\*\*/g, '');
      html.push(`<!-- ВСТАВИТЬ ${escapeHtml(note)} -->`);
    } else {
      html.push(`<p>${inlineMarkdown(quoteLines.join(' '))}</p>`);
    }
    contentStarted = true;
    continue;
  }

  if (trimmed.startsWith('|') && isTableSeparator(lines[index + 1] || '')) {
    const header = parseTableRow(line);
    const rows = [];
    index += 2;
    while (index < lines.length && lines[index].trim().startsWith('|')) {
      rows.push(parseTableRow(lines[index]));
      index += 1;
    }
    html.push(...renderTable(header, rows));
    contentStarted = true;
    continue;
  }

  if (/^-\s/.test(line)) {
    const items = [];
    while (index < lines.length && /^-\s/.test(lines[index])) {
      let item = lines[index].replace(/^-\s+/, '');
      item = item.replace(/^\[ \]\s*/, '☐ ');
      item = item.replace(/^\[x\]\s*/i, '☑ ');
      items.push(`- ${inlineMarkdown(item)}<br />`);
      index += 1;
    }
    html.push(`<p>${items.join('\n')}</p>`);
    contentStarted = true;
    continue;
  }

  if (/^\d+\.\s/.test(line)) {
    const items = [];
    while (index < lines.length && /^\d+\.\s/.test(lines[index])) {
      const match = lines[index].match(/^(\d+)\.\s+(.+)$/);
      items.push(`${match[1]}. ${inlineMarkdown(match[2])}<br />`);
      index += 1;
    }
    html.push(`<p>${items.join('\n')}</p>`);
    contentStarted = true;
    continue;
  }

  const image = trimmed.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
  if (image) {
    const entry = {source:image[2],alt:image[1],caption:'Рисунок '+(imageManifest.length+1)+'. '+image[1]};
    if (!/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(image[2])) {
      const bytes = fs.readFileSync(path.resolve(path.dirname(sourcePath),image[2]));
      entry.sha256 = createHash('sha256').update(bytes).digest('hex');
      if (bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) {
        entry.pixelWidth = bytes.readUInt32BE(16);
        entry.pixelHeight = bytes.readUInt32BE(20);
        entry.cssWidth = Math.round(entry.pixelWidth/imageScale);
        entry.cssHeight = Math.round(entry.pixelHeight/imageScale);
      }
    }
    imageManifest.push(entry);
    const width = entry.cssWidth ? ` width="${entry.cssWidth}"` : '';
    html.push(`<p style="text-align: center;"><img alt="${escapeHtml(image[1])}" src="${escapeHtml(image[2])}"${width} style="max-width: 100%; height: auto;" /></p>`);
    html.push(`<p style="text-align: center;"><em>${escapeText(entry.caption)}</em></p>`);
    contentStarted = true;
    index += 1;
    continue;
  }

  if (/^<iframe\b[^>]*><\/iframe>$/i.test(trimmed)) {
    html.push(trimmed);
    contentStarted = true;
    index += 1;
    continue;
  }

  const paragraph = [trimmed];
  index += 1;
  while (
    index < lines.length &&
    lines[index].trim() !== '' &&
    !beginsBlock(lines[index], lines[index + 1] || '')
  ) {
    paragraph.push(lines[index].trim());
    index += 1;
  }
  html.push(`<p>${inlineMarkdown(paragraph.join(' '))}</p>`);
  contentStarted = true;
}

while (html[html.length - 1] === SPACER) {
  html.pop();
}

const result = `${html.join('\n')}\n`;
fs.writeFileSync(targetPath, result, 'utf8');

fs.writeFileSync(manifestPath,JSON.stringify(imageManifest,null,2)+'\n','utf8');
const preview = `<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(articleTitle)} — предпросмотр</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f7;color:#263238;font:16px/1.65 Arial,sans-serif}
header{max-width:1064px;margin:28px auto 0;padding:0 32px;color:#607080;font-size:13px}
article{max-width:1064px;margin:12px auto 40px;padding:36px 32px;background:white;border:1px solid #e1e5e9;border-radius:8px}
h1{font-size:28px;line-height:1.3;margin:0 0 30px}p{margin:8px 0}p:has(img){margin:20px 0 8px}p:has(>em:only-child){font-size:14px;color:#627083}
img{display:inline-block;vertical-align:middle}pre{overflow:auto;padding:18px 20px;background:#f6f8fa;border:1px solid #e3e8ee;border-radius:6px;font:14px/1.55 Consolas,monospace}
a{color:#1769aa}p code,a{overflow-wrap:anywhere}.spoiler-title{cursor:pointer;font-weight:bold}.spoiler-content{padding:12px;border-left:3px solid #d4dce5}
@media(max-width:700px){header{padding:0 18px}article{padding:24px 18px;border-radius:0}h1{font-size:24px}}
</style></head><body><header>Локальный предпросмотр. В редактор Инфостарта вставляется отдельный файл *_infostart.html.</header>
<article><h1>${escapeHtml(articleTitle)}</h1>
${result}</article>
<script>document.querySelectorAll('.spoiler-title').forEach(function(el){el.addEventListener('click',function(){var body=el.nextElementSibling;body.style.display=body.style.display==='none'?'block':'none';});});</script>
</body></html>\n`;
fs.writeFileSync(previewPath,preview,'utf8');

console.log(`Создан файл: ${targetPath}`);
console.log(`Предпросмотр: ${previewPath}`);
console.log(`Изображения: ${manifestPath} (${imageManifest.length})`);
