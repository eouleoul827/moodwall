import { useCallback, useEffect, useRef, useState } from 'react';
import { readPlaybackBubbleCatalog } from '@clients/display/playback-bubble-cache.mjs';
import { AUTH_ERRORS, displayRequest, notifyDisplaySession, pairDisplay, useDisplaySession } from './display-session.js';
import './display.css';

const PAIR_MESSAGES = {
  PAIRING_CODE_INVALID: '등록 코드가 맞지 않거나 만료됐어요. 새 코드를 확인해 주세요.',
  PAIRING_INVALID: '등록 코드가 맞지 않거나 만료됐어요. 새 코드를 확인해 주세요.',
  INVALID_PAIRING_CODE: '등록 코드가 맞지 않거나 만료됐어요. 새 코드를 확인해 주세요.',
  RATE_LIMITED: '잠시 기다린 뒤 다시 시도해 주세요.',
};

/** Staff explicitly opens this page in the display profile. It never creates a playback runtime. */
export default function DisplaySetupApp({ fetcher = fetch, storage, catalogReader = readPlaybackBubbleCatalog }) {
  const { session, boot, busy, problem: sessionProblem, check, invalidate, accept } = useDisplaySession(fetcher);
  const [code, setCode] = useState(''), [pairing, setPairing] = useState(false), [problem, setProblem] = useState('');
  const [bubbles, setBubbles] = useState([]), [catalogProblem, setCatalogProblem] = useState('');
  const lifecycle = useRef(0);
  useEffect(() => { const current = ++lifecycle.current; return () => { if (lifecycle.current === current) lifecycle.current++; }; }, []);

  useEffect(() => {
    if (!session) { setBubbles([]); return; }
    let active = true, inFlight = false, request = null;
    const refresh = async () => {
      if (!active || inFlight) return;
      inFlight = true; const controller = new AbortController(); request = controller;
      try {
        const snapshots = [], seen = new Set(); let cursor = null;
        do {
          const value = await displayRequest(`/api/v2/display/state${cursor === null ? '' : `?cursor=${encodeURIComponent(cursor)}`}`, { fetcher, signal: controller.signal });
          if (!active) return;
          snapshots.push(value); cursor = value.nextCursor;
          if (cursor !== null && (typeof cursor !== 'string' || !cursor.length || cursor.length > 2048 || seen.has(cursor))) throw new Error('INVALID_CURSOR');
          seen.add(cursor);
          if (snapshots.length >= 10_000 && cursor !== null) throw new Error('PAGE_LIMIT');
        } while (cursor !== null);
        let localStorage = storage;
        if (localStorage === undefined) { try { localStorage = window.localStorage; } catch { localStorage = null; } }
        const catalog = catalogReader({ boardId: session.boardId, storage: localStorage, snapshots });
        if (active) { setBubbles(catalog.bubbles); setCatalogProblem(catalog.storageAvailable ? '' : '이 브라우저의 말풍선 보관함을 읽을 수 없어요.'); }
      } catch (error) {
        if (!active || controller.signal.aborted) return;
        setBubbles([]);
        if (AUTH_ERRORS.has(error.status)) invalidate();
        else setCatalogProblem('말풍선 목록을 확인하지 못했어요. 연결 후 다시 확인해 주세요.');
      } finally { inFlight = false; request = null; }
    };
    void refresh();
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 15_000);
    window.addEventListener('storage', refresh);
    return () => { active = false; request?.abort(); clearInterval(timer); window.removeEventListener('storage', refresh); };
  }, [session, fetcher, storage, catalogReader, invalidate]);

  const pair = async event => {
    event.preventDefault(); if (pairing || code.trim().length < 16) return;
    const current = lifecycle.current;
    setPairing(true); setProblem('');
    try {
      const value = await pairDisplay(code.trim(), fetcher);
      if (current !== lifecycle.current) return;
      setCode(''); accept(value); notifyDisplaySession();
    } catch (error) {
      if (current === lifecycle.current) setProblem(Object.hasOwn(PAIR_MESSAGES, error.code) ? PAIR_MESSAGES[error.code] : '전광판을 연결하지 못했어요. 등록 코드와 연결을 확인해 주세요.');
    } finally { if (current === lifecycle.current) setPairing(false); }
  };
  const fullscreen = useCallback(async () => {
    try { await document.documentElement.requestFullscreen(); }
    catch { setProblem('송출 화면을 연 뒤 F11 키로 전체 화면을 열어 주세요.'); }
  }, []);

  return <main className="mw-display mw-setup">
    <section className="mw-setup-card">
      <div className="mw-wordmark">MOOD WALL<span>직원용 전광판 설정</span></div>
      <h1>{session ? '전광판이 등록되어 있어요' : boot === 'LOADING' ? '전광판 연결 확인' : '전광판 등록'}</h1>
      <p>이 화면은 전광판에 연결된 전용 브라우저에서 사용해 주세요. 설정을 마친 뒤 송출 화면을 열어 주세요.</p>
      {boot === 'LOADING' ? <div className="mw-loading" role="status">연결을 확인하고 있어요.</div> : null}
      {boot === 'PAIR' && <form onSubmit={pair} className="mw-pair-form">
        <label htmlFor="display-code">전광판 등록 코드</label>
        <input id="display-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="off" autoCapitalize="none" spellCheck="false" maxLength={256} placeholder="등록 코드를 붙여 넣어 주세요" />
        <button className="mw-primary" disabled={pairing || code.trim().length < 16}>{pairing ? '연결하고 있어요…' : '전광판 연결하기'}</button>
      </form>}
      {(problem || sessionProblem) && <p className="mw-setup-error" role="alert">{problem || sessionProblem}</p>}
      <button className="mw-primary" disabled={busy || pairing} onClick={() => { setProblem(''); void check(); }}>다시 연결하기</button>
      {session && <>
        <p>화면 송출과 접수의 중단·재개는 운영자 화면에서 따로 관리해 주세요.</p>
        <a className="mw-operator-link" href="/display">송출 화면 열기</a>
        <button className="mw-primary" onClick={() => void fullscreen()}>전체 화면 열기</button>
        <section className="mw-bubble-management" aria-label="말풍선 관리">
          <h2>말풍선 관리</h2><p>투명해진 말풍선도 선택할 수 있어요. 운영자 로그인 후 숨김을 직접 실행해 주세요.</p>
          {catalogProblem && <p role="status">{catalogProblem}</p>}
          <ul>{bubbles.map(bubble => <li key={bubble.id}><a href={`/operator#hide=${encodeURIComponent(bubble.id)}`} target="_blank" rel="noreferrer"><span>{bubble.content}</span><small>숨김 관리 ↗</small></a></li>)}</ul>
        </section>
      </>}
      <a className="mw-operator-link" href="/operator" target="_blank" rel="noreferrer">운영자 화면 열기 ↗</a>
      <p>연결이 확인됐는데 송출 화면이 복구되지 않으면, 송출 창을 선택해 새로고침해 주세요. 브라우저 데이터는 지우지 마세요.</p>
    </section>
  </main>;
}
