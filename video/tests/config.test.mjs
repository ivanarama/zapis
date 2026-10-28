import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {validateCampaign, loadCampaign, resolveAsset, wavDuration, subtitles, stamp} from '../src/config.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sample = JSON.parse(await fs.readFile(path.join(root,'campaigns/edo.json'),'utf8'));
async function temporary(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(),'video-workshop-test-'));
  t.after(async()=>{
    assert.equal(path.dirname(directory),path.resolve(os.tmpdir()));
    assert(path.basename(directory).startsWith('video-workshop-test-'));
    await fs.rm(directory,{recursive:true,force:true});
  });
  return directory;
}

test('the real example and its original screenshots are present',async()=>{
  const {campaign}=await loadCampaign(root,'edo');assert.equal(campaign.scenes.length,6);
  assert(campaign.scenes.some(s=>s.type==='flow'));assert(campaign.scenes.some(s=>s.type==='compare'));
});
test('duplicate scenes, unknown layouts and missing evidence are rejected',()=>{
  for(const mutate of [c=>c.scenes.push(c.scenes[0]),c=>c.scenes[0].type='fake-ui',c=>c.scenes[0].evidence=[]]){
    const broken=structuredClone(sample);mutate(broken);assert.throws(()=>validateCampaign(broken));
  }
});
test('invalid cue timings and dangerous amplification fail before synthesis',()=>{
  const broken=structuredClone(sample);broken.scenes[1].revealAt=[0,1,20,30];assert.throws(()=>validateCampaign(broken));
  const loud=structuredClone(sample);loud.voice.gainDb=100;assert.throws(()=>validateCampaign(loud));
});
test('assets cannot escape public or use remote URLs',async t=>{
  const base=await temporary(t);await fs.mkdir(path.join(base,'public'));await fs.writeFile(path.join(base,'private.txt'),'private');
  for(const name of ['../private.txt','https://example.test/file.png','C:/private.png','image%2epng','assets\\image.png'])await assert.rejects(resolveAsset(path.join(base,'public'),name));
});
test('WAV duration uses byte rate and supports metadata chunks',()=>{
  const data=Buffer.alloc(44+16000);data.write('RIFF');data.writeUInt32LE(data.length-8,4);data.write('WAVE',8);data.write('fmt ',12);data.writeUInt32LE(16,16);data.writeUInt16LE(1,20);data.writeUInt16LE(1,22);data.writeUInt32LE(8000,24);data.writeUInt32LE(16000,28);data.writeUInt16LE(2,32);data.writeUInt16LE(16,34);data.write('data',36);data.writeUInt32LE(16000,40);
  assert.equal(wavDuration(data),1);assert.throws(()=>wavDuration(Buffer.alloc(32)));
});
test('subtitle cue boundaries use actual frame counts and support hours',()=>{
  const scenes=[{frames:25,narration:'Первый'},{frames:50,narration:'Второй'}];
  assert(subtitles(scenes,25).includes('00:00:01.000 --> 00:00:03.000'));
  assert(subtitles(scenes,25,'srt').includes('2\n00:00:01,000 --> 00:00:03,000'));
  assert.equal(stamp(3661.25),'01:01:01.250');
});
