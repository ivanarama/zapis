import fs from 'node:fs/promises';
import path from 'node:path';
import {spawnSync} from 'node:child_process';

export async function normalizeNarration(file) {
  const {RenderInternals} = await import('@remotion/renderer');
  // The renderer version is pinned. Use its own platform binary, not a global FFmpeg.
  const ffmpeg = RenderInternals.getExecutablePath({type:'ffmpeg',indent:false,logLevel:'error',binariesDirectory:null});
  const output = path.join(path.dirname(file), 'normalized.mp4');
  const measure = spawnSync(ffmpeg, ['-hide_banner','-nostats','-i',file,'-vn','-af','loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json','-f','null','-'], {encoding:'utf8',maxBuffer:4*1024*1024});
  const measuredMatch = measure.stderr?.match(/\{\s*"input_i"[\s\S]*?\}/);
  if (measure.status !== 0 || !measuredMatch) throw new Error('Не удалось измерить громкость озвучки');
  const input = JSON.parse(measuredMatch[0]);
  if (!Number.isFinite(Number(input.input_i))) throw new Error('В озвучке не обнаружен слышимый звук');
  const filter = `loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=${input.input_i}:measured_LRA=${input.input_lra}:measured_TP=${input.input_tp}:measured_thresh=${input.input_thresh}:offset=${input.target_offset}:linear=true:print_format=json`;
  const result = spawnSync(ffmpeg, ['-hide_banner','-nostats','-y','-i',file,'-c:v','copy','-af',filter,'-ar','48000','-c:a','aac','-b:a','160k','-movflags','+faststart',output], {encoding:'utf8',maxBuffer:4*1024*1024});
  if (result.status !== 0) throw new Error('Не удалось выровнять громкость: ' + result.stderr);
  const match = result.stderr.match(/\{\s*"input_i"[\s\S]*?\}/);
  if (!match) throw new Error('FFmpeg не вернул результаты измерения громкости');
  const stats = JSON.parse(match[0]);
  if (!Number.isFinite(Number(stats.input_i)) || !Number.isFinite(Number(stats.output_i))) throw new Error('В озвучке не обнаружен слышимый звук');
  await fs.copyFile(output, file);
  await fs.unlink(output);
  return {method:'ffmpeg-loudnorm',targetLufs:-16,truePeakLimitDb:-1.5,inputLufs:Number(stats.input_i),outputLufs:Number(stats.output_i),outputTruePeakDb:Number(stats.output_tp)};
}
