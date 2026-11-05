export class ApiError extends Error {
  constructor(code, status = 0) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

export async function request(path, { method = "GET", body, csrfToken, idempotencyKey, signal } = {}) {
  if (!path.startsWith("/api/") || path.startsWith("//")) throw new TypeError("A same-origin API path is required");
  const controller = new AbortController();
  const cancel = () => controller.abort(signal.reason);
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = setTimeout(() => controller.abort(new ApiError("REQUEST_TIMEOUT")), 6000);
  try {
    const headers = { Accept: "application/json" };
    if (method === "POST") {
      headers["Content-Type"] = "application/json";
      if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
      if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    }
    const response = await fetch(path, {
      method, headers, credentials: "same-origin", cache: "no-store", signal: controller.signal,
      ...(method === "POST" ? { body: JSON.stringify(body ?? {}) } : {}),
    });
    let data;
    try { data = await response.json(); }
    catch { throw new ApiError("INVALID_RESPONSE", response.status); }
    if (!response.ok) throw new ApiError(data.code || "REQUEST_FAILED", response.status);
    return data;
  } catch (error) {
    if (controller.signal.aborted) throw controller.signal.reason || new ApiError("REQUEST_TIMEOUT");
    if (error instanceof ApiError) throw error;
    throw new ApiError("NETWORK_ERROR");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}

export function operatorMessage(error) {
  if (error?.code === "INVALID_CREDENTIALS") return "아이디와 비밀번호를 다시 확인해 주세요.";
  if (error?.status === 401) return "로그인이 만료됐어요. 다시 로그인해 주세요.";
  if (error?.status === 403) return "이 작업에 필요한 권한이 없어요.";
  if (error?.status === 429) return "요청이 많아요. 잠시 후 다시 시도해 주세요.";
  if (error?.code === "INVALID_STATE") return "현재 숨길 수 없는 글이에요. 목록을 새로고침해 주세요.";
  return "연결을 확인하지 못했어요. 화면의 현재 상태를 확인한 뒤 다시 시도해 주세요.";
}
