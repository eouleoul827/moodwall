import { useEffect, useState } from 'react';

const names = {
  CONNECTION_FAILED: '서버 연결', AUTH_REQUIRED: '전광판 재등록', QR_UNAVAILABLE: '참여 QR',
  PLAYBACK_OWNED: '중복 송출 창', PLAYBACK_INTERRUPTED: '재생 중단', PLAYBACK_UNKNOWN: '재생 결과 확인',
  ASSET_FAILURE: '배경·글꼴 준비', STORAGE_UNAVAILABLE: '말풍선 보관', RENDER_FAILURE: '화면 렌더링',
};

export default function DisplayDiagnostics({ api }) {
  const [items, setItems] = useState([]), [received, setReceived] = useState(null), [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true, busy = false;
    const refresh = async () => {
      if (!active || busy) return; busy = true;
      try {
        const value = await api('/api/operator/display/diagnostics');
        if (active && Array.isArray(value?.items)) {
          setItems(value.items.filter(item => Object.hasOwn(names, item.code) && ['FAILED', 'RECOVERED'].includes(item.state)).slice(0, 45));
          setReceived(value.lastReceivedAt); setFailed(false);
        }
      } catch { if (active) setFailed(true); }
      finally { busy = false; }
    };
    void refresh(); const timer = setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 15_000);
    return () => { active = false; clearInterval(timer); };
  }, [api]);
  return <section className="operator-provider" aria-label="전광판 진단">
    <h2>전광판 진단</h2>
    <p>{failed ? '진단 보고를 확인하지 못했어요.' : received ? `최근 보고 ${new Date(received).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })}` : '아직 받은 진단 보고가 없어요.'}</p>
    <p>이 보고만으로 실제 전광판 전원이나 화면 출력을 확인할 수는 없어요.</p>
    {items.length > 0 && <ul>{items.map(item => <li key={`${item.component}:${item.code}`}>{names[item.code]} · {item.state === 'RECOVERED' ? '복구 확인' : '직원 확인 필요'}</li>)}</ul>}
  </section>;
}
