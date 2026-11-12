/** Student credentials stay in HttpOnly cookies; CSRF and QR tickets stay in memory. */
export class StudentApiError extends Error {
  constructor(code, status = 0, retryAfter = null) {
    super(code);
    this.code = code;
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export function takeAdmissionTicket(location = window.location, history = window.history) {
  const fragment = new URLSearchParams(location.hash.replace(/^#/, ''));
  const ticket = fragment.get('ticket');
  if (location.hash) history.replaceState(null, '', `${location.pathname}${location.search}`);
  return ticket;
}

export function createStudentApi(fetcher = globalThis.fetch) {
  async function request(path, { method = 'GET', body, csrfToken = '', idempotencyKey, signal } = {}) {
    if (!path.startsWith('/api/') || path.startsWith('//')) throw new TypeError('A same-origin API path is required');
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    const timer = setTimeout(abort, 6000);
    try {
      const response = await fetcher(path, {
        method, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: {
          Accept: 'application/json',
          'X-Moodwall-Client-Features': 'support-v1',
          ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
          ...(method === 'POST' ? { 'Content-Type': 'application/json', ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}) } : {}),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      let data;
      try { data = await response.json(); } catch { throw new StudentApiError('SERVICE_UNAVAILABLE', response.status); }
      if (!response.ok) throw new StudentApiError(data.code || 'SERVICE_UNAVAILABLE', response.status, response.headers.get('Retry-After'));
      return data;
    } catch (error) {
      if (error instanceof StudentApiError || signal?.aborted) throw error;
      throw new StudentApiError('NETWORK_ERROR');
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
    }
  }

  async function session(signal) {
    try { return await request('/api/session', { signal }); }
    catch (error) { if (error.status === 401) return null; throw error; }
  }

  async function connect(ticket, signal) {
    let current = await session(signal);
    if (!ticket) return current;
    if (ticket.length > 2048) throw new StudentApiError('INVALID_QR');
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await request('/api/admissions/exchange', {
          method: 'POST', body: { ticket }, csrfToken: current?.csrfToken, signal,
        });
        // Confirm the browser retained the cookie before allowing a post.
        const confirmed = await session(signal);
        if (!confirmed) throw new StudentApiError('COOKIES_REQUIRED');
        return confirmed;
      } catch (error) {
        if (signal?.aborted) throw error;
        if (error.code === 'COOKIES_REQUIRED') throw error;
        if (error.status === 401 || error.code === 'CSRF_REJECTED' || !error.status || error.status >= 500) {
          current = await session(signal);
          // A lost exchange response may already have completed successfully.
          if (current?.admissionValid && (!error.status || error.status >= 500)) return current;
          if (attempt === 0) continue;
        }
        throw error;
      }
    }
    return current;
  }

  return { request, connect, session, usage: signal => request('/api/me/usage', { signal }) };
}

const messages = {
  CLIENT_UPDATE_REQUIRED: '화면이 업데이트됐어요. 새로고침한 뒤 다시 확인해 주세요.',
  INVALID_QR: '전광판의 새 QR을 다시 찍어 주세요.',
  QR_EXPIRED: '입장 시간이 지났어요. 전광판의 새 QR을 찍어 주세요.',
  ADMISSION_EXPIRED: '입장 시간이 지났어요. 전광판의 새 QR을 찍어 주세요.',
  SESSION_REQUIRED: '전광판의 QR을 찍으면 참여할 수 있어요.',
  SESSION_EXPIRED: '전광판의 QR을 다시 찍어 주세요.',
  CSRF_REJECTED: '접속을 다시 확인해 주세요.',
  COOKIES_REQUIRED: '브라우저에서 쿠키를 허용한 뒤 QR을 다시 찍어 주세요.',
  VALIDATION_ERROR: '줄바꿈 없이 1~30자로 작성해 주세요.',
  INVALID_CONTENT: '줄바꿈 없이 1~30자로 작성해 주세요.',
  CONTENT_TOO_LONG: '30자 이내로 작성해 주세요.',
  POST_LIMIT_REACHED: '최근 1시간의 작성 횟수를 모두 썼어요. 잠시 후 다시 보내 주세요.',
  HOURLY_LIMIT_REACHED: '최근 1시간의 작성 횟수를 모두 썼어요. 잠시 후 다시 보내 주세요.',
  QUOTA_EXCEEDED: '최근 1시간의 작성 횟수를 모두 썼어요. 잠시 후 다시 보내 주세요.',
  COOLDOWN_ACTIVE: '잠깐만 기다린 뒤 다시 보내 주세요.',
  SUBMISSION_COOLDOWN: '잠깐만 기다린 뒤 다시 보내 주세요.',
  SUBMISSION_IN_PROGRESS: '이전 메시지를 확인하고 있어요. 잠시 후 다시 보내 주세요.',
  PARTICIPANT_SUSPENDED: '지금은 작성할 수 없어요. 내일 다시 참여해 주세요.',
  PARTICIPANT_BANNED: '지금은 작성할 수 없어요. 내일 다시 참여해 주세요.',
  BANNED: '지금은 작성할 수 없어요. 내일 다시 참여해 주세요.',
  DISPLAY_QUEUE_FULL: '전광판에 메시지가 많이 모였어요. 잠시 후 다시 보내 주세요.',
  MODERATION_BUSY: '글이 많이 몰렸어요. 잠시 후 다시 보내 주세요.',
  MODEL_NOT_READY: '준비 중이에요. 잠시 후 다시 보내 주세요.',
  DAY_ENDING: '오늘 전광판이 마무리되고 있어요. 내일 다시 만나요.',
  DISPLAY_OFFLINE: '전광판을 잠시 연결하고 있어요. 조금 뒤 다시 보내 주세요.',
  INTAKE_PAUSED: '지금은 전광판이 쉬고 있어요. 잠시 후 다시 보내 주세요.',
  REQUEST_RATE_LIMITED: '잠깐만 기다린 뒤 다시 시도해 주세요.',
  RATE_LIMITED: '잠깐만 기다린 뒤 다시 시도해 주세요.',
  IDEMPOTENCY_CONFLICT: '전송 내용을 다시 확인해 주세요.',
  NETWORK_ERROR: '연결이 원활하지 않아요. 잠시 후 다시 시도해 주세요.',
};

export function studentErrorMessage(code) {
  return messages[code] || '잠시 연결을 준비하고 있어요. 조금 뒤 다시 시도해 주세요.';
}
