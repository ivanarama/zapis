import React from 'react';
import {AbsoluteFill, Audio, Composition, Img, Sequence, interpolate, spring, registerRoot, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import sample from '../campaigns/edo.json';

const frameCount = (scene, fps) => scene.frames ?? Math.ceil(scene.seconds * fps);
const reveal = (frame, fps, delay = 0) => frame < delay * fps ? 0 : spring({frame: frame - delay * fps, fps, config: {damping: 18, stiffness: 135, mass: .85}});
function KineticTitle({text, frame, fps}) {
  return text.split('\n').map((line, row) => <React.Fragment key={row}>{row > 0 && <br />}{line.split(' ').map((word, column) => {
    const progress = reveal(frame, fps, .12 + row * .15 + column * .045);
    return <React.Fragment key={column}>{column > 0 && ' '}<span style={{display: 'inline-block', opacity: Math.min(progress, 1), transform: `translateY(${(1 - progress) * 22}px)`}}>{word}</span></React.Fragment>;
  })}</React.Fragment>);
}
function Flow({scene, frame, fps, accent, portrait}) {
  return <div style={{display:'flex',flexDirection:portrait?'column':'row',alignItems:'center',marginTop:portrait?42:40,gap:portrait?12:8}}>{scene.nodes.map((node,index)=>{
    const progress=reveal(frame,fps,scene.revealAt?.[index]??(.5+index*.65));
    const line=interpolate(frame/fps,[(scene.revealAt?.[index]??(.5+index*.65))+.1,(scene.revealAt?.[index]??(.5+index*.65))+.6],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'});
    return <React.Fragment key={node}><div style={{flex:portrait?'none':1,width:portrait?'100%':undefined,minHeight:portrait?108:158,padding:'22px 16px',border:'2px solid '+accent,borderRadius:22,background:'#fff',opacity:Math.min(progress,1),transform:`translate${portrait?'Y':'X'}(${(1-progress)*20}px) scale(${.92+.08*progress})`,textAlign:'center'}}><div style={{fontSize:18,fontWeight:750,color:accent,marginBottom:12}}>0{index+1}</div><div style={{fontSize:portrait?27:23,fontWeight:700,lineHeight:1.3}}>{node}</div></div>{index<scene.nodes.length-1&&<svg width={portrait?34:44} height={portrait?34:40} viewBox="0 0 44 40" style={{flex:'none',transform:portrait?'rotate(90deg)':undefined,opacity:Math.min(progress,1)}}><path d="M2 20 H38" stroke={accent} strokeWidth="3" fill="none" strokeDasharray="36" strokeDashoffset={36*(1-line)}/><path d="M30 12 L39 20 L30 28" stroke={accent} strokeWidth="3" fill="none" opacity={line}/><circle cx={3+32*line} cy="20" r="4" fill={accent}/></svg>}</React.Fragment>;
  })}</div>;
}

function Scene({scene, campaign, index, total, portrait}) {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const frames = frameCount(scene, fps);
  const fade = Math.min(8, Math.floor(frames / 5));
  const opacity = interpolate(frame, [0, fade, frames - fade, frames], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const accent = campaign.brand.accent;
  const pad = portrait ? 42 : 58;
  return <AbsoluteFill style={{background: '#f5f6fc', color: '#1e2c43', overflow: 'hidden', fontFamily: 'Workshop, sans-serif'}}>
    {campaign.canvas === 'paper' && <AbsoluteFill style={{backgroundImage:'linear-gradient(#52657d09 1px,transparent 1px),linear-gradient(90deg,#52657d09 1px,transparent 1px)',backgroundSize:'32px 32px'}}/>}
    <div style={{position: 'absolute', width: 680, height: 680, right: -280 + Math.sin(frame / fps * .6) * 20, bottom: -300 + Math.cos(frame / fps * .6) * 18, borderRadius: '50%', background: accent, opacity: .065}} />
    <div style={{position: 'absolute', left: pad, right: pad, top: 30, display: 'flex', justifyContent: 'space-between', fontSize: portrait ? 20 : 18, color: '#68768b'}}><b style={{color: accent}}>{campaign.brand.name}</b><span>{String(index + 1).padStart(2, '0')} / {String(total).padStart(2, '0')}</span></div>
    <div style={{position: 'absolute', left: pad, right: pad, top: portrait ? 122 : 96, bottom: 76, opacity, display: 'flex', flexDirection: 'column'}}>
      <div style={{fontSize: portrait ? 19 : 17, fontWeight: 700, letterSpacing: 1.5, color: accent, marginBottom: 17}}>{scene.kicker}</div>
      <h1 style={{fontSize: portrait ? 48 : 49, fontWeight: 760, lineHeight: 1.15, letterSpacing: -1.5, margin: '0 0 22px', overflowWrap: 'anywhere'}}><KineticTitle text={scene.title} frame={frame} fps={fps} /></h1>
      <p style={{fontSize: portrait ? 25 : 24, lineHeight: 1.5, color: '#59677d', margin: 0}}>{scene.body}</p>
      {scene.metric && <div style={{marginTop:22,display:'flex',gap:26,alignItems:'center',opacity:Math.min(reveal(frame,fps,.3),1)}}><b style={{fontSize:portrait?115:130,color:accent,lineHeight:1}}>{Math.round(scene.metric.value*interpolate(frame/fps,[.3,1.6],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'}))}</b><span style={{fontSize:27,maxWidth:520}}>{scene.metric.label}</span></div>}
      {scene.type === 'screenshot' && <figure style={{margin: portrait ? '46px 0 0' : '28px 0 0', padding: portrait ? 16 : 20, border: '1px solid #d6ddef', borderRadius: 18, background: '#fff',opacity:Math.min(reveal(frame,fps,.4),1),transform:`translateY(${(1-reveal(frame,fps,.4))*24}px) scale(${1+interpolate(frame,[0,frames],[0,.018])})`}}>
        <Img src={staticFile(scene.image)} style={{display: 'block', maxWidth: '100%', width: 'auto', height: 'auto', maxHeight: portrait ? 550 : 260, objectFit: 'contain', margin: '0 auto'}} />
        <figcaption style={{fontSize: portrait ? 18 : 15, lineHeight: 1.5, marginTop: 16, color: '#6b7890'}}>{scene.imageCaption}</figcaption>
      </figure>}
      {scene.type === 'compare' && <div style={{display: 'flex', flexDirection: portrait ? 'column' : 'row', gap: 22, marginTop: 34}}>{scene.cards.map((card,i) => <div key={card.label} style={{flex: 1, padding: portrait ? 27 : 28, border: '1px solid #d6ddef', borderRadius: 18, background: '#fff',opacity:Math.min(reveal(frame,fps,scene.revealAt?.[i]??(.5+i*.5)),1),transform:`translateY(${(1-reveal(frame,fps,scene.revealAt?.[i]??(.5+i*.5)))*25}px)`}}><div style={{fontSize: 17, color: '#788499', marginBottom: 18}}>{card.label}</div><div style={{fontSize: portrait ? 33 : 37, fontWeight: 750, color: accent, overflowWrap: 'anywhere'}}>{card.value}</div></div>)}</div>}
      {scene.type === 'compare' && <p style={{fontSize: 19, color: '#6b7890', marginTop: 24}}>{scene.disclosure}</p>}
      {scene.type === 'steps' && <div style={{display: 'grid', gap: 16, marginTop: 34}}>{scene.steps.map((step, i) => <div key={step} style={{padding: '18px 24px', background: '#fff', border: '1px solid #d6ddef', borderRadius: 14, fontSize: 24,opacity:Math.min(reveal(frame,fps,scene.revealAt?.[i]??(.5+i*.4)),1),transform:`translateX(${(1-reveal(frame,fps,scene.revealAt?.[i]??(.5+i*.4)))*30}px)`}}><b style={{color: accent, marginRight: 18}}>0{i + 1}</b>{step}</div>)}</div>}
      {scene.type === 'flow' && <Flow scene={scene} frame={frame} fps={fps} accent={accent} portrait={portrait}/>}
      {scene.type === 'cta' && <div style={{display: 'flex', flexDirection: portrait ? 'column' : 'row', alignItems: portrait ? 'flex-start' : 'center', gap: 25, marginTop: 44}}><div style={{padding: '23px 32px', background: accent, borderRadius: 16, fontSize: 28, color: '#fff', fontWeight: 750}}>{scene.button}</div><div style={{fontSize: 27, fontWeight: 700, color: accent, overflowWrap: 'anywhere'}}>{scene.contact}</div></div>}
    </div>
    <div style={{position: 'absolute', left: pad, right: pad, bottom: 24, fontSize: portrait ? 17 : 15, color: '#7a869a'}}>{campaign.footer}</div>
    <div style={{position:'absolute',bottom:0,left:0,height:4,width:`${100*frame/frames}%`,background:accent,opacity:.6}}/>
    {scene.audio && <Audio src={staticFile(scene.audio)} volume={10 ** ((campaign.voice?.gainDb ?? 3) / 20)} />}
  </AbsoluteFill>;
}

function Video({campaign, format}) {
  const {fps} = useVideoConfig();
  let offset = 0;
  return <>
    <style>{`@font-face{font-family:Workshop;src:url('${staticFile('assets/fonts/NotoSans.ttf')}') format('truetype');font-weight:100 900;font-style:normal}`}</style>
    {campaign.scenes.map((scene, index) => {const from = offset; const frames = frameCount(scene, fps); offset += frames; return <Sequence key={scene.id} from={from} durationInFrames={frames}><Scene scene={scene} campaign={campaign} index={index} total={campaign.scenes.length} portrait={format === 'portrait'} /></Sequence>;})}
  </>;
}

function Root() {
  return <>{[['landscape', 1280, 720], ['portrait', 720, 1280]].map(([format, width, height]) => <Composition key={format} id={'Video-' + format} component={Video} width={width} height={height} fps={24} durationInFrames={sample.scenes.reduce((n, s) => n + frameCount(s, 24), 0)} defaultProps={{campaign: sample, format}} calculateMetadata={({props}) => {
    const fps = props.campaign.fps ?? 24;
    return {fps, durationInFrames: props.campaign.scenes.reduce((n, s) => n + frameCount(s, fps), 0)};
  }} />)}</>;
}

registerRoot(Root);
