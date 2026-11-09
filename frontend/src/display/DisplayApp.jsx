import { useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import QRCode from 'qrcode';
import { createMoodwallDisplayRuntime } from '@clients/display/playback-browser.mjs';
import { DISPLAY_PALETTES, particleStyles, periodFor, seedFor, themeFor, THEMES } from './themes.js';
import './display.css';
import './display-scene.css';
import DisplayBackdrop from './DisplayBackdrop.jsx';
import CloudMessage from './CloudMessage.jsx';
import LedQr from './LedQr.jsx';
import { AUTH_ERRORS, displayRequest, useDisplaySession } from './display-session.js';
import { createDisplayDiagnostics } from './display-diagnostics.js';

const EMPTY_BUBBLES = { visible: false, bubbles: [], totalCount: 0 };
// Every received cloud stays counted in the heading and forecast, but only the
// newest ones drift. A 2046 × 768 panel becomes an unreadable, GPU-heavy mass
// long before the 2000-card cache limit (each cloud is a masked, filtered layer).
export const MAX_DRIFTING_CLOUDS = 30;
const RECHECK_AFTER_403_MS = 30_000;
function WeatherIcon({ className = '' }) {
  return <svg className={className} viewBox="0 0 100 100" fill="none" aria-hidden="true">
    <circle cx="40" cy="36" r="18" fill="#f6d49b" />
    <path d="M32 75h41a17 17 0 0 0 1-34 25 25 0 0 0-46-1 18 18 0 0 0 4 35Z" fill="#fff" stroke="currentColor" strokeWidth="2" />
    <path d="M40 21v-7M19 36h-7M24 20l-5-5M59 19l5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>;
}

function EmotionMark({ sceneId = 'neutral', className = '' }) {
  const isHeart = ['joy', 'excitement'].includes(sceneId);
  return <svg className={className} viewBox="0 0 100 100" aria-hidden="true">
    {isHeart ? <path d="M50 83 17 53C-4 31 27 9 50 32 73 9 104 31 83 53Z" fill="currentColor" /> :
      sceneId === 'sadness' ? <><path d="M20 55h58a15 15 0 0 0-2-30 25 25 0 0 0-46 0 15 15 0 0 0-10 30Z" fill="currentColor" /><path d="m30 68-5 12m25-12-5 12m25-12-5 12" stroke="currentColor" strokeWidth="5" strokeLinecap="round" /></> :
      sceneId === 'tired' ? <path d="M24 70h51a17 17 0 0 0-2-34 24 24 0 0 0-45-3 19 19 0 0 0-4 37Z" fill="currentColor" /> :
      sceneId === 'anger' ? <path d="M51 8C74 32 33 34 65 55c-1-15 8-20 8-20 27 36 9 58-23 58-37 0-46-34-22-58 0 21 8 22 10 23C27 30 52 24 51 8Z" fill="currentColor" /> :
      <path d="m50 5 11 30 32 15-32 13-11 32-12-32L6 50l32-15Z" fill="currentColor" />}
  </svg>;
}

function SceneParticles({ item }) {
  const seed = seedFor(item.sceneSeed);
  const theme = themeFor(item);
  return <div className={`mw-particles mw-effect-${theme.effect}`} aria-hidden="true">
    {Array.from({ length: 30 }, (_, index) => <span key={index} style={particleStyles(seed, index)}>
      {['hearts', 'petals', 'sparkles'].includes(theme.effect) && <EmotionMark sceneId={item.sceneId} />}
    </span>)}
  </div>;
}

const BubbleField = function BubbleField({ view, period }) {
  return <div className="mw-bubble-field" aria-label="이 화면에 남은 메시지">
    {view.visible && view.bubbles.slice(-MAX_DRIFTING_CLOUDS).map((bubble, shownIndex, shown) => {
      // Position from the index in the full ordered list so older clouds never jump rows.
      const index = view.bubbles.length - shown.length + shownIndex;
      const seed = seedFor(bubble.id);
      return <div key={bubble.id} className={`mw-bubble-track ${bubble.phase === 'AMBIENT' ? 'is-ambient' : ''}`}
        style={{ '--row': `${6 + ((index * 113 + seed % 47) % 72)}%`, '--rest-x': `${23 + seed % 46}%`,
          '--drift-duration': `${80 + seed % 60}s`, '--drift-delay': `${-(seed % 140)}s` }}>
        <CloudMessage item={bubble} period={period} />
      </div>;
    })}
  </div>;
};

function MoodForecast({ bubbles }) {
  const counts = useMemo(() => {
    const values = Object.keys(THEMES).map(id => ({ id, ...THEMES[id], count: 0 }));
    for (const bubble of bubbles) {
      const value = values.find(entry => entry.id === bubble.sceneId) || values.at(-1);
      value.count++;
    }
    return values;
  }, [bubbles]);
  const total = bubbles.length;
  const top = counts.reduce((a, b) => a.count >= b.count ? a : b);
  return <aside className="mw-forecast" aria-label="이 화면의 기분 분포">
    <div className="mw-eyebrow">지금의 무드 예보</div>
    <div className="mw-forecast-heading"><WeatherIcon className="mw-weather-icon" />
      <h2>{total ? top.forecast : '어떤 기분이든\n들려주세요'}</h2></div>
    <div className="mw-mood-bars">{counts.filter(entry => entry.id !== 'neutral' || entry.count).map(entry =>
      <div className="mw-mood-row" key={entry.id}>
        <span>{entry.label}</span><div className="mw-mood-track"><i style={{ width: `${total ? entry.count / total * 100 : 0}%`, background: entry.color }} /></div>
        <small>{total ? Math.round(entry.count / total * 100) : 0}%</small>
      </div>)}</div>
    <p className="mw-local-note">이 화면에 남은 기분 기준</p>
  </aside>;
}

function Sky({ period }) {
  return <div className={`mw-sky mw-sky-${period}`} aria-hidden="true">
    <div className="mw-day-glow" />
    <div className="mw-night-stars">{Array.from({ length: 36 }, (_, index) =>
      <i key={index} style={{ left: `${(index * 137 + 17) % 100}%`, top: `${(index * 29 + 7) % 72}%`,
        '--star-delay': `${-(index % 7)}s`, '--star-duration': `${3 + index % 4}s` }} />)}</div>
  </div>;
}

export default function DisplayApp({ runtimeFactory = createMoodwallDisplayRuntime, fetcher = fetch }) {
  const { session, csrfRef, invalidate, check } = useDisplaySession(fetcher);
  const [bubbles, setBubbles] = useState(EMPTY_BUBBLES), [spotlight, setSpotlight] = useState(null);
  const [qr, setQr] = useState(null), [mode, setMode] = useState('MOODWALL'), [now, setNow] = useState(Date.now());
  const [authority, setAuthority] = useState({ connected: false, until: 0 });
  const [fatal, setFatal] = useState(false);
  const sceneElement = useRef(null), runtimeRef = useRef(null), diagnosticsRef = useRef(null), spotlightRef = useRef(null);
  // The visible clock and day/night colours follow the server, not a library laptop's clock.
  const clockOffsetRef = useRef(0), lastRecheckRef = useRef(-Infinity);
  const syncClock = serverNow => {
    const parsed = Date.parse(serverNow);
    if (Number.isFinite(parsed)) clockOffsetRef.current = parsed - Date.now();
  };

  useEffect(() => {
    const clock = setInterval(() => setNow(Date.now() + clockOffsetRef.current), 1000);
    return () => clearInterval(clock);
  }, []);

  // A signage panel must not follow the laptop's "reduce animations" setting, which
  // managed school PCs often switch off for speed; viewers cannot change it anyway.
  useEffect(() => {
    document.documentElement.classList.add('mw-signage');
    return () => document.documentElement.classList.remove('mw-signage');
  }, []);

  // Keep the laptop from blanking the panel while the wall is visible.
  useEffect(() => {
    if (!session || typeof navigator === 'undefined' || !navigator.wakeLock?.request) return undefined;
    let active = true, lock = null;
    const acquire = async () => {
      if (!active || document.visibilityState !== 'visible' || (lock && !lock.released)) return;
      try { lock = await navigator.wakeLock.request('screen'); } catch { /* power policy may refuse */ }
      if (!active) void lock?.release().catch(() => {});
    };
    void acquire();
    document.addEventListener('visibilitychange', acquire);
    return () => {
      active = false; document.removeEventListener('visibilitychange', acquire);
      void lock?.release().catch(() => {});
    };
  }, [session]);

  useEffect(() => {
    if (!session) return;
    let active = true, bubbleSignature = '', qrTimer = null, qrBusy = false, qrAgain = false, invalidated = false;
    let qrNotBefore = 0, latestControl = null, currentMode = session.mode;
    const controller = new AbortController();
    const diagnostics = createDisplayDiagnostics({ fetcher, csrfToken: () => csrfRef.current });
    diagnosticsRef.current = diagnostics;
    setMode(session.mode); setBubbles(EMPTY_BUBBLES); setQr(null); setFatal(false);
    setAuthority({ connected: false, until: 0 });
    const revoke = () => {
      if (!active || invalidated) return;
      invalidated = true;
      runtimeRef.current?.resetAuthentication();
      controller.abort(); clearTimeout(qrTimer);
      setQr(null); setBubbles(EMPTY_BUBBLES); setSpotlight(null);
      setAuthority({ connected: false, until: 0 }); invalidate();
    };
    // 403 is never a revocation: refresh the session (a new CSRF token) at a bounded rate.
    const recheck = () => {
      if (performance.now() - lastRecheckRef.current < RECHECK_AFTER_403_MS) return;
      lastRecheckRef.current = performance.now(); void check();
    };
    const observedFetch = async (path, options) => {
      const response = await fetcher(path, options);
      if (!active || invalidated) return response;
      if (AUTH_ERRORS.has(response.status)) revoke();
      else if (response.status === 403) recheck();
      return response;
    };
    const renderer = {
      async prepare(item, { signal }) {
        if (signal.aborted) throw signal.reason;
        if (item.sceneVersion !== '1' || !Object.hasOwn(THEMES, item.sceneId)) {
          diagnostics.record('RENDER', 'ASSET_FAILURE');
          throw Object.assign(new Error('UNSUPPORTED_SCENE'), { code: 'ASSET_FAILURE' });
        }
        try {
          await document.fonts?.load('400 96px "Jua"', item.content);
          if (signal.aborted) throw signal.reason;
          await document.fonts?.load('700 96px "Noto Sans KR"', item.content);
          if (signal.aborted) throw signal.reason;
          await document.fonts?.ready;
          if (signal.aborted) throw signal.reason;
          diagnostics.record('RENDER', 'ASSET_FAILURE', 'RECOVERED');
        } catch (error) { if (!signal.aborted) diagnostics.record('RENDER', 'ASSET_FAILURE'); throw error; }
      },
      show(item, display) {
        if (!active || invalidated || document.visibilityState !== 'visible') throw new Error('PAGE_HIDDEN');
        if (currentMode !== 'MOODWALL') throw new Error('PAGE_HIDDEN');
        spotlightRef.current = item;
        // The controller passes the server-chosen full-screen time for the progress bar.
        const durationMs = Number.isInteger(display?.durationMs) ? display.durationMs : 15_000;
        flushSync(() => setSpotlight({ ...item, displayDurationMs: durationMs }));
        if (sceneElement.current) sceneElement.current.hidden = false;
      },
      hide() {
        spotlightRef.current = null;
        if (sceneElement.current) sceneElement.current.hidden = true;
        if (active) setSpotlight(null);
      },
    };
    const runtime = runtimeFactory({ boardId: session.boardId, renderer, csrfToken: () => csrfRef.current,
      fetch: observedFetch,
      renderBubbles(view) {
        if (!active || invalidated) return;
        const signature = `${view.visible}:${view.bubbles.map(bubble => `${bubble.id}:${bubble.phase}`).join(',')}`;
        if (signature !== bubbleSignature) { bubbleSignature = signature; setBubbles(view); }
      },
      onState(state) {
        if (!active || invalidated) return;
        if (state.storageAvailable === false) diagnostics.record('CACHE', 'STORAGE_UNAVAILABLE');
        else if (state.storageAvailable === true) diagnostics.record('CACHE', 'STORAGE_UNAVAILABLE', 'RECOVERED');
        if (state.fatal) { renderer.hide(); setFatal(true); setAuthority({ connected: false, until: 0 }); return; }
        // Playback and snapshots travel independently. A late snapshot must not
        // pause a newer presentation, nor may a late playback reopen a pause.
        if (/^\d+$/.test(state.deviceGeneration) && /^\d+$/.test(state.revision)) {
          const next = { generation: BigInt(state.deviceGeneration), revision: BigInt(state.revision), at: Date.parse(state.serverNow) };
          if (latestControl && (next.generation < latestControl.generation ||
              next.generation === latestControl.generation && (next.revision < latestControl.revision ||
              next.revision === latestControl.revision && next.at < latestControl.at))) return;
          latestControl = next;
        }
        if (state.serverNow) syncClock(state.serverNow);
        if (state.mode) { currentMode = state.mode; setMode(state.mode); }
        if (state.mode && state.mode !== 'MOODWALL' || Array.isArray(state.removedIds) && state.removedIds.includes(spotlightRef.current?.id)) {
          renderer.hide(); runtimeRef.current?.player?.pause('HIDDEN');
        }
        if (state.connected === false) { setAuthority({ connected: false, until: 0 }); return; }
        if (state.connected === true) diagnostics.record(state.source === 'snapshot' ? 'CACHE' : 'PLAYBACK', 'CONNECTION_FAILED', 'RECOVERED');
        if (state.source === 'playback' && state.attempt?.status === 'PLAYING') {
          diagnostics.record('PLAYBACK', 'PLAYBACK_OWNED', 'RECOVERED');
          diagnostics.record('PLAYBACK', 'PLAYBACK_INTERRUPTED', 'RECOVERED');
        }
        if (state.source === 'playback' && state.attempt?.status === 'DONE') diagnostics.record('PLAYBACK', 'PLAYBACK_UNKNOWN', 'RECOVERED');
        // Runtime uses the RTT-adjusted server clock; raw serverNow would extend a slow response's lease.
        if (Number.isFinite(state.monotonicLeaseUntil)) setAuthority({ connected: true, until: state.monotonicLeaseUntil });
      },
      onProblem(error) {
        if (!active || invalidated) return;
        if (AUTH_ERRORS.has(error?.status)) revoke();
        else {
          if (error?.status === 403) recheck();
          const code = error?.code === 'PLAYBACK_OWNED' ? 'PLAYBACK_OWNED'
            : ['STORAGE_UNAVAILABLE', 'CACHE_STORAGE_UNAVAILABLE', 'COMPLETED_CACHE_UNVERIFIED'].includes(error?.code) ? 'STORAGE_UNAVAILABLE'
            : error?.fatal ? 'RENDER_FAILURE'
            : error?.outcome === 'UNKNOWN' ? 'PLAYBACK_UNKNOWN'
            : error?.outcome === 'INTERRUPTED' ? 'PLAYBACK_INTERRUPTED' : 'CONNECTION_FAILED';
          diagnostics.record(code === 'STORAGE_UNAVAILABLE' || error?.source === 'snapshot' ? 'CACHE' : error?.fatal ? 'RENDER' : 'PLAYBACK', code);
        }
      },
    });
    runtimeRef.current = runtime;
    const refreshQr = async () => {
      if (!active || invalidated) return;
      if (qrBusy) { qrAgain = true; return; }
      if (performance.now() < qrNotBefore) {
        clearTimeout(qrTimer); qrTimer = setTimeout(refreshQr, qrNotBefore - performance.now()); return;
      }
      qrBusy = true; clearTimeout(qrTimer);
      const started = performance.now();
      try {
        const ticket = await displayRequest('/api/display/qr', { signal: controller.signal, fetcher: observedFetch });
        const url = new URL(ticket.joinUrl);
        if (!['https:', 'http:'].includes(url.protocol)) throw new Error('INVALID_QR');
        // One pixel per module; LedQr enlarges it by whole physical pixels.
        const image = await QRCode.toDataURL(ticket.joinUrl, { errorCorrectionLevel: 'M', margin: 2, scale: 1, color: { dark: '#32372fff', light: '#ffffffff' } });
        if (!active || invalidated) return;
        const validFor = Date.parse(ticket.validUntil) - Date.parse(ticket.serverNow);
        const rotatesIn = Date.parse(ticket.rotatesAt) - Date.parse(ticket.serverNow);
        if (!Number.isFinite(validFor) || validFor <= 0 || !Number.isFinite(rotatesIn) || performance.now() >= started + validFor) throw new Error('INVALID_QR');
        syncClock(ticket.serverNow);
        setQr({ image, expires: started + validFor });
        diagnostics.record('QR', 'QR_UNAVAILABLE', 'RECOVERED');
        qrNotBefore = 0;
        qrTimer = setTimeout(refreshQr, Math.max(1000, Math.min(30_000, rotatesIn)));
      } catch (error) {
        if (active && !invalidated && error.name !== 'AbortError') {
          diagnostics.record('QR', 'QR_UNAVAILABLE');
          const delay = error.status === 429 ? Math.max(5000, error.retryAfterMs || 5000) : 5000;
          qrNotBefore = performance.now() + delay;
          qrTimer = setTimeout(refreshQr, delay);
        }
      } finally { qrBusy = false; if (active && !invalidated && qrAgain) { qrAgain = false; void refreshQr(); } }
    };
    let events;
    try {
      if (typeof EventSource !== 'undefined') {
        events = new EventSource('/api/display/events');
        events.addEventListener('state.changed', () => void runtime.refresh());
        events.addEventListener('qr.changed', () => void refreshQr());
      }
    } catch { diagnostics.record('PLAYBACK', 'CONNECTION_FAILED'); }
    runtime.start(); void refreshQr();
    return () => {
      active = false; runtime.stop(); runtimeRef.current = null;
      controller.abort(); clearTimeout(qrTimer); events?.close(); diagnostics.stop(); diagnosticsRef.current = null;
    };
  }, [session, runtimeFactory, fetcher, invalidate, check, csrfRef]);

  const validQr = Boolean(qr && mode === 'MOODWALL' && authority.connected && performance.now() < authority.until && performance.now() < qr.expires);
  const wallAvailable = Boolean(session && mode === 'MOODWALL' && bubbles.visible && !fatal);
  useEffect(() => {
    if (wallAvailable) diagnosticsRef.current?.record('RENDER', 'RENDER_FAILURE', 'RECOVERED');
  }, [wallAvailable]);
  const date = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'long', day: 'numeric', weekday: 'long' }).format(now);
  const time = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hour12: false }).format(now);
  const period = periodFor(now), palette = DISPLAY_PALETTES[period], theme = themeFor(spotlight, period);
  if (!session || fatal) return <DisplayBackdrop />;

  return <main className={`mw-display mw-display-${period}`} style={{ '--scene-color': theme.color, '--scene-tint': theme.tint,
    '--mw-bg': palette.bg, '--mw-panel': palette.panel, '--mw-ink': palette.ink, '--mw-sub': palette.sub,
    '--mw-line': palette.line, '--mw-track': palette.track, '--mw-sky-top': palette.skyTop,
    '--mw-sky-bottom': palette.skyBottom, '--mw-cloud-ink': palette.cloudInk, '--mw-shadow': palette.shadow }}>
    {!wallAvailable && <DisplayBackdrop />}
    <div className="mw-display-frame">
    <div className="mw-wall-content" hidden={!wallAvailable}>
      <Sky period={period} />
      <BubbleField view={bubbles} period={period} />
      <div className="mw-wall-layout">
        <aside className="mw-join-panel">
          <div className="mw-wordmark">MOOD WALL</div>
          <h1>오늘 기분,<br />한 줄로 남겨요</h1>
          <div className="mw-qr-box">{validQr ? <LedQr image={qr.image} alt="기분을 남기는 참여 QR 코드" /> : <WeatherIcon />}</div>
          {validQr && <p>폰 카메라로 찍어주세요<br /><strong>30자 · 1시간에 3번</strong></p>}
        </aside>
        <section className="mw-wall-center">
          <header className="mw-wall-heading"><div><p>{date}</p><h2>지금 <em>{bubbles.bubbles.length}</em>개의 구름이<br />머물고 있어요</h2><span className="mw-count-scope">이 화면에 남은 기분 기준</span></div><time>{time}</time></header>
          {!bubbles.bubbles.length && <div className="mw-empty"><WeatherIcon /><p>이 벽의 첫 번째 기분을<br />남겨볼까요?</p></div>}
          {validQr && <div className="mw-wall-caption"><span aria-hidden="true">↗</span> QR을 찍으면 내 한 줄이 화면 가득 떠요</div>}
        </section>
        <MoodForecast bubbles={bubbles.bubbles} />
      </div>
    </div>
    <section ref={sceneElement} className="mw-spotlight" hidden={!spotlight} aria-live="polite" aria-label="새로운 메시지">
      {spotlight && <div className="mw-spotlight-inner" key={spotlight.id}>
        <SceneParticles item={spotlight} /><EmotionMark className="mw-scene-mark" sceneId={spotlight.sceneId} />
        <header><span className="mw-emotion-pill">{theme.label}</span><p>새 구름이 도착했어요</p></header>
        <p className="mw-full-message" style={{ '--message-font': [...spotlight.content].length > 24 ? 4.5 : 5.1667 }}>{spotlight.content}</p>
        <footer><span>익명 · 방금 전</span>{validQr && <div className="mw-spotlight-qr"><p>나도 남기기<br /><small>QR을 찍어주세요</small></p><div className="mw-spotlight-qr-box"><LedQr image={qr.image} alt="기분을 남기는 참여 QR 코드" /></div></div>}</footer>
        <div className="mw-duration-track" data-duration-seconds={spotlight.displayDurationMs / 1000}
          style={{ '--play-duration': `${spotlight.displayDurationMs}ms` }} aria-hidden="true"><i /></div>
      </div>}
    </section>
    </div>
  </main>;
}
