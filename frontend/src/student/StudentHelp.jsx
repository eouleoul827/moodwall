import { useEffect, useRef, useState } from 'react';
import { ReportClient } from '@clients/student/report-client.mjs';

const categories = { HARASSMENT: '괴롭힘·모욕', PRIVACY: '개인정보 노출', THREAT: '위협·위험한 내용', OTHER: '그 밖의 문제' };

export function StudentHelp({ open, onOpenChange, api, csrfToken, canReport, showReportEntry = false }) {
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
  const report = async event => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      if (!uncertain) client.begin(category);
      const result = await client.send();
      if (mounted.current && result.state !== 'STALE') setReportResult(result);
    } catch { if (mounted.current) setReportResult({ state: 'REJECTED' }); }
    finally { if (mounted.current) setBusy(false); }
  };

  return <div className="student-help-entry">
    <div className="student-help-links">
      <button type="button" className="student-text-button" aria-expanded={open} aria-controls="student-help" onClick={() => onOpenChange(!open)}>도움이 필요해요</button>
      {canReport && showReportEntry && <button type="button" className="student-text-button" onClick={() => { onOpenChange(true); setReportOpen(true); }}>문제 있는 글 알리기</button>}
    </div>
    {open && <section id="student-help" className="student-help" aria-label="도움받는 방법">
      <h2>도움이 필요할 때</h2>
      <p>지금 많이 힘들거나 안전이 걱정된다면 가까운 선생님, 보호자, 믿을 수 있는 어른에게 알려주세요. 직접 말하기 어렵다면 이 화면을 보여줘도 괜찮아요.</p>
      <p>이 안내를 보는 것만으로 글이 전광판에 올라가거나 누군가에게 연락되지는 않아요. 지금 도움이 필요하면 주변 어른에게 직접 도움을 요청해 주세요.</p>
      {canReport && <>
        {!reportOpen && <button type="button" className="student-text-button" onClick={() => setReportOpen(true)}>전광판에서 본 문제 있는 글 알리기</button>}
        {reportOpen && <div className="student-report">
          <h3>문제 있는 글 알리기</h3>
          <p>전광판에서 본 글을 알려주세요. 글 내용이나 이름을 다시 적을 필요는 없어요. 운영자가 확인할 수 있도록 접수하며, 접수만으로 글이 바로 가려지지는 않아요.</p>
          {reportResult?.state === 'ACCEPTED' ? <p role="status">알려줘서 고마워요. 확인할 수 있도록 접수했어요.</p> : <form onSubmit={report}>
            <label htmlFor="report-category">어떤 문제인가요?</label>
            <select id="report-category" value={category} disabled={busy || uncertain} onChange={event => setCategory(event.target.value)}>
              {Object.entries(categories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            {uncertain && <p role="status">접수 결과를 확인하지 못했어요. 다시 보내도 같은 신고로 확인해요.</p>}
            {reportResult?.state === 'REJECTED' && <p role="alert">지금은 접수할 수 없어요. 접속을 확인하고 잠시 후 다시 시도해 주세요.</p>}
            <button type="submit" className="student-button student-button-secondary" disabled={busy}>{busy ? '접수하는 중…' : uncertain ? '같은 신고 다시 보내기' : '알리기'}</button>
          </form>}
        </div>}
      </>}
      <button type="button" className="student-text-button" onClick={() => onOpenChange(false)}>안내 닫기</button>
    </section>}
  </div>;
}
