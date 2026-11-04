import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import QRCode from 'qrcode';
import { createMoodwallDisplayRuntime } from '@clients/display/playback-browser.mjs';
import { particleStyles, seedFor, themeFor, THEMES } from './themes.js';
import './display.css';

const EMPTY_BUBBLES = { visible: false, bubbles: [], totalCount: 0 };
const AUTH_ERRORS = new Set([401, 403]);
const PAIR_MESSAGES = {
  PAIRING_CODE_INVALID: '등록 코드가 맞지 않거나 만료됐어요. 새 코드를 확인해 주세요.',
  PAIRING_INVALID: '등록 코드가 맞지 않거나 만료됐어요. 새 코드를 확인해 주세요.',
  INVALID_PAIRING_CODE: '등록 코드가 맞지 않거나 만료됐어요. 새 코드를 확인해 주세요.',
  RATE_LIMITED: '잠시 기다린 뒤 다시 시도해 주세요.',
};

async function displayRequest(path, { signal, body, csrfToken, fetcher = fetch } = {}) {
  const controller = new AbortController();
  const cancel = () => controller.abort(signal.reason);
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = setTimeout(() => controller.abort(new Error('DISPLAY_REQUEST_TIMEOUT')), 8000);
  try {
    const response = await fetcher(path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      headers: { Accept: 'application/json', ...(body === undefined ? {} : {
        'Content-Type': 'application/json', ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
      }) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const value = await response.json();
    if (!response.ok) throw Object.assign(new Error(value.code || 'DISPLAY_REQUEST_FAILED'), {
      status: response.status, code: value.code,
    });
    return value;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
  }
}

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

const BubbleField = function BubbleField({ view }) {
  return <div className="mw-bubble-field" aria-label="오늘 모인 메시지">
    {view.visible && view.bubbles.map((bubble, index) => {
      const seed = seedFor(bubble.id);
      const theme = themeFor(bubble);
      return <div key={bubble.id} className={`mw-bubble-track ${bubble.phase === 'AMBIENT' ? 'is-ambient' : ''}`}
        style={{ '--row': `${10 + ((index * 137 + seed % 47) % 76)}%`, '--drift-duration': `${65 + seed % 65}s`,
          '--drift-delay': `${-(seed % 105)}s`, '--bob-duration': `${4 + seed % 4}s`, '--bubble-color': theme.color }}>
        <div className="mw-bubble"><i aria-hidden="true" />{bubble.content}</div>
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
  return <aside className="mw-forecast">
    <div className="mw-eyebrow">오늘의 무드 예보</div>
    <WeatherIcon className="mw-weather-icon" />
    <h2>{total ? top.weather : '어떤 기분이든\n들려주세요'}</h2>
    <div className="mw-mood-bars">{counts.filter(entry => entry.id !== 'neutral' || entry.count).map(entry =>
      <div className="mw-mood-row" key={entry.id}>
        <span>{entry.label}</span><div className="mw-mood-track"><i style={{ width: `${total ? entry.count / total * 100 : 0}%`, background: entry.color }} /></div>
        <small>{total ? Math.round(entry.count / total * 100) : 0}%</small>
      </div>)}</div>
    <p className="mw-local-note">이 화면에 남은 기분 기준</p>
  </aside>;
}

export default function DisplayApp({ runtimeFactory = createMoodwallDisplayRuntime, fetcher = fetch }) {
  const [session, setSession] = useState(null);
  const [boot, setBoot] = useState('LOADING');
  const [code, setCode] = useState('');
  const [pairing, setPairing] = useState(false);
  const [problem, setProblem] = useState('');
  const [bubbles, setBubbles] = useState(EMPTY_BUBBLES);
  const [spotlight, setSpotlight] = useState(null);
  const [qr, setQr] = useState(null);
  const [mode, setMode] = useState('MOODWALL');
  const [now, setNow] = useState(Date.now());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [runtimeVersion, setRuntimeVersion] = useState(0);
  const sceneElement = useRef(null), runtimeRef = useRef(null);
  const csrfRef = useRef('');

  const checkSession = useCallback(async signal => {
    setBoot('LOADING'); setProblem('');
    try {
      const value = await displayRequest('/api/display/session', { signal, fetcher });
      if (signal?.aborted) return;
      csrfRef.current = value.csrfToken;
      setSession(value); setMode(value.mode); setBoot('READY');
    } catch (error) {
      if (signal?.aborted) return;
      setBoot(AUTH_ERRORS.has(error.status) ? 'PAIR' : 'ERROR');
      if (!AUTH_ERRORS.has(error.status)) setProblem('서버에 연결하지 못했어요. 연결을 확인하고 다시 시도해 주세요.');
    }
  }, [fetcher]);

  useEffect(() => {
    const controller = new AbortController();
    void checkSession(controller.signal);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => { controller.abort(); clearInterval(clock); };
  }, [checkSession]);

  useEffect(() => {
    if (!session) return;
    let active = true, bubbleSignature = '', qrTimer = null, qrBusy = false, qrAgain = false;
    const controller = new AbortController();
    const invalidate = () => {
      if (!active) return;
      runtimeRef.current?.resetAuthentication();
      csrfRef.current = ''; setQr(null); setSession(null); setBoot('PAIR');
      setProblem('전광판 연결이 만료됐어요. 새 등록 코드로 연결해 주세요.');
    };
    const observedFetch = async (path, options) => {
      const response = await fetcher(path, options);
      if (AUTH_ERRORS.has(response.status)) invalidate();
      if (active && String(path).includes('/api/display/')) {
        try {
          const data = await response.clone().json();
          if (active && response.ok && data.mode) { setMode(data.mode); setProblem(''); }
          if (active && data.code === 'PLAYBACK_OWNED') setProblem('다른 창에서 전광판을 재생하고 있어요. 그 창을 닫은 뒤 잠시 기다려 주세요.');
        } catch { /* SSE is not consumed through this fetch adapter. */ }
      }
      return response;
    };
    const renderer = {
      async prepare(item, { signal }) {
        if (signal.aborted) throw signal.reason;
        if (item.sceneVersion !== '1' || !Object.hasOwn(THEMES, item.sceneId)) {
          if (active) setProblem('이 기분의 배경을 준비하지 못했어요. 전광판 화면을 새로 열어 주세요.');
          throw new Error('UNSUPPORTED_SCENE');
        }
        // Fontsource splits Hangul into unicode-range files. fonts.ready alone
        // covers only glyphs already used by the page, not this incoming text.
        await document.fonts?.load('400 96px "Jua"', item.content);
        if (signal.aborted) throw signal.reason;
        await document.fonts?.ready;
        if (signal.aborted) throw signal.reason;
      },
      show(item) {
        if (!active || document.visibilityState !== 'visible') throw new Error('PAGE_HIDDEN');
        // The player measures its first frame only after this synchronous commit.
        flushSync(() => setSpotlight(item));
        if (sceneElement.current) sceneElement.current.hidden = false;
      },
      hide() {
        // Hide immediately even when called during React unmount/effect cleanup.
        if (sceneElement.current) sceneElement.current.hidden = true;
        if (active) setSpotlight(null);
      },
    };
    const runtime = runtimeFactory({ boardId: session.boardId, renderer, csrfToken: () => csrfRef.current,
      fetch: observedFetch, renderBubbles(view) {
        if (!active) return;
        const signature = `${view.visible}:${view.bubbles.map(bubble => `${bubble.id}:${bubble.phase}`).join(',')}`;
        if (signature !== bubbleSignature) { bubbleSignature = signature; setBubbles(view); }
      }, onProblem(error) {
        if (!active) return;
        if (AUTH_ERRORS.has(error?.status)) invalidate();
        else setProblem(error?.code === 'PLAYBACK_OWNED' ? '다른 창에서 전광판을 재생하고 있어요.' : '연결을 다시 확인하고 있어요. 잠시만 기다려 주세요.');
      } });
    runtimeRef.current = runtime;
    const refreshQr = async () => {
      if (!active) return;
      if (qrBusy) { qrAgain = true; return; }
      qrBusy = true;
      clearTimeout(qrTimer);
      const started = performance.now();
      try {
        const ticket = await displayRequest('/api/display/qr', { signal: controller.signal, fetcher: observedFetch });
        const url = new URL(ticket.joinUrl);
        if (!['https:', 'http:'].includes(url.protocol)) throw new Error('INVALID_QR');
        const image = await QRCode.toDataURL(ticket.joinUrl, { errorCorrectionLevel: 'M', margin: 2, width: 440, color: { dark: '#32372fff', light: '#ffffffff' } });
        if (!active) return;
        const validFor = Date.parse(ticket.validUntil) - Date.parse(ticket.serverNow);
        const rotatesIn = Date.parse(ticket.rotatesAt) - Date.parse(ticket.serverNow);
        if (!Number.isFinite(validFor) || validFor <= 0 || !Number.isFinite(rotatesIn)) throw new Error('INVALID_QR');
        setQr({ image, expires: started + validFor });
        qrTimer = setTimeout(refreshQr, Math.max(1000, Math.min(30_000, rotatesIn)));
      } catch (error) {
        if (active && error.name !== 'AbortError') {
          setProblem('참여 QR을 새로 불러오고 있어요.');
          qrTimer = setTimeout(refreshQr, 5000);
        }
      } finally {
        qrBusy = false;
        if (active && qrAgain) { qrAgain = false; void refreshQr(); }
      }
    };
    let events;
    if (typeof EventSource !== 'undefined') {
      events = new EventSource('/api/display/events');
      events.addEventListener('state.changed', () => void runtime.refresh());
      events.addEventListener('qr.changed', () => void refreshQr());
      // Periodic HTTP synchronization continues while EventSource reconnects.
    }
    runtime.start();
    void refreshQr();
    return () => {
      active = false; runtime.stop(); runtimeRef.current = null;
      controller.abort(); clearTimeout(qrTimer); events?.close();
    };
  }, [session, runtimeFactory, fetcher, runtimeVersion]);

  async function pair(event) {
    event.preventDefault();
    if (pairing || code.trim().length < 16) return;
    setPairing(true); setProblem('');
    try {
      // Bootstrap recovery is serialized. A valid shared cookie needs its fresh
      // CSRF token; an expired cookie is cleared only by the server's pair POST.
      try {
        const current = await displayRequest('/api/display/session', { fetcher });
        csrfRef.current = current.csrfToken;
      } catch (error) {
        if (error.status !== 401) throw error;
        csrfRef.current = '';
      }
      const requestPair = () => displayRequest('/api/display/pair', { body: { code: code.trim() }, csrfToken: csrfRef.current, fetcher });
      let value;
      try { value = await requestPair(); }
      catch (error) {
        if (error.status === 401 && error.code === 'SESSION_EXPIRED') {
          csrfRef.current = '';
          value = await requestPair();
        } else if (error.status === 403 && error.code === 'CSRF_REJECTED') {
          const current = await displayRequest('/api/display/session', { fetcher });
          csrfRef.current = current.csrfToken;
          value = await requestPair();
        } else throw error;
      }
      csrfRef.current = value.csrfToken; setCode(''); setSession(value); setMode(value.mode); setBoot('READY'); setSettingsOpen(false);
    } catch (error) {
      // A lost pair response may still have installed its cookie. Recover via GET
      // instead of consuming a fresh code or claiming that registration failed.
      if (!error.status || error.status >= 500) {
        try {
          const recovered = await displayRequest('/api/display/session', { fetcher });
          csrfRef.current = recovered.csrfToken; setCode(''); setSession(recovered); setMode(recovered.mode); setBoot('READY');
          return;
        } catch { /* Show the original, actionable pairing error below. */ }
      }
      setProblem(PAIR_MESSAGES[error.code] || '전광판을 연결하지 못했어요. 등록 코드와 연결을 확인해 주세요.');
    }
    finally { setPairing(false); }
  }

  async function fullscreen() {
    try { await document.documentElement.requestFullscreen(); }
    catch { setProblem('브라우저에서 F11 키로 전체 화면을 열어 주세요.'); }
  }

  const validQr = qr && performance.now() < qr.expires;
  const date = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'long', day: 'numeric', weekday: 'long' }).format(now);
  const time = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hour12: false }).format(now);
  const theme = themeFor(spotlight);

  if (!session) return <main className="mw-display mw-setup">
    <section className="mw-setup-card">
      <a className="mw-wordmark" href="/">MOOD WALL<span>마음이 머무는 작은 벽</span></a>
      <WeatherIcon className="mw-setup-icon" />
      <h1>{boot === 'LOADING' ? '전광판을 연결하고 있어요' : '우리의 기분이 모일 곳'}</h1>
      <p>전광판에 연결된 노트북에서 열어 주세요.<br />운영자 화면에서 만든 등록 코드로 시작할 수 있어요.</p>
      {boot === 'LOADING' ? <div className="mw-loading" role="status">잠시만 기다려 주세요.</div> : boot === 'ERROR' ?
        <button className="mw-primary" onClick={() => void checkSession()}>다시 연결하기</button> :
        <form onSubmit={pair} className="mw-pair-form">
          <label htmlFor="display-code">전광판 등록 코드</label>
          <input id="display-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="off" autoCapitalize="none" spellCheck="false" maxLength={256} placeholder="등록 코드를 붙여 넣어 주세요" />
          <button className="mw-primary" disabled={pairing || code.trim().length < 16}>{pairing ? '연결하고 있어요…' : '전광판 연결하기'}</button>
        </form>}
      {problem && <p className="mw-setup-error" role="alert">{problem}</p>}
      <a className="mw-operator-link" href="/operator">운영자 화면 열기 <span aria-hidden="true">↗</span></a>
    </section>
  </main>;

  return <main className="mw-display" style={{ '--scene-color': theme.color, '--scene-tint': theme.tint }}>
    <BubbleField view={bubbles} />
    <div className="mw-wall-layout">
      <aside className="mw-join-panel">
        <div className="mw-wordmark">MOOD WALL<span>마음이 머무는 작은 벽</span></div>
        <h1>오늘 기분,<br />한 줄로 남겨요<span className="mw-title-dot">.</span></h1>
        <div className="mw-qr-box">{validQr ? <img src={qr.image} alt="기분을 남기는 참여 QR 코드" /> : <span>참여 QR을<br />준비하고 있어요</span>}</div>
        <p>폰 카메라로 찍어주세요<br /><strong>30자 · 1시간에 3번</strong></p>
        <span className="mw-open-badge"><i />지금, 여기의 우리</span>
      </aside>

      <section className="mw-wall-center">
        <header className="mw-wall-heading"><div><p>{date}</p><h2>오늘 <em>{bubbles.totalCount}</em>개의 기분이<br />모였어요</h2></div><time>{time}</time></header>
        {!bubbles.totalCount && <div className="mw-empty"><WeatherIcon /><p>이 벽의 첫 번째 기분을<br />남겨볼까요?</p></div>}
        <div className="mw-wall-caption"><span aria-hidden="true">↗</span> QR을 찍으면 내 한 줄이 화면 가득 떠요</div>
      </section>
      <MoodForecast bubbles={bubbles.visible ? bubbles.bubbles : []} />
    </div>

    <section ref={sceneElement} className="mw-spotlight" hidden={!spotlight} aria-live="polite" aria-label="새로운 메시지">
      {spotlight && <div className="mw-spotlight-inner" key={spotlight.id}>
        <SceneParticles item={spotlight} />
        <EmotionMark className="mw-scene-mark" sceneId={spotlight.sceneId} />
        <header><span className="mw-emotion-pill">{theme.label}</span><p>새로운 기분이 도착했어요</p></header>
        <p className="mw-full-message">{spotlight.content}</p>
        <footer><span>익명 · 방금 전</span><div className="mw-spotlight-qr"><p>나도 남기기<br /><small>QR을 찍어주세요</small></p>{validQr && <img src={qr.image} alt="기분을 남기는 참여 QR 코드" />}</div></footer>
        <div className="mw-duration-track" aria-hidden="true"><i /></div>
      </div>}
    </section>

    {mode !== 'MOODWALL' && <div className="mw-paused"><WeatherIcon /><h2>잠시 쉬어가는 중이에요</h2><p>곧 더 많은 기분으로 만나요.</p></div>}
    {problem && <div className="mw-connection-note" role="status">{problem}</div>}
    <div className="mw-display-tools"><button aria-label="전체 화면 열기" title="전체 화면" onClick={() => void fullscreen()}>⛶</button><button aria-label="전광판 연결 설정" aria-expanded={settingsOpen} title="연결 설정" onClick={() => setSettingsOpen(!settingsOpen)}>⋯</button></div>
    {settingsOpen && <aside className="mw-connection-panel"><h2>전광판 연결</h2><p>이 노트북의 연결을 다시 확인해요.</p><button onClick={() => { setProblem(''); setRuntimeVersion(value => value + 1); setSettingsOpen(false); }}>연결 다시 확인하기</button><a href="/operator">운영자 화면</a>
      <section className="mw-bubble-management" aria-label="말풍선 관리"><h3>말풍선 관리</h3><p>운영자 로그인 후 숨길 수 있어요.<br />투명해진 말풍선도 선택할 수 있어요.</p>
        {bubbles.bubbles.length ? <ul>{bubbles.bubbles.map(bubble => <li key={bubble.id}>
          <a href={`/operator#hide=${encodeURIComponent(bubble.id)}`} target="_blank" rel="noreferrer"><span>{bubble.content}</span><small>숨김 관리 ↗</small></a>
        </li>)}</ul> : <p>현재 표시 중인 말풍선이 없어요.</p>}
      </section><button onClick={() => setSettingsOpen(false)}>닫기</button></aside>}
  </main>;
}
