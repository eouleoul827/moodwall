import { useCallback, useEffect, useRef, useState } from "react";
import { request as defaultRequest, operatorMessage } from "../shared/api.js";
import { secureUuid } from "@clients/uuid.mjs";
import "./operator.css";

const emotionNames = {
  CELEBRATION: "축하", JOY: "기쁨", SUPPORT: "응원", EXCITEMENT: "설렘",
  TIRED: "피곤", SADNESS: "슬픔", ANGER: "화남", NEUTRAL: "차분함",
};
const statusNames = {
  PENDING: "준비 중", PROCESSING: "준비 중", APPROVED: "검수 완료", REJECTED: "수정 필요",
  NEEDS_REWRITE: "수정 필요", FAILED: "처리 실패", EXPIRED: "기한 종료", CANCELLED: "취소",
};
const reportCategories = { HARASSMENT: "괴롭힘·모욕", PRIVACY: "개인정보 노출", THREAT: "위협·위험한 내용", OTHER: "그 밖의 문제" };
const reportResolutions = { HIDDEN: "해당 글을 숨김", BOARD_PAUSED: "화면 송출을 중단함", NO_ACTION: "조치할 내용 없음", INSUFFICIENT_EVIDENCE: "확인할 자료 부족" };
const providerModes = { OPEN: "검수 가능", PAUSED: "검수 중단됨", OPEN_CIRCUIT: "복구 확인 중", HALF_OPEN: "복구 확인 중" };
const providerFailures = {
  PROVIDER_AUTH_ERROR: "API 키와 프로젝트 권한을 확인해 주세요. 설정을 고친 뒤 검수 재개 확인을 눌러 주세요.",
  PROVIDER_RATE_LIMITED: "API 사용량 한도에 도달했어요. 대기 후 복구를 확인합니다.",
  PROVIDER_TIMEOUT: "검수 응답 시간이 초과됐어요. 해당 글을 자동으로 재전송하지 않아요.",
  PROVIDER_UNAVAILABLE: "검수 서비스에 연결하지 못했어요. 서버의 연결 상태를 확인해 주세요.",
  INVALID_PROVIDER_OUTPUT: "검수 응답을 확인하지 못했어요. 서버 설정을 확인한 뒤 검수를 재개해 주세요.",
  PROVIDER_INPUT_LIMIT: "검수 입력 한도를 초과했어요. 서버 설정을 확인해 주세요.",
  PROVIDER_TARIFF_EXPIRED: "사용료 기준을 갱신한 뒤 서버를 다시 시작해 주세요.",
  MODEL_NOT_READY: "서버의 API 키 설정과 검수 작업자의 시작 상태를 확인해 주세요.",
};
const MESSAGE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function takeHideTarget() {
  const target = new URLSearchParams(window.location.hash.slice(1)).get("hide");
  if (!target || !MESSAGE_ID.test(target)) return "";
  // A display link only selects a target. It never performs an operator action.
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return target;
}
const localTime = value => value ? new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hour12: false,
}).format(new Date(value)) : "—";

export default function OperatorApp({ api = defaultRequest }) {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sessionCheckFailed, setSessionCheckFailed] = useState(false);
  const [sessionCheck, setSessionCheck] = useState(0);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [items, setItems] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [pairing, setPairing] = useState(null);
  const [mode, setMode] = useState(null);
  const [hideTarget, setHideTarget] = useState(takeHideTarget);
  const [reports, setReports] = useState([]);
  const [reportCursor, setReportCursor] = useState(null);
  const [reportError, setReportError] = useState("");
  const [resolutions, setResolutions] = useState({});
  const [pendingResolutions, setPendingResolutions] = useState({});
  const [provider, setProvider] = useState(null);
  const resolutionIntents = useRef(new Map());
  const sequence = useRef(0);
  const readSequence = useRef(0);
  const reportReadSequence = useRef(0);
  const providerReadSequence = useRef(0);
  const isAdmin = session?.role === "ADMIN";
  const providerIsMock = provider?.profile === "gemini_mock" && provider.transportConfigured === false;
  const providerConnecting = provider?.profile === "gemini" && provider.mode === "OPEN"
    && (!provider.transportConfigured || provider.workerReady === false);

  useEffect(() => {
    if (session) return;
    setReports([]); setReportCursor(null); setReportError(""); setProvider(null);
    setPendingResolutions({}); setResolutions({}); resolutionIntents.current.clear();
  }, [session]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setSessionCheckFailed(false); setError("");
    api("/api/operator/session", { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setSession(value); })
      .catch(reason => {
        if (controller.signal.aborted) return;
        if (reason.status === 401) { setSession(null); return; }
        // A failed read cannot prove the cookie is absent. A login POST with
        // that existing cookie would require its CSRF, so verify it first.
        setSessionCheckFailed(true); setError(operatorMessage(reason));
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); sequence.current++; readSequence.current++; reportReadSequence.current++; };
  }, [api, sessionCheck]);

  const refresh = useCallback(async (nextCursor = null) => {
    const epoch = sequence.current;
    const read = ++readSequence.current;
    try {
      const query = nextCursor ? `&cursor=${encodeURIComponent(nextCursor)}` : "";
      const result = await api(`/api/operator/submissions?limit=30${query}`);
      if (epoch !== sequence.current || read !== readSequence.current) return;
      setItems(previous => nextCursor ? [...new Map([...previous, ...result.items]
        .map(item => [item.submission.id, item])).values()] : result.items);
      setCursor(result.nextCursor);
    } catch (reason) {
      if (epoch !== sequence.current || read !== readSequence.current) return;
      if (reason.status === 401) { setSession(null); setItems([]); setPairing(null); }
      setError(operatorMessage(reason));
    }
  }, [api]);

  const refreshReports = useCallback(async (nextCursor = null) => {
    const epoch = sequence.current, read = ++reportReadSequence.current;
    try {
      const query = nextCursor ? `?cursor=${encodeURIComponent(nextCursor)}` : "";
      const result = await api(`/api/operator/reports${query}`);
      if (epoch !== sequence.current || read !== reportReadSequence.current) return;
      // Report cards never render a message body or a reporter's identity.
      const entries = result.items.filter(item => MESSAGE_ID.test(item.reportId) && reportCategories[item.category]
        && ["RECEIVED", "RESOLVED"].includes(item.status));
      setReports(previous => nextCursor ? [...new Map([...previous, ...entries].map(item => [item.reportId, item])).values()] : entries);
      setReportCursor(result.nextCursor); setReportError("");
    } catch (reason) {
      if (epoch !== sequence.current || read !== reportReadSequence.current) return;
      if (reason.status === 401) { setSession(null); setReports([]); setItems([]); setPairing(null); }
      setReportError(operatorMessage(reason));
    }
  }, [api]);

  const refreshProvider = useCallback(async () => {
    const epoch = sequence.current, read = ++providerReadSequence.current;
    try {
      const value = await api("/api/operator/moderation/provider");
      if (epoch === sequence.current && read === providerReadSequence.current && typeof value.enabled === "boolean") setProvider(value);
    } catch { /* Optional status must not disable emergency display controls. */ }
  }, [api]);

  useEffect(() => {
    if (!session) return;
    void refresh();
    void refreshReports();
    void refreshProvider();
    const timer = setInterval(() => { if (document.visibilityState === "visible") { void refresh(); void refreshReports(); void refreshProvider(); } }, 15_000);
    return () => { clearInterval(timer); readSequence.current++; reportReadSequence.current++; };
  }, [session, refresh, refreshReports, refreshProvider]);

  const login = async event => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    const epoch = ++sequence.current;
    try {
      let value;
      try {
        try { value = await api("/api/operator/login", { method: "POST", body: { username, password } }); }
        catch (reason) {
          // Authentication rejects stale cookies before checking credentials
          // and clears them. Retry this exact login once, never bad passwords.
          if (reason.status !== 401 || reason.code !== "SESSION_EXPIRED") throw reason;
          value = await api("/api/operator/login", { method: "POST", body: { username, password } });
        }
      }
      catch (reason) {
        if (reason.status && reason.status < 500) throw reason;
        value = await api("/api/operator/session");
      }
      if (epoch === sequence.current) { setSession(value); setUsername(""); }
    } catch (reason) { if (epoch === sequence.current) setError(operatorMessage(reason)); }
    finally { if (epoch === sequence.current) { setPassword(""); setBusy(false); } }
  };

  const act = async (path, body, success, apply) => {
    if (busy || !session) return;
    const epoch = sequence.current;
    if (path === "/api/operator/moderation/provider") providerReadSequence.current++;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api(path, { method: "POST", body, csrfToken: session.csrfToken });
      if (epoch !== sequence.current) return;
      apply?.(result); setNotice(success);
      if (epoch === sequence.current) await refresh();
    } catch (reason) {
      if (epoch !== sequence.current) return;
      if (reason.status === 401) { setSession(null); setItems([]); setPairing(null); }
      setError(operatorMessage(reason));
    } finally { if (epoch === sequence.current) setBusy(false); }
  };

  const logout = () => act("/api/operator/logout", {}, "로그아웃했어요.", () => {
    sequence.current++; readSequence.current++; reportReadSequence.current++;
    setSession(null); setPairing(null); setItems([]); setMode(null); setBusy(false);
    setReports([]); setReportCursor(null); setReportError(""); setPendingResolutions({}); setResolutions({}); resolutionIntents.current.clear();
  });

  const resolveReport = async (event, reportId) => {
    event.preventDefault();
    if (busy || !session) return;
    const epoch = sequence.current, resolution = resolutions[reportId] || "INSUFFICIENT_EVIDENCE";
    const previous = resolutionIntents.current.get(reportId);
    if (previous && previous.resolution !== resolution) {
      setReportError("이전 처리 결과를 먼저 확인해 주세요. 결과를 확인하기 전에는 다른 처리로 바꿀 수 없어요.");
      return;
    }
    const intent = previous || { resolution, idempotencyKey: secureUuid() };
    resolutionIntents.current.set(reportId, intent);
    setBusy(true); setReportError(""); setNotice("");
    try {
      const result = await api(`/api/operator/reports/${reportId}/resolve`, { method: "POST", body: { resolution: intent.resolution },
        csrfToken: session.csrfToken, idempotencyKey: intent.idempotencyKey });
      if (epoch !== sequence.current) return;
      if (result.reportId !== reportId || result.status !== "RESOLVED") throw new Error("INVALID_REPORT_RESPONSE");
      resolutionIntents.current.delete(reportId);
      setPendingResolutions(values => ({ ...values, [reportId]: false }));
      setNotice("신고 처리 결과를 기록했어요.");
      await refreshReports();
    } catch (reason) {
      if (epoch !== sequence.current) return;
      if (reason.status >= 400 && reason.status < 500) {
        resolutionIntents.current.delete(reportId);
        setPendingResolutions(values => ({ ...values, [reportId]: false }));
        setReportError(["INVALID_STATE", "REPORT_ACTION_REQUIRED"].includes(reason.code) ? "실제 숨김·중단 상태와 맞지 않아요. 먼저 해당 작업을 완료한 뒤 기록해 주세요." : operatorMessage(reason));
      } else {
        setPendingResolutions(values => ({ ...values, [reportId]: true }));
        setReportError("처리 결과를 확인하지 못했어요. 같은 처리 기록을 다시 확인해 주세요.");
      }
      if (reason.status === 401) { setSession(null); setReports([]); setItems([]); setPairing(null); }
    } finally { if (epoch === sequence.current) setBusy(false); }
  };

  const hideById = event => {
    event.preventDefault();
    const id = hideTarget.trim();
    if (!MESSAGE_ID.test(id)) { setError("전광판 설정에서 선택한 말풍선 ID를 확인해 주세요."); return; }
    void act(`/api/operator/submissions/${id}/hide`, { reasonCode: "INAPPROPRIATE_CONTENT" },
      "해당 말풍선을 전광판에서 숨겼어요.", () => setHideTarget(""));
  };

  return <main className="operator-app">
    <header className="operator-header"><a href="/" className="operator-brand brand-word">moodwall<span>마음이 모이는 곳</span></a>
      <div className="operator-header-right"><a href="/display" target="_blank" rel="noreferrer">전광판 열기 ↗</a>
        {session && <button type="button" className="operator-link" disabled={busy} onClick={logout}>로그아웃</button>}</div></header>
    {loading ? <section className="operator-login" aria-busy="true"><p>운영자 화면을 확인하고 있어요…</p></section>
      : sessionCheckFailed ? <section className="operator-login"><p className="operator-eyebrow">무드월 운영</p>
        <h1>접속을 확인하지 못했어요</h1><p role="alert">{error}</p>
        <button className="operator-primary" type="button" onClick={() => setSessionCheck(value => value + 1)}>다시 확인</button>
      </section>
      : !session ? <section className="operator-login"><p className="operator-eyebrow">무드월 운영</p>
        <h1>안녕하세요,<br />오늘의 무드월을 열어볼까요?</h1><p>운영자 계정으로 로그인해 주세요.</p>
        <form onSubmit={login}>
          <label>아이디<input autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required minLength={3} maxLength={64} /></label>
          <label>비밀번호<input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required maxLength={128} /></label>
          <button className="operator-primary" disabled={busy}>{busy ? "확인 중…" : "로그인"}</button>
        </form>{error && <p className="operator-error" role="alert">{error}</p>}
      </section> : <>
        <section className="operator-heading"><div><p className="operator-eyebrow">오늘의 무드월</p><h1>마음이 잘 도착하도록.</h1>
          <p>{session.username} · {isAdmin ? "관리자" : "운영자"}</p></div>
          <span className={`operator-state ${mode === "MOODWALL" ? "is-on" : ""}`}>{mode === "MOODWALL" ? "무드월 재개됨" : mode === "PAUSED" ? "화면 중단됨" : "송출 상태는 전광판에서 확인"}</span></section>
        {error && <p className="operator-error" role="alert">{error}</p>}
        {notice && <p className="operator-notice" role="status">{notice}</p>}
        {provider?.enabled && <section className="operator-provider" aria-label="자동 검수 상태"><div><h2>{providerIsMock ? "모의 검수" : "자동 검수"}</h2><p>{providerIsMock ? "실제 API 호출·과금 없음 · " : ""}{providerConnecting ? "연결 확인 중" : providerModes[provider.mode] || "상태 확인 중"}</p>
          {provider?.profile === "gemini" && !provider.transportConfigured ? <p>서버에 Gemini API 키를 등록해 주세요.</p> : null}
          {providerFailures[provider.lastFailureCode] ? <p>{providerFailures[provider.lastFailureCode]}</p> : null}
          {provider.mode === "OPEN_CIRCUIT" || provider.mode === "HALF_OPEN" ? <p>복구 확인이 끝나면 새 글을 받을 수 있어요.</p> : null}</div>
          {isAdmin && <div className="operator-button-row"><button type="button" disabled={busy || provider.mode === "PAUSED"} onClick={() => act("/api/operator/moderation/provider", { paused: true }, "자동 검수를 중단했어요.", setProvider)}>검수 중단</button>
            <button type="button" disabled={busy || provider.mode !== "PAUSED"} onClick={() => act("/api/operator/moderation/provider", { paused: false }, "자동 검수의 복구 확인을 시작했어요.", setProvider)}>검수 재개 확인</button></div>}
        </section>}
        {isAdmin && <section className="operator-controls" aria-label="전광판 운영">
          <article className="operator-card"><span className="operator-card-icon" aria-hidden="true">↗</span><h2>전광판 연결</h2>
            <p>노트북의 전광판 화면에<br />연결 코드를 입력해 주세요.</p>
            <button type="button" disabled={busy} onClick={() => act("/api/operator/display/pairing-codes", { reasonCode: "MAINTENANCE" }, "연결 코드를 발급했어요. 5분 안에 입력해 주세요.", setPairing)}>연결 코드 발급</button>
            {pairing && <div className="operator-pairing"><label>전광판 연결 코드<input aria-label="전광판 연결 코드" readOnly value={pairing.code} onFocus={event => event.target.select()} /></label>
              <span>{localTime(pairing.expiresAt)}까지 유효 · 노트북에만 입력해 주세요</span></div>}</article>
          <article className="operator-card"><span className="operator-card-icon" aria-hidden="true">✉</span><h2>글 접수</h2>
            <p>전광판이 연결된 뒤<br />학생들의 마음을 받아요.</p>
            <div className="operator-button-row"><button type="button" disabled={busy} onClick={() => act("/api/operator/board/intake", { paused: false, reasonCode: "MAINTENANCE" }, "글 접수를 재개했어요.")}>접수 재개</button>
              <button type="button" disabled={busy} onClick={() => act("/api/operator/board/intake", { paused: true, reasonCode: "MAINTENANCE" }, "새 글 접수를 중단했어요.")}>접수 중단</button></div></article>
          <article className="operator-card"><span className="operator-card-icon" aria-hidden="true">☀</span><h2>화면 송출</h2>
            <p>무드월을 보여주거나<br />잠시 화면을 가려요.</p>
            <div className="operator-button-row"><button type="button" disabled={busy} onClick={() => act("/api/operator/board/mode", { mode: "MOODWALL", reasonCode: "MAINTENANCE" }, "무드월 화면을 재개했어요.", value => setMode(value.mode))}>무드월 재개</button>
              <button type="button" className="operator-danger" disabled={busy} onClick={() => act("/api/operator/board/mode", { mode: "PAUSED", reasonCode: "MAINTENANCE" }, "전광판 화면을 중단했어요.", value => setMode(value.mode))}>화면 중단</button></div></article>
        </section>}
        <section className="operator-hide-target" aria-label="보관 종료된 말풍선 숨기기">
          <div><h2>화면에 남은 말풍선 숨기기</h2><p>전광판 설정에서 글을 선택해 이 화면을 열면 ID가 채워져요. 투명해진 말풍선도 숨길 수 있어요.</p></div>
          <form onSubmit={hideById}><label htmlFor="hide-target">말풍선 ID</label>
            <div><input id="hide-target" value={hideTarget} onChange={event => setHideTarget(event.target.value)}
              placeholder="전광판에서 선택하거나 ID를 붙여 넣으세요" autoComplete="off" spellCheck="false" maxLength={36} />
              <button type="submit" disabled={busy || !MESSAGE_ID.test(hideTarget.trim())}>이 말풍선 숨기기</button></div></form>
        </section>
        <section className="operator-reports" aria-label="학생이 알려준 문제">
          <div className="operator-list-heading"><div><h2>학생이 알려준 문제</h2><p>신고만으로 글을 숨기지 않아요. 확인 후 숨김·송출 중단을 실행하고 처리 결과를 기록해 주세요.</p></div>
            <button type="button" disabled={busy} onClick={() => void refreshReports()}>신고 새로고침</button></div>
          {reportError && <p className="operator-error" role="alert">{reportError}</p>}
          {!reports.length ? <p className="operator-report-empty">접수된 신고가 없어요.</p> : <ul>{reports.map(report => <li key={report.reportId}>
            <div className="operator-report-summary"><span className="operator-item-meta">{localTime(report.receivedAt)} · {report.status === "RESOLVED" ? "처리 완료" : "확인 필요"}</span><h3>{reportCategories[report.category]}</h3>
              <p>{report.submissionId ? report.targetAvailable ? "해당 글을 확인할 수 있어요." : "보관 기간이 지나 원문을 확인할 수 없어요. 화면에 남은 말풍선은 ID로 숨길 수 있어요." : "신고 시각의 전광판을 확인해 주세요. 특정 글이 확인되지 않은 신고예요."}</p>
              {report.submissionId && <code>{report.submissionId}</code>}
              {report.status === "RECEIVED" && report.submissionId && <button type="button" disabled={busy} onClick={() => act(`/api/operator/submissions/${report.submissionId}/hide`, { reasonCode: "INAPPROPRIATE_CONTENT" }, "신고된 글을 숨겼어요. 처리 결과를 기록해 주세요.")}>신고된 글 숨기기</button>}
            </div>
            {report.status === "RESOLVED" ? <p className="operator-item-meta">{reportResolutions[report.resolution] || "처리 완료"} · {localTime(report.resolvedAt)}</p> : <form className="operator-report-resolution" onSubmit={event => void resolveReport(event, report.reportId)}>
              <label htmlFor={`resolution-${report.reportId}`}>처리 결과</label>
              <select id={`resolution-${report.reportId}`} value={resolutions[report.reportId] || "INSUFFICIENT_EVIDENCE"} disabled={busy || pendingResolutions[report.reportId]} onChange={event => setResolutions(values => ({ ...values, [report.reportId]: event.target.value }))}>
                {Object.entries(reportResolutions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <button type="submit" disabled={busy}>{pendingResolutions[report.reportId] ? "같은 처리 기록 다시 확인" : "처리 기록"}</button>
            </form>}
          </li>)}</ul>}
          {reportCursor && <button type="button" className="operator-more" disabled={busy} onClick={() => void refreshReports(reportCursor)}>이전 신고 더 보기</button>}
        </section>
        <section className="operator-messages"><div className="operator-list-heading"><div><h2>최근 도착한 마음</h2><p>원문 보관 중인 오늘의 글이에요.</p></div>
          <button type="button" onClick={() => { setError(""); void refresh(); }} disabled={busy}>새로고침</button></div>
          {!items.length ? <div className="operator-empty"><span aria-hidden="true">✦</span><p>아직 보관 중인 글이 없어요.</p></div>
            : <ul>{items.map(({ submission: item }) => <li key={item.id}>
              <div><span className="operator-item-meta">{localTime(item.acceptedAt)} · {emotionNames[item.emotion] || statusNames[item.moderationStatus] || "준비 중"}</span>
                <p>{item.content}</p></div>
              {item.moderationStatus === "APPROVED" && ["QUEUED", "FLOATING"].includes(item.displayStatus)
                ? <button type="button" disabled={busy} aria-label={`${item.content} 숨기기`} onClick={() => act(`/api/operator/submissions/${item.id}/hide`, { reasonCode: "INAPPROPRIATE_CONTENT" }, "해당 글을 전광판에서 숨겼어요.")}>숨기기</button>
                : <span className="operator-item-meta">{statusNames[item.moderationStatus] || "보관 중"}</span>}
            </li>)}</ul>}
          {cursor && <button className="operator-more" type="button" disabled={busy} onClick={() => void refresh(cursor)}>이전 글 더 보기</button>}
        </section>
      </>}
    <footer className="operator-footer">서로의 마음이 편히 머무는 곳, 무드월.</footer>
  </main>;
}
