import { useCallback, useEffect, useRef, useState } from 'react';
import { SubmissionClient } from '@clients/student/submission-client.mjs';
import { createStudentApi, studentErrorMessage, takeAdmissionTicket } from './api.js';
import { characterCount, validateMention } from './graphemes.js';
import { StudentHelp } from './StudentHelp.jsx';
import './student.css';

const defaultApi = createStudentApi();
const defaultClient = options => new SubmissionClient(options);
const COMPLETE = { card: 'COMPLETE', title: '메시지를 보냈어요!',
  message: '메시지 확인 후 표시 여부가 정해져요.\n전광판에 표시되면 15초 동안 크게 보여요.' };

function formatTime(value) {
  return new Intl.DateTimeFormat('ko-KR', { hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Seoul' }).format(new Date(value));
}

function postingAvailability(session, usage, now) {
  if (!session || !session.admissionValid || Date.parse(session.admissionExpiresAt) <= now) {
    return { blocked: true, text: '전광판의 새 QR을 찍으면 참여할 수 있어요.' };
  }
  if (usage?.suspendedUntil && Date.parse(usage.suspendedUntil) > now) {
    return { blocked: true, text: '지금은 작성할 수 없어요. 내일 다시 참여해 주세요.' };
  }
  if (usage?.remainingSlots === 0) {
    return { blocked: true, text: usage.nextSlotAt ? `${formatTime(usage.nextSlotAt)}부터 다시 쓸 수 있어요.` : '잠시 후 다시 쓸 수 있어요.' };
  }
  if (usage?.nextSubmissionAt && Date.parse(usage.nextSubmissionAt) > now || usage?.pendingCount > 0) {
    return { blocked: true, text: '잠시 후 다시 보낼 수 있어요.' };
  }
  return { blocked: false, text: '' };
}

function MoodPreview({ content, complete = false }) {
  return <div className={`student-preview${complete ? ' is-complete' : ''}`}>
    <svg className="student-preview-cloud" viewBox="0 0 160 100" aria-hidden="true"><path d="M26 82C2 82 0 48 23 43 20 14 61 3 76 26 97 2 134 18 131 43 161 44 167 82 139 82Z" /></svg>
    <p>{content || '여기에 내 멘션이 보여요'}</p>
  </div>;
}

export function StudentApp({ api = defaultApi, createSubmissionClient = defaultClient, initialTicket, reload = () => window.location.reload() } = {}) {
  const [ticket] = useState(() => initialTicket === undefined ? takeAdmissionTicket() : initialTicket);
  const [session, setSession] = useState(null);
  const [usage, setUsage] = useState(null);
  const [boot, setBoot] = useState('loading');
  const [screen, setScreen] = useState('compose');
  const [text, setText] = useState('');
  const [preview, setPreview] = useState('');
  const [result, setResult] = useState(null);
  const [sending, setSending] = useState(false);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const [notice, setNotice] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [clock, setClock] = useState(Date.now());
  const [bootKey, setBootKey] = useState(0);
  const [helpOpen, setHelpOpen] = useState(false);
  const [updateRequired, setUpdateRequired] = useState(false);
  const csrfRef = useRef('');
  const clockOffset = useRef(0);
  const mounted = useRef(false);
  const screenRef = useRef(screen);
  const refreshReceiptRef = useRef(() => {});
  const refreshingReceipt = useRef(false);
  screenRef.current = screen;
  const [client] = useState(() => createSubmissionClient({ csrfToken: () => csrfRef.current,
    wallNow: () => Date.now() + clockOffset.current }));

  const updateSession = useCallback(value => {
    csrfRef.current = value?.csrfToken || '';
    setSession(value);
    setUsage(value?.usage || null);
    setClock(Date.now());
    if (value?.serverNow) clockOffset.current = Date.parse(value.serverNow) - Date.now();
  }, []);

  const refreshUsage = useCallback(async () => {
    try {
      const value = await api.usage();
      if (mounted.current) setUsage(value);
    } catch { /* A read outage does not turn an accepted message into a failure. */ }
  }, [api]);

  const refreshSession = useCallback(async () => {
    try {
      const value = await api.session();
      if (mounted.current) updateSession(value);
      return value;
    } catch { /* Keep the current draft during a temporary connection failure. */ }
  }, [api, updateSession]);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    setBoot('loading');
    setConnectionError('');
    api.connect(ticket, controller.signal).then(async value => {
      if (controller.signal.aborted) return;
      updateSession(value);
      const activeId = value?.usage?.activeSubmissionId;
      let recovered = false;
      if (value && client.hasPendingRecovery) {
        const restored = await client.restorePending({ signal: controller.signal });
        if (!controller.signal.aborted && restored) { recovered = true; await applyResult(restored); }
      }
      if (!recovered && activeId && !client.intent) {
        const restored = await client.getReceipt(activeId, { signal: controller.signal });
        if (!controller.signal.aborted && restored.state === 'ACCEPTED') {
          setResult(restored);
          setScreen('receipt');
          watchAccepted();
        } else if (!controller.signal.aborted && restored.state === 'UPDATE_REQUIRED') {
          setUpdateRequired(true);
        }
      }
      if (!controller.signal.aborted) setBoot('ready');
    }).catch(error => {
      if (controller.signal.aborted) return;
      setConnectionError(studentErrorMessage(error.code));
      setBoot('error');
    });
    return () => { mounted.current = false; controller.abort(); };
  }, [api, ticket, bootKey, updateSession, client]);

  useEffect(() => () => client.resetAuthentication(), [client]);

  useEffect(() => {
    if (boot !== 'ready') return;
    const timer = setInterval(() => {
      setClock(Date.now());
      if (session && document.visibilityState === 'visible') void refreshUsage();
    }, 5000);
    const visible = () => {
      setClock(Date.now());
      if (document.visibilityState === 'visible') {
        void refreshSession().then(fresh => {
          if (fresh && mounted.current) void refreshReceiptRef.current();
        });
      }
    };
    document.addEventListener('visibilitychange', visible);
    window.addEventListener('focus', visible);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', visible); window.removeEventListener('focus', visible); };
  }, [boot, session, refreshUsage, refreshSession]);

  const availability = postingAvailability(session, usage, clock + clockOffset.current);
  const length = characterCount(text.trim());
  const validation = validateMention(text);
  const view = result?.view || COMPLETE;

  const watchAccepted = () => {
    void client.watchReceipt({ intervalMs: 3000, onChange: next => {
      if (!mounted.current) return;
      if (next.state === 'UPDATE_REQUIRED') { setUpdateRequired(true); return; }
      if (next.state !== 'ACCEPTED') return;
      setResult(next);
      if (next.view.card === 'SUPPORT') setPreview('');
      if (screenRef.current === 'compose' && next.view.card !== 'COMPLETE') {
        setNotice(`이전 메시지: ${next.view.message}`);
      }
      void refreshUsage();
    } }).catch(() => {});
  };

  const applyResult = async next => {
    if (!mounted.current || next.state === 'STALE') return;
    if (next.state === 'ACCEPTED') {
      setResult(next);
      setUnconfirmed(false);
      setNotice('');
      setScreen('receipt');
      if (next.view.card === 'SUPPORT') { setPreview(''); setText(''); }
      watchAccepted();
      void refreshUsage();
    } else if (next.state === 'UPDATE_REQUIRED') {
      setUpdateRequired(true);
      setNotice('화면이 업데이트됐어요. 새로고침한 뒤 다시 확인해 주세요.');
    } else if (next.state === 'UNKNOWN') {
      setUnconfirmed(true);
      setNotice(client.recoveryPersistent === false
        ? '복구 정보를 보관하지 못했어요. 이 화면을 닫거나 새로고침하지 말고 다시 확인해 주세요.'
        : client.canResend === false
          ? '이전 메시지의 결과를 확인하지 못했어요. 원문은 저장하지 않아 다시 전송하지 않아요. 다시 확인해 주세요.'
          : '전송 결과를 확인하지 못했어요. 다시 확인해 주세요.');
    } else if (next.state === 'AUTH_REQUIRED') {
      setUnconfirmed(true);
      setNotice('접속을 다시 확인해 주세요. 같은 메시지의 결과를 확인할게요.');
    } else if (next.state === 'REJECTED') {
      setUnconfirmed(false);
      setNotice(studentErrorMessage(next.code));
      if (next.code === 'ADMISSION_EXPIRED') {
        setSession(current => current ? { ...current, admissionValid: false } : current);
      }
      void refreshUsage();
    }
  };

  refreshReceiptRef.current = async () => {
    if (refreshingReceipt.current || updateRequired || sending || !client.receipt && !unconfirmed) return;
    refreshingReceipt.current = true;
    try {
      const next = client.receipt ? await client.getReceipt() : await client.recover();
      if (!mounted.current || next.state === 'STALE') return;
      if (unconfirmed) { await applyResult(next); return; }
      if (next.state === 'UPDATE_REQUIRED') { setUpdateRequired(true); return; }
      if (next.state !== 'ACCEPTED') return;
      setResult(next);
      if (next.view.card === 'SUPPORT') setPreview('');
      if (screenRef.current === 'compose' && next.view.card !== 'COMPLETE') setNotice(`이전 메시지: ${next.view.message}`);
      watchAccepted();
    } catch { /* Keep the accepted card or original uncertain key during a read outage. */ }
    finally { refreshingReceipt.current = false; }
  };

  const send = async event => {
    event?.preventDefault();
    if (sending || unconfirmed || updateRequired || availability.blocked) return;
    if (validation) { setNotice(validation); return; }
    setSending(true);
    setNotice('');
    setPreview(text.trim().normalize('NFC'));
    try {
      client.begin(text.trim().normalize('NFC'));
      let next = await client.send();
      // One quiet lookup resolves a lost response without creating a second post.
      if (next.state === 'UNKNOWN') next = await client.recover();
      await applyResult(next);
    } catch { if (mounted.current) { setNotice('전송 결과를 다시 확인해 주세요.'); setUnconfirmed(true); } }
    finally { if (mounted.current) setSending(false); }
  };

  const recover = async (resend = false) => {
    if (sending || updateRequired) return;
    setSending(true);
    try {
      const fresh = await api.session();
      if (!mounted.current) return;
      updateSession(fresh);
      if (!fresh) {
        setNotice('접속 시간이 지났어요. 전광판의 QR로 다시 들어와 주세요.');
        return;
      }
      await applyResult(await (resend && client.canResend !== false ? client.send() : client.recover()));
    } catch { if (mounted.current) setNotice('연결이 원활하지 않아요. 잠시 후 다시 확인해 주세요.'); }
    finally { if (mounted.current) setSending(false); }
  };

  const writeAgain = () => {
    if (availability.blocked || updateRequired) return;
    setText(['REWRITE', 'UNCERTAIN'].includes(view.card) ? preview : '');
    setNotice('');
    setResult(null);
    setScreen('compose');
    void refreshUsage();
  };

  return <main className="student-shell">
    <div className="student-page">
      {updateRequired && <aside className="student-update" role="alert"><p>화면이 업데이트됐어요. 새로고침하면 작성 중인 글이 지워져요. 자동으로 다시 보내지 않아요.</p><button type="button" className="student-button student-button-secondary" onClick={reload}>새로고침하기</button></aside>}
      {boot === 'loading' ? <section className="student-entry" aria-live="polite"><div className="student-loading" aria-hidden="true" /><h1>무드월에 들어가는 중이에요</h1><p>잠깐만 기다려 주세요.</p></section>
        : boot === 'error' || !session ? <section className="student-entry">
          <div className="student-entry-mark" aria-hidden="true">✦</div><h1>지금의 기분을<br />함께 나눠요</h1>
          <p role={connectionError ? 'alert' : undefined}>{connectionError || '전광판에 있는 QR을 찍으면\n로그인 없이 바로 참여할 수 있어요.'}</p>
          {boot === 'error' && <button className="student-button" onClick={() => setBootKey(value => value + 1)}>다시 연결하기</button>}
        </section> : screen === 'compose' ? <form className="student-compose" onSubmit={send}>
          <div className="student-compose-main">
            <div className="student-intro"><h1>지금 기분이 어때요?</h1></div>
            <div className="student-input-group">
              <label className="student-sr-only" htmlFor="mention">멘션 입력</label>
              <textarea id="mention" rows="2" placeholder="오늘 기분을 한 줄로 남겨주세요" value={text} readOnly={sending || unconfirmed || updateRequired}
                onChange={event => { setText(event.target.value); setNotice(''); }} aria-describedby="mention-count mention-rules" aria-invalid={length > 30} />
              <div id="mention-count" className={`student-counter${length > 30 ? ' is-over' : ''}`}>{length}<span>/30</span></div>
            </div>
            {(notice || availability.text) && <p className="student-notice" role={notice ? 'alert' : 'status'}>{notice || availability.text}</p>}
          </div>
          <div className="student-actions">
            {unconfirmed ? <><button type="button" className="student-button" disabled={sending || updateRequired} onClick={() => recover()}>{sending ? '확인하는 중…' : '다시 확인'}</button>{client.canResend !== false && <button type="button" className="student-text-button" disabled={sending || updateRequired} onClick={() => recover(true)}>같은 메시지 다시 보내기</button>}</>
              : <button className="student-button" type="submit" disabled={sending || updateRequired || Boolean(validation) || availability.blocked}>{sending ? '보내는 중…' : '전광판에 올리기'}</button>}
            <p id="mention-rules" className="student-rules">서로를 배려하는 한마디를 남겨 주세요.</p>
          </div>
        </form> : <section className="student-receipt">
          <div className="student-receipt-main">
            <div className={`student-result-mark${view.card === 'SUPPORT' ? ' is-support' : view.card === 'REWRITE' ? ' is-notice' : view.card !== 'COMPLETE' ? ' is-neutral' : ''}`} aria-hidden="true">{view.card === 'COMPLETE' ? <svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7.5" /></svg> : view.card === 'SUPPORT' ? '♡' : view.card === 'REWRITE' ? '!' : '…'}</div>
            <div aria-live="polite"><h1>{view.card === 'COMPLETE' ? COMPLETE.title : view.title}</h1><p className="student-result-copy">{view.card === 'COMPLETE' ? COMPLETE.message : view.message}</p></div>
            {preview && view.card !== 'SUPPORT' && <MoodPreview content={preview} complete />}
            {view.card === 'COMPLETE' && <div className="student-wait-note"><p>표시되는 메시지는 앞선 구름이 지나간 뒤 순서대로 보여요.</p></div>}
          </div>
          <div className="student-actions">
            {view.card === 'SUPPORT' && <button className="student-button" type="button" onClick={() => setHelpOpen(true)}>도움받는 방법 보기</button>}
            <button className="student-button student-button-secondary" disabled={availability.blocked || updateRequired} onClick={writeAgain}>{view.card === 'SUPPORT' ? '작성 화면으로' : view.card === 'REWRITE' ? '수정하기' : view.card === 'UNCERTAIN' ? '다시 작성하기' : view.card === 'COMPLETE' ? '하나 더 쓰기' : '다시 쓰기'}</button>
            {availability.text && <p className="student-rules" role="status">{availability.text}</p>}
          </div>
        </section>}
      <StudentHelp open={helpOpen} onOpenChange={setHelpOpen} api={api} csrfToken={() => csrfRef.current} refreshSession={refreshSession} canReport={Boolean(session)} showReportEntry={screen === 'receipt'} />
      <footer className="student-footer">moodwall · 우리의 오늘이 모이는 곳</footer>
    </div>
  </main>;
}

export default StudentApp;
