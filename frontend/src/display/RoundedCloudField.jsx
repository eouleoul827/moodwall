import React, { useMemo, useRef, useId, useEffect, useState } from 'react';
import { layoutClouds } from './cloud-layout.mjs';
import { advanceCloudMotion, reconcileCloudMotion } from './cloud-motion.mjs';
import { createDeparture, departureReason } from './cloud-departure.mjs';
import './rounded-clouds.css';

const TINTS = {
  celebration: '#fce9d7', joy: '#f9e0e9', support: '#e0eafd', excitement: '#eee3fa',
  tired: '#e8eaf1', sadness: '#e0eff4', anger: '#f9e5df', neutral: '#f7f0df',
};

function Cloud({ cloud, departing = false }) {
  const { item, shape, lines } = cloud;
  const paintId = useId();
  return <article className={departing ? 'rw-departing-cloud' : 'rw-reading-cloud'} data-cloud-id={item.id} data-cloud-shape={shape.id}
    style={{ '--rw-cloud-width': cloud.width / 20.46, '--rw-cloud-height': cloud.height / 20.46,
      '--rw-cloud-font': cloud.font / 20.46, '--rw-cloud-tint': TINTS[item.sceneId] || TINTS.neutral,
      '--rw-bob-duration': `${17 + cloud.seed % 17}s`, '--rw-bob-delay': `${-cloud.seed % 31}s`,
      '--rw-wander': `${(4 + cloud.seed % 6) / 20.46}`, '--rw-cloud-flip': cloud.flipped ? -1 : 1 }}>
    <svg className="rw-cloud-silhouette" viewBox="0 0 600 260" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id={paintId} x1="0" y1="0" x2=".25" y2="1"><stop offset="0" stopColor="#fffdf7"/><stop offset="1" stopColor="var(--rw-cloud-tint)"/></linearGradient></defs>
      <path d={shape.path} fill={`url(#${paintId})`} stroke="#8298b2" strokeWidth="2.4" vectorEffect="non-scaling-stroke" strokeLinejoin="round"/>
    </svg>
    {!departing && <p className="rw-cloud-text" aria-label={String(item.content ?? '')}>
      {lines.map((line,index)=><span key={index} aria-hidden="true">{line}</span>)}
    </p>}
  </article>;
}

export default function RoundedCloudField({ view, period = 'night', paused = false, serverNow = Date.now() }) {
  const motion = useRef(new Map());
  const tracks = useRef(new Map());
  const departing = useRef([]);
  const [departureVersion, setDepartureVersion] = useState(0);
  const pool = view?.visible ? (view.bubbles || []).slice(-30) : [];
  const signature = JSON.stringify([Boolean(view?.visible), pool.map(item => [item.id, item.content, item.sceneId, item.contentDeleteAt, item.completedSequence])]);
  const clouds = useMemo(() => {
    const time=performance.now();
    const ids=new Set(pool.map(item=>item.id));
    const arriving=pool.some(item=>!motion.current.has(item.id));
    if (!view?.visible) departing.current=[];
    else {
      departing.current=departing.current.filter(cloud=>!ids.has(cloud.id) && time<cloud.started+cloud.duration);
      for (const old of motion.current.values()) if (!ids.has(old.id)) {
        const reason=departureReason(old,pool,arriving,serverNow);
        if (reason) departing.current.push(createDeparture(old,reason,time));
      }
      departing.current=departing.current.slice(-30);
    }
    const layout = layoutClouds(pool, motion.current);
    motion.current = reconcileCloudMotion(layout, motion.current);
    return layout;
  }, [signature]);
  useEffect(() => {
    if (!departing.current.length) return;
    const until=Math.min(...departing.current.map(cloud=>cloud.started+cloud.duration));
    const timer=setTimeout(()=>{
      departing.current=departing.current.filter(cloud=>performance.now()<cloud.started+cloud.duration);
      setDepartureVersion(value=>value+1);
    },Math.max(1,until-performance.now()));
    return ()=>clearTimeout(timer);
  },[clouds,departureVersion]);
  useEffect(() => {
    const paint = () => {
      for (const cloud of motion.current.values()) {
        const track=tracks.current.get(cloud.id);
        if (!track) continue;
        track.style.setProperty('--rw-x', String(cloud.x / 20.46));
        track.style.setProperty('--rw-y', String(cloud.y / 20.46));
      }
    };
    paint();
    if (paused || !clouds.length) return;
    const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (reduced && !document.documentElement.classList.contains('mw-signage')) return;
    let frame, last=null;
    const tick = now => {
      // Background tabs resume from the current location without a catch-up jump.
      if (!document.hidden && last!==null) {
        advanceCloudMotion(motion.current,Math.min(.05,Math.max(0,(now-last)/1000)));
        paint();
      }
      last=document.hidden?null:now;
      frame=window.requestAnimationFrame(tick);
    };
    frame=window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [clouds, paused]);
  if (!view?.visible) return null;
  return <section className={`rw-cloud-field rw-cloud-field-${period}${paused ? ' is-paused' : ''}`} aria-label="이 화면에 남은 메시지"
    data-pool-count={clouds.length} data-message-count={clouds.length}>
    {clouds.map(cloud=><div key={`${cloud.id}:${cloud.content}`} className="rw-cloud-track" data-cloud-track={cloud.id} data-cloud-speed={cloud.speed}
      ref={node=>{ if(node) tracks.current.set(cloud.id,node); else tracks.current.delete(cloud.id); }}
      style={{ '--rw-x':cloud.x / 20.46, '--rw-y':cloud.y / 20.46,
        zIndex:motion.current.get(cloud.id)?.layer ?? Math.round(cloud.y) }}>
      <Cloud cloud={cloud}/>
    </div>)}
    {departing.current.map(cloud=><div key={`depart-${cloud.id}`} className={`rw-cloud-departure is-${cloud.reason}`}
      data-departure={cloud.reason} aria-hidden="true"
      style={{ '--rw-x':cloud.x / 20.46, '--rw-y':cloud.y / 20.46,
        '--rw-exit-duration':`${cloud.duration}ms`, zIndex:cloud.layer }}>
      <Cloud cloud={cloud} departing/>
    </div>)}
    {clouds.length===0&&<div className="rw-empty-sky"><p className="rw-invitation-title">첫 번째 구름을 띄워볼까요?</p><p className="rw-invitation-copy">작은 한마디로 우리의 하늘을 채워 줘.</p></div>}
    {clouds.length===1&&<div className="rw-sparse-invitation">작은 한마디로 우리의 하늘을 채워 줘.</div>}
  </section>;
}
