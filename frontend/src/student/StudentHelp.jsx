import { useEffect, useRef, useState } from 'react';
import { ReportClient } from '@clients/student/report-client.mjs';

const categories = { HARASSMENT: '괴롭힘·모욕', PRIVACY: '개인정보 노출', THREAT: '위협·위험한 내용', OTHER: '그 밖의 문제' };

export function StudentHelp({ api, csrfToken, refreshSession, canReport, showReportEntry = false }) {
  const [reportOpen, setReportOpen] = useState(false);
  const [category, setCategory] = useState('HARASSMENT');
  const [reportResult, setReportResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(false);
  const [client] = useState(() => new ReportClient({ request: (...args) => api.request(...args), csrfToken }));
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; client.reset(); };
  }, [client]);

  const uncertain = ['UNKNOWN', 'AUTH_REQUIRED'].includes(reportResult?.state);
  useEffect(() => {
    // Re-entry starts a new report only after the previous delivery was confirmed.
    if (!reportOpen && !busy && reportResult && !uncertain) {
      client.reset(); setReportResult(null); setCategory('HARASSMENT');
    }
  }, [reportOpen, busy, reportResult, uncertain, client]);
  const report = async event => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      if (!uncertain) client.begin(category);
      let result = await client.send();
      if (result.state === 'AUTH_REQUIRED') {
        try {
          const fresh = await (refreshSession ? refreshSession() : api.session());
          if (!mounted.current) return;
          result = fresh?.csrfToken ? await client.send({ csrfToken: fresh.csrfToken })
            : { state: 'AUTH_REQUIRED', code: fresh === null ? 'SESSION_EXPIRED' : 'AUTH_REFRESH_FAILED' };
        } catch { result = { state: 'AUTH_REQUIRED', code: 'AUTH_REFRESH_FAILED' }; }
      }
      if (mounted.current && result.state !== 'STALE') setReportResult(result);
    } catch { if (mounted.current) setReportResult({ state: 'UNKNOWN' }); }
    finally { if (mounted.current) setBusy(false); }
  };

  if (!(canReport && showReportEntry)) return null;

  return <div className="student-help-entry">
    <div className="student-help-links">
      <button type="button" className="student-text-button" onClick={() => setReportOpen(true)}>문제 있는 글 알리기</button>
    </div>
    {reportOpen && <section id="student-report" className="student-help" aria-label="문제 있는 글 알리기">
      <div className="student-report">
          <h2>문제 있는 글 알리기</h2>
          <p>전광판에서 본 글을 알려주세요. 글 내용이나 이름을 다시 적을 필요는 없어요. 운영자가 확인할 수 있도록 접수하며, 접수만으로 글이 바로 가려지지는 않아요.</p>
          {reportResult?.state === 'ACCEPTED' ? <p role="status">알려줘서 고마워요. 확인할 수 있도록 접수했어요.</p> : <form onSubmit={report}>
            <label htmlFor="report-category">어떤 문제인가요?</label>
            <select id="report-category" value={category} disabled={busy || uncertain} onChange={event => setCategory(event.target.value)}>
              {Object.entries(categories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            {uncertain && <p role="status">{reportResult.code === 'SESSION_EXPIRED'
              ? '접속 시간이 지났어요. 전광판의 QR로 다시 들어와 주세요. 확인 중인 신고는 그대로 유지해요.'
              : reportResult.state === 'AUTH_REQUIRED' ? '접속을 확인하지 못했어요. 잠시 후 같은 신고로 다시 확인해 주세요.'
                : '접수 결과를 확인하지 못했어요. 다시 보내도 같은 신고로 확인해요.'}</p>}
            {reportResult?.state === 'REJECTED' && <p role="alert">지금은 접수할 수 없어요. 접속을 확인하고 잠시 후 다시 시도해 주세요.</p>}
            <button type="submit" className="student-button student-button-secondary" disabled={busy}>{busy ? '접수하는 중…' : uncertain ? '같은 신고 다시 보내기' : '알리기'}</button>
          </form>}
      </div>
      <button type="button" className="student-text-button" onClick={() => setReportOpen(false)}>신고 닫기</button>
    </section>}
  </div>;
}
