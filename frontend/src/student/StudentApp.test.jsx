import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StudentApp } from './StudentApp.jsx';
import { characterCount, validateMention } from './graphemes.js';

const usage = { remainingSlots: 3, limit: 3, pendingCount: 0, suspendedUntil: null, nextSubmissionAt: null };
const session = () => ({ csrfToken: 'test-csrf', serverNow: new Date().toISOString(),
  admissionValid: true, admissionExpiresAt: new Date(Date.now() + 600000).toISOString(), usage });
const accepted = (status = 'WAITING') => ({ state: 'ACCEPTED', receipt: { userStatus: status }, view: {
  card: status === 'NEEDS_REWRITE' ? 'REWRITE' : 'COMPLETE',
  title: status === 'NEEDS_REWRITE' ? '표현을 조금 바꿔 주세요' : '메시지를 보냈어요!',
  message: status === 'NEEDS_REWRITE' ? '표현을 바꿔 다시 보내 주세요.' : '확인 후 전광판에 순서대로 올라가요. 표시되면 15초 동안 크게 보여요.',
} });

function setup({ sendResult = accepted(), connectedSession = session(), reload, recoveryResult, recoveryPersistent } = {}) {
  const api = { connect: vi.fn().mockResolvedValue(connectedSession), session: vi.fn().mockResolvedValue(connectedSession),
    usage: vi.fn().mockResolvedValue(usage), request: vi.fn().mockResolvedValue({ reportId: '22222222-2222-4222-8222-222222222222', status: 'RECEIVED' }) };
  const client = { begin: vi.fn(), send: vi.fn().mockResolvedValue(sendResult), recover: vi.fn().mockResolvedValue({ state: 'UNKNOWN' }),
    getReceipt: vi.fn().mockResolvedValue(accepted()), watchReceipt: vi.fn().mockResolvedValue({}), stopWatching: vi.fn(), resetAuthentication: vi.fn(),
    ...(recoveryResult !== undefined ? { hasPendingRecovery: true, canResend: false, restorePending: vi.fn().mockResolvedValue(recoveryResult) } : {}),
    ...(recoveryPersistent === undefined ? {} : { recoveryPersistent }) };
  render(<StudentApp api={api} createSubmissionClient={() => client} initialTicket="test-ticket" reload={reload} />);
  return { api, client };
}

async function submit(content = '오늘도 좋은 하루!') {
  const input = await screen.findByRole('textbox', { name: '멘션 입력' });
  fireEvent.change(input, { target: { value: content } });
  fireEvent.click(screen.getByRole('button', { name: /전광판에 올리기/ }));
}

describe('student composition and receipts', () => {
  it('connects the QR entry, submits text only, and keeps a single normal completion card', async () => {
    const { api, client } = setup();
    expect(await screen.findByRole('heading', { name: '지금 기분이 어때요?' })).toBeTruthy();
    await submit();
    expect(await screen.findByRole('heading', { name: '메시지를 보냈어요!' })).toBeTruthy();
    expect(api.connect).toHaveBeenCalledWith('test-ticket', expect.any(AbortSignal));
    expect(client.begin).toHaveBeenCalledExactlyOnceWith('오늘도 좋은 하루!');
    expect(screen.queryByText('배경 이모지 고르기')).toBeNull();
    expect(screen.getByText(/표시되면 15초 동안 크게 보여요/)).toBeTruthy();
    expect(screen.queryByText(/전광판에 곧 올라가요|약 1분|자정까지/)).toBeNull();
    const watched = client.watchReceipt.mock.calls[0][0];
    act(() => watched.onChange(accepted('SHOWING')));
    expect(screen.getByRole('heading', { name: '메시지를 보냈어요!' })).toBeTruthy();
    expect(screen.queryByText('SHOWING')).toBeNull();
  });

  it('keeps an unknown intent immutable and recovers before allowing a fresh post', async () => {
    const { client } = setup({ sendResult: { state: 'UNKNOWN' } });
    await submit('답장을 기다려요');
    const recoverButton = await screen.findByRole('button', { name: '다시 확인' });
    expect(screen.getByRole('textbox', { name: '멘션 입력' }).readOnly).toBe(true);
    expect(screen.queryByRole('heading', { name: '메시지를 보냈어요!' })).toBeNull();
    client.recover.mockResolvedValueOnce(accepted());
    fireEvent.click(recoverButton);
    expect(await screen.findByRole('heading', { name: '메시지를 보냈어요!' })).toBeTruthy();
    expect(client.begin).toHaveBeenCalledTimes(1);
    expect(client.send).toHaveBeenCalledTimes(1);
    expect(client.recover).toHaveBeenCalledTimes(2);
  });

  it('resends the same intent explicitly without creating another key', async () => {
    const { client } = setup({ sendResult: { state: 'UNKNOWN' } });
    await submit();
    await screen.findByRole('button', { name: '다시 확인' });
    client.send.mockResolvedValueOnce(accepted());
    fireEvent.click(screen.getByRole('button', { name: '같은 메시지 다시 보내기' }));
    await screen.findByRole('heading', { name: '메시지를 보냈어요!' });
    expect(client.begin).toHaveBeenCalledTimes(1);
    expect(client.send).toHaveBeenCalledTimes(2);
  });

  it('allows an emoji grapheme and blocks 31 visible characters', async () => {
    setup();
    const input = await screen.findByRole('textbox', { name: '멘션 입력' });
    expect(input.getAttribute('maxlength')).toBeNull();
    fireEvent.change(input, { target: { value: '가'.repeat(31) } });
    expect(screen.getByRole('button', { name: /전광판에 올리기/ }).disabled).toBe(true);
    fireEvent.change(input, { target: { value: '👨‍👩‍👧‍👦'.repeat(30) } });
    expect(screen.getByRole('button', { name: /전광판에 올리기/ }).disabled).toBe(false);
    expect(characterCount('가👨‍👩‍👧‍👦')).toBe(2);
    expect(validateMention('두 줄\n입력')).toMatch(/한 줄/);
  });

  it('requires QR admission and uses the server quota rather than a local counter', async () => {
    setup({ connectedSession: { ...session(), admissionValid: false, usage: { ...usage, remainingSlots: 1 } } });
    const input = await screen.findByRole('textbox', { name: '멘션 입력' });
    fireEvent.change(input, { target: { value: '기분 좋아' } });
    expect(screen.getByRole('button', { name: /전광판에 올리기/ }).disabled).toBe(true);
    expect(screen.getByText('전광판의 새 QR을 찍으면 참여할 수 있어요.')).toBeTruthy();
    expect(screen.queryByText('1/3')).toBeNull();
  });

  it('retains the server rolling quota and explains when writing is available again', async () => {
    setup({ connectedSession: { ...session(), usage: { ...usage, remainingSlots: 0,
      nextSlotAt: new Date(Date.now() + 300000).toISOString() } } });
    const input = await screen.findByRole('textbox', { name: '멘션 입력' });
    fireEvent.change(input, { target: { value: '잠깐 쉬었다 올게' } });
    expect(screen.getByRole('button', { name: /전광판에 올리기/ }).disabled).toBe(true);
    expect(screen.getByRole('status').textContent).toMatch(/부터 다시 쓸 수 있어요/);
    expect(screen.queryByText(/오늘은 다 썼어요|자정에 다시|3번 반복|5번/)).toBeNull();
  });

  it('lets the server decide content policy without adding local warning or ban counters', async () => {
    const { client } = setup({ sendResult: accepted('NEEDS_REWRITE') });
    await submit('시발 오늘 진짜 힘들다');
    expect(await screen.findByRole('heading', { name: '표현을 조금 바꿔 주세요' })).toBeTruthy();
    expect(client.begin).toHaveBeenCalledExactlyOnceWith('시발 오늘 진짜 힘들다');
    expect(screen.queryByText(/3번|5번|경고|시도 \d/)).toBeNull();
    expect(screen.queryByText(/테스트용 기록 초기화|오늘 내가 띄운 구름/)).toBeNull();
  });

  it('keeps an uncertain moderation result neutral without a warning or publication promise', async () => {
    const { studentReceiptView } = await import('@clients/student/submission-client.mjs');
    const receipt = { userStatus: 'NEEDS_REWRITE', reasonCode: 'CONTENT_REVIEW_UNCERTAIN', contentStatus: 'PURGED' };
    setup({ sendResult: { state: 'ACCEPTED', receipt, view: studentReceiptView(receipt) } });
    await submit('조금 복잡한 마음');
    expect(await screen.findByRole('heading', { name: '공개 여부를 판단하기 어려웠어요' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '다시 작성하기' })).toBeTruthy();
    expect(document.querySelector('.student-result-mark.is-neutral')).toBeTruthy();
    expect(document.querySelector('.student-result-mark.is-notice')).toBeNull();
    expect(screen.queryByText(/경고|정지|잘못|곧 올라가요/)).toBeNull();
  });

  it('keeps a new draft when the prior receipt needs rewriting', async () => {
    const { client } = setup();
    await submit();
    fireEvent.click(await screen.findByRole('button', { name: '하나 더 쓰기' }));
    fireEvent.change(screen.getByRole('textbox', { name: '멘션 입력' }), { target: { value: '새로 쓰는 기분' } });
    act(() => client.watchReceipt.mock.calls[0][0].onChange(accepted('NEEDS_REWRITE')));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/이전 메시지/));
    expect(screen.getByRole('textbox', { name: '멘션 입력' }).value).toBe('새로 쓰는 기분');
    expect(screen.queryByRole('heading', { name: '표현을 조금 바꿔 주세요' })).toBeNull();
  });

  it('restores an active receipt after reload without recreating a post or its body', async () => {
    const activeId = '1a5bf09e-52ac-4ab6-a97d-0de2bc47494c';
    const { client } = setup({ connectedSession: { ...session(), usage: { ...usage, activeSubmissionId: activeId } } });
    expect(await screen.findByRole('heading', { name: '메시지를 보냈어요!' })).toBeTruthy();
    expect(client.getReceipt).toHaveBeenCalledWith(activeId, { signal: expect.any(AbortSignal) });
    expect(client.begin).not.toHaveBeenCalled();
    expect(client.send).not.toHaveBeenCalled();
    expect(screen.queryByText('여기에 내 멘션이 보여요')).toBeNull();
  });

  it('refreshes admission on focus without replacing the draft', async () => {
    const { api } = setup({ connectedSession: { ...session(), admissionValid: false } });
    const input = await screen.findByRole('textbox', { name: '멘션 입력' });
    fireEvent.change(input, { target: { value: '남겨 둔 초안' } });
    api.session.mockResolvedValueOnce(session());
    fireEvent.focus(window);
    await waitFor(() => expect(screen.getByRole('button', { name: /전광판에 올리기/ }).disabled).toBe(false));
    expect(input.value).toBe('남겨 둔 초안');
  });

  it('shows a private support card without repeating the crisis text or asking for a rewrite', async () => {
    const { studentReceiptView } = await import('@clients/student/submission-client.mjs');
    const receipt = { userStatus: 'NEEDS_REWRITE', reasonCode: 'SUPPORT_AVAILABLE', contentStatus: 'PURGED' };
    setup({ sendResult: { state: 'ACCEPTED', receipt, view: studentReceiptView(receipt) } });
    await submit('너무 힘들어 도와줘');
    await screen.findByRole('heading', { name: '혼자 감당하지 않아도 괜찮아요' });
    expect(screen.queryByText('너무 힘들어 도와줘')).toBeNull();
    expect(screen.queryByRole('button', { name: '수정하기' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '도움받는 방법 보기' }));
    expect(screen.getByRole('region', { name: '도움받는 방법' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '작성 화면으로' }));
    expect(screen.getByRole('textbox', { name: '멘션 입력' }).value).toBe('');
  });

  it('keeps help accessible during technical uncertainty', async () => {
    setup({ sendResult: { state: 'UNKNOWN' } });
    await submit();
    await screen.findByRole('button', { name: '다시 확인' });
    fireEvent.click(screen.getByRole('button', { name: '도움이 필요해요' }));
    expect(screen.getByRole('heading', { name: '도움이 필요할 때' })).toBeTruthy();
    expect(screen.getByRole('textbox', { name: '멘션 입력' }).readOnly).toBe(true);
  });

  it('requires an explicit refresh after a client upgrade, preserving the draft without retry', async () => {
    const reload = vi.fn();
    const { client } = setup({ sendResult: { state: 'UPDATE_REQUIRED', code: 'CLIENT_UPDATE_REQUIRED' }, reload });
    await submit('남겨 둔 초안');
    const refresh = await screen.findByRole('button', { name: '새로고침하기' });
    expect(client.recover).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox', { name: '멘션 입력' }).value).toBe('남겨 둔 초안');
    expect(screen.getByRole('button', { name: /전광판에 올리기/ }).disabled).toBe(true);
    expect(reload).not.toHaveBeenCalled();
    fireEvent.click(refresh);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(client.send).toHaveBeenCalledTimes(1);
  });

  it('reports only a fixed category and preserves the key after a lost response', async () => {
    const { api } = setup();
    api.request.mockRejectedValueOnce(Object.assign(Error('synthetic'), { code: 'NETWORK_ERROR' }));
    await submit('비공개 원문');
    fireEvent.click(await screen.findByRole('button', { name: '문제 있는 글 알리기' }));
    fireEvent.change(screen.getByLabelText('어떤 문제인가요?'), { target: { value: 'PRIVACY' } });
    fireEvent.click(screen.getByRole('button', { name: '알리기' }));
    const retry = await screen.findByRole('button', { name: '같은 신고 다시 보내기' });
    expect(screen.getByLabelText('어떤 문제인가요?').disabled).toBe(true);
    fireEvent.click(retry);
    await screen.findByText('알려줘서 고마워요. 확인할 수 있도록 접수했어요.');
    expect(api.request).toHaveBeenCalledTimes(2);
    const first = api.request.mock.calls[0], second = api.request.mock.calls[1];
    expect(first[0]).toBe('/api/v2/reports');
    expect(first[1].body).toEqual({ category: 'PRIVACY' });
    expect(first[1].csrfToken).toBe('test-csrf');
    expect(second).toEqual(first);
    expect(JSON.stringify(first)).not.toContain('비공개 원문');
  });

  it('restores a saved READY receipt even when usage has no active pending submission', async () => {
    const { client } = setup({ recoveryResult: accepted('WAITING'), connectedSession: { ...session(), usage: { ...usage, activeSubmissionId: null } } });
    expect(await screen.findByRole('heading', { name: '메시지를 보냈어요!' })).toBeTruthy();
    expect(client.restorePending).toHaveBeenCalledWith({ signal: expect.any(AbortSignal) });
    expect(client.getReceipt).not.toHaveBeenCalled();
    expect(client.begin).not.toHaveBeenCalled();
    expect(client.send).not.toHaveBeenCalled();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('falls back to the server active receipt after an expired local recovery entry was cleared', async () => {
    const activeId = '1a5bf09e-52ac-4ab6-a97d-0de2bc47494c';
    const { client } = setup({ recoveryResult: null, connectedSession: { ...session(), usage: { ...usage, activeSubmissionId: activeId } } });
    await screen.findByRole('heading', { name: '메시지를 보냈어요!' });
    expect(client.restorePending).toHaveBeenCalledTimes(1);
    expect(client.getReceipt).toHaveBeenCalledWith(activeId, { signal: expect.any(AbortSignal) });
    expect(client.send).not.toHaveBeenCalled();
  });

  it('keeps an unresolved reload read-only and offers lookup without resending a missing body', async () => {
    const { client } = setup({ recoveryResult: { state: 'UNKNOWN', code: 'RECEIPT_NOT_YET_FOUND' } });
    await screen.findByRole('button', { name: '다시 확인' });
    expect(screen.getByRole('textbox', { name: '멘션 입력' }).readOnly).toBe(true);
    expect(screen.getByRole('textbox', { name: '멘션 입력' }).value).toBe('');
    expect(screen.queryByRole('button', { name: '같은 메시지 다시 보내기' })).toBeNull();
    expect(screen.getByText(/원문은 저장하지 않아/)).toBeTruthy();
    expect(client.begin).not.toHaveBeenCalled();
    expect(client.send).not.toHaveBeenCalled();
  });

  it('explains unavailable recovery storage while preserving the current uncertain message', async () => {
    setup({ sendResult: { state: 'UNKNOWN' }, recoveryPersistent: false });
    await submit('화면에서 유지할 초안');
    await screen.findByRole('button', { name: '다시 확인' });
    expect(screen.getByText(/이 화면을 닫거나 새로고침하지 말고/)).toBeTruthy();
    expect(screen.getByRole('textbox', { name: '멘션 입력' }).value).toBe('화면에서 유지할 초안');
    expect(screen.getByRole('textbox', { name: '멘션 입력' }).readOnly).toBe(true);
  });

  it('rechecks the owner receipt on focus after polling stopped and retains a normal completion card', async () => {
    const { client } = setup();
    await submit();
    await screen.findByRole('heading', { name: '메시지를 보냈어요!' });
    client.receipt = { submissionId: '22222222-2222-4222-8222-222222222222' };
    client.getReceipt.mockResolvedValueOnce(accepted('NEEDS_REWRITE'));
    fireEvent.focus(window);
    expect(await screen.findByRole('heading', { name: '표현을 조금 바꿔 주세요' })).toBeTruthy();
    expect(client.getReceipt).toHaveBeenCalledTimes(1);
    expect(client.send).toHaveBeenCalledTimes(1);
  });

  it('focus recovery of the earlier receipt never replaces a new draft', async () => {
    const { client } = setup();
    await submit();
    fireEvent.click(await screen.findByRole('button', { name: '하나 더 쓰기' }));
    fireEvent.change(screen.getByRole('textbox', { name: '멘션 입력' }), { target: { value: '새 초안 보존' } });
    client.receipt = { submissionId: '22222222-2222-4222-8222-222222222222' };
    client.getReceipt.mockResolvedValueOnce(accepted('NEEDS_REWRITE'));
    fireEvent.focus(window);
    await waitFor(() => expect(client.getReceipt).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('textbox', { name: '멘션 입력' }).value).toBe('새 초안 보존');
    expect(screen.queryByRole('heading', { name: '표현을 조금 바꿔 주세요' })).toBeNull();
  });

  it('refreshes report CSRF once and retries the same report key and category', async () => {
    const { api } = setup();
    api.request.mockRejectedValueOnce(Object.assign(Error('stale'), { status: 403, code: 'CSRF_REJECTED' }));
    api.session.mockResolvedValueOnce({ ...session(), csrfToken: 'fresh-report-csrf' });
    await submit();
    fireEvent.click(await screen.findByRole('button', { name: '문제 있는 글 알리기' }));
    fireEvent.change(screen.getByLabelText('어떤 문제인가요?'), { target: { value: 'PRIVACY' } });
    fireEvent.click(screen.getByRole('button', { name: '알리기' }));
    await screen.findByText('알려줘서 고마워요. 확인할 수 있도록 접수했어요.');
    expect(api.session).toHaveBeenCalledTimes(1);
    expect(api.request).toHaveBeenCalledTimes(2);
    const first = api.request.mock.calls[0][1], second = api.request.mock.calls[1][1];
    expect(second.idempotencyKey).toBe(first.idempotencyKey);
    expect(second.body).toEqual(first.body);
    expect(first.csrfToken).toBe('test-csrf');
    expect(second.csrfToken).toBe('fresh-report-csrf');
  });

  it('bounds an unsuccessful report auth refresh and keeps its original intent on re-entry', async () => {
    const { api } = setup();
    api.request.mockRejectedValue(Object.assign(Error('stale'), { status: 403, code: 'CSRF_REJECTED' }));
    await submit();
    fireEvent.click(await screen.findByRole('button', { name: '문제 있는 글 알리기' }));
    fireEvent.click(screen.getByRole('button', { name: '알리기' }));
    await screen.findByText('접속을 확인하지 못했어요. 잠시 후 같은 신고로 다시 확인해 주세요.');
    expect(api.session).toHaveBeenCalledTimes(1);
    expect(api.request).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole('button', { name: '안내 닫기' }));
    fireEvent.click(screen.getByRole('button', { name: '문제 있는 글 알리기' }));
    expect(screen.getByLabelText('어떤 문제인가요?').disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '같은 신고 다시 보내기' }));
    await waitFor(() => expect(api.request).toHaveBeenCalledTimes(4));
    expect(new Set(api.request.mock.calls.map(([, options]) => options.idempotencyKey)).size).toBe(1);
  });

  it('requires QR re-entry when report authentication expired, and resumes the same intent on focus', async () => {
    const { api } = setup();
    api.request.mockRejectedValueOnce(Object.assign(Error('expired'), { status: 401, code: 'SESSION_EXPIRED' }));
    api.session.mockResolvedValueOnce(null);
    await submit();
    fireEvent.click(await screen.findByRole('button', { name: '문제 있는 글 알리기' }));
    fireEvent.click(screen.getByRole('button', { name: '알리기' }));
    await screen.findByText(/전광판에 있는 QR을 찍으면/);
    expect(api.request).toHaveBeenCalledTimes(1);
    api.session.mockResolvedValueOnce({ ...session(), csrfToken: 'renewed-csrf' });
    fireEvent.focus(window);
    await screen.findByRole('button', { name: '같은 신고 다시 보내기' });
    fireEvent.click(screen.getByRole('button', { name: '같은 신고 다시 보내기' }));
    await screen.findByText('알려줘서 고마워요. 확인할 수 있도록 접수했어요.');
    expect(api.request.mock.calls[1][1].idempotencyKey).toBe(api.request.mock.calls[0][1].idempotencyKey);
  });

  it('shows a fresh report form on re-entry only after the previous report was confirmed', async () => {
    const { api } = setup();
    await submit();
    fireEvent.click(await screen.findByRole('button', { name: '문제 있는 글 알리기' }));
    fireEvent.change(screen.getByLabelText('어떤 문제인가요?'), { target: { value: 'PRIVACY' } });
    fireEvent.click(screen.getByRole('button', { name: '알리기' }));
    await screen.findByText('알려줘서 고마워요. 확인할 수 있도록 접수했어요.');
    fireEvent.click(screen.getByRole('button', { name: '안내 닫기' }));
    fireEvent.click(screen.getByRole('button', { name: '문제 있는 글 알리기' }));
    expect(screen.getByLabelText('어떤 문제인가요?').value).toBe('HARASSMENT');
    expect(screen.getByLabelText('어떤 문제인가요?').disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '알리기' }));
    await screen.findByText('알려줘서 고마워요. 확인할 수 있도록 접수했어요.');
    expect(api.request).toHaveBeenCalledTimes(2);
    expect(api.request.mock.calls[1][1].idempotencyKey).not.toBe(api.request.mock.calls[0][1].idempotencyKey);
  });
});
