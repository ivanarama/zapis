// The Python service passes a private, validated job file, never shell text.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const options = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
await fs.mkdir(options.workDir, {recursive: true});
process.env.TEMP = process.env.TMP = process.env.TMPDIR = options.workDir;
try {
  if (options.command === 'validate') {
    await (await import('../src/config.mjs')).loadCampaign(root, options.campaign, options.publicDir);
  } else {
    const result = await (await import('./pipeline.mjs')).generate(root, options);
    await fs.writeFile(options.resultFile, JSON.stringify(result));
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
