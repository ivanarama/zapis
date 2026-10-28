import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {spawnSync} from 'node:child_process';
import {RenderInternals} from '@remotion/renderer';
import {normalizeNarration} from '../scripts/audio.mjs';

test('a silent AAC track is rejected instead of being marked as audible narration',async t=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'video-workshop-audio-'));
  t.after(async()=>{assert.equal(path.dirname(root),path.resolve(os.tmpdir()));assert(path.basename(root).startsWith('video-workshop-audio-'));await fs.rm(root,{recursive:true,force:true});});
  const ffmpeg=RenderInternals.getExecutablePath({type:'ffmpeg',indent:false,logLevel:'error',binariesDirectory:null});
  const file=path.join(root,'silent.mp4');
  const result=spawnSync(ffmpeg,['-hide_banner','-loglevel','error','-y','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','1','-c:a','aac',file],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  await assert.rejects(normalizeNarration(file),/не обнаружен слышимый звук/);
});
