import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DisplayDiagnostics from './DisplayDiagnostics.jsx';

describe('private operator diagnostics', () => {
  it('shows allowlisted failures and recovery while discarding arbitrary server strings', async () => {
    const api = vi.fn(async () => ({ lastReceivedAt: '2026-11-05T09:00:00Z', items: [
      { component: 'QR', code: 'QR_UNAVAILABLE', state: 'FAILED' },
      { component: 'RENDER', code: 'ASSET_FAILURE', state: 'RECOVERED' },
      { component: 'RENDER', code: '<private message>', state: 'FAILED' },
    ] }));
    render(<DisplayDiagnostics api={api} />);
    await screen.findByText('참여 QR · 직원 확인 필요'); expect(screen.getByText('배경·글꼴 준비 · 복구 확인')).toBeTruthy();
    expect(screen.queryByText('<private message>')).toBeNull();
  });
  it('keeps reporting failures inside the operator screen', async () => {
    const api = vi.fn(async () => { throw new Error('PRIVATE_FAILURE'); }); render(<DisplayDiagnostics api={api} />);
    await waitFor(() => expect(screen.getByText('진단 보고를 확인하지 못했어요.')).toBeTruthy()); expect(screen.queryByText('PRIVATE_FAILURE')).toBeNull();
  });
});
