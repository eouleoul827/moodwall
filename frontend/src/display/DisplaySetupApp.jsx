import { useCallback, useEffect, useRef, useState } from 'react';
import { displayRequest, notifyDisplaySession, pairDisplay, useDisplaySession } from './display-session.js';
import './display.css';

const PAIR_MESSAGES = {
  PAIRING_CODE_INVALID: '등록 코드가 맞지 않거나 만료됐어요. 새 코드를 확인해 주세요.',
  PAIRING_INVALID: '등록 코드가 맞지 않거나 만료됐어요. 새 코드를 확인해 주세요.',
  INVALID_PAIRING_CODE: '등록 코드가 맞지 않거나 만료됐어요. 새 코드를 확인해 주세요.',
  RATE_LIMITED: '잠시 기다린 뒤 다시 시도해 주세요.',
};

/** Staff explicitly opens this page in the display profile. It never creates a playback runtime. */
export default function DisplaySetupApp({ fetcher = fetch }) {
  const { session, boot, busy, problem: sessionProblem, check, accept } = useDisplaySession(fetcher);
  const [code, setCode] = useState(''), [pairing, setPairing] = useState(false), [problem, setProblem] = useState('');
  const [publicOrigin, setPublicOrigin] = useState('');
  const lifecycle = useRef(0);
  useEffect(() => { const current = ++lifecycle.current; return () => { if (lifecycle.current === current) lifecycle.current++; }; }, []);

  // Every POST must carry the server's PUBLIC_ORIGIN. Opening the wall from another
  // address (localhost vs 127.0.0.1, LAN IP vs HTTPS domain) silently breaks it.
  useEffect(() => {
    if (!session) { setPublicOrigin(''); return; }
    const controller = new AbortController();
    displayRequest('/api/display/qr', { fetcher, signal: controller.signal })
      .then(ticket => { try { setPublicOrigin(new URL(ticket.joinUrl).origin); } catch { setPublicOrigin(''); } })
      .catch(() => {});
    return () => controller.abort();
  }, [session, fetcher]);
  const originMismatch = Boolean(publicOrigin && publicOrigin !== window.location.origin);

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
      {originMismatch && <p className="mw-setup-error" role="alert">
        이 화면 주소({window.location.origin})가 서버의 공개 주소({publicOrigin})와 달라요. 이대로는 전광판이 송출되지 않아요. 런처를 공개 주소로 다시 설치해 주세요.</p>}
      <button className="mw-primary" disabled={busy || pairing} onClick={() => { setProblem(''); void check(); }}>다시 연결하기</button>
      {session && <>
        <p>화면 송출과 접수의 중단·재개는 운영자 화면에서 따로 관리해 주세요.</p>
        <a className="mw-operator-link" href="/display">송출 화면 열기</a>
        <button className="mw-primary" onClick={() => void fullscreen()}>전체 화면 열기</button>
      </>}
      <a className="mw-operator-link" href="/operator" target="_blank" rel="noreferrer">운영자 화면 열기 ↗</a>
      <p>연결이 확인됐는데 송출 화면이 복구되지 않으면, 송출 창을 선택해 새로고침해 주세요. 브라우저 데이터는 지우지 마세요.</p>
    </section>
  </main>;
}
