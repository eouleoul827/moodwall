import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OperatorApp from "./OperatorApp.jsx";

const signedIn = { username: "test-admin", role: "ADMIN", csrfToken: "test-only-csrf" };
const rejected = status => Object.assign(new Error("synthetic"), { status, code: status === 401 ? "SESSION_EXPIRED" : "REQUEST_FAILED" });
afterEach(() => window.history.replaceState(null, "", "/"));

describe("operator API connection", () => {
  it.each([
    [{ mode: "OPEN", transportConfigured: true, workerReady: false }, "연결 확인 중"],
    [{ mode: "PAUSED", transportConfigured: true, workerReady: false, lastFailureCode: "PROVIDER_AUTH_ERROR" }, "API 키와 프로젝트 권한을 확인해 주세요. 설정을 고친 뒤 검수 재개 확인을 눌러 주세요."],
    [{ mode: "OPEN", transportConfigured: false, workerReady: false, lastFailureCode: "MODEL_NOT_READY" }, "서버에 Gemini API 키를 등록해 주세요."],
  ])("shows real provider readiness and a fixed safe failure message: %j", async (state, message) => {
    const api = vi.fn(async path => {
      if (path === "/api/operator/session") return signedIn;
      if (path === "/api/operator/moderation/provider") return {
        enabled: true, profile: "gemini", syntheticAccounting: false, ...state,
        error: "must-not-render-provider-body-or-secret",
      };
      return { items: [], nextCursor: null };
    });
    render(<OperatorApp api={api} />);
    await screen.findByText(message);
    expect(screen.queryByText(/실제 API 호출·과금 없음/)).toBeNull();
    expect(screen.queryByText("검수 가능")).toBeNull();
    expect(screen.queryByText("must-not-render-provider-body-or-secret")).toBeNull();
  });

  it("shows mock moderation honestly and resumes into recovery rather than claiming healthy", async () => {
    const provider = { enabled: true, profile: "gemini_mock", transportConfigured: false, syntheticAccounting: true, mode: "PAUSED" };
    const api = vi.fn(async (path, options) => {
      if (path === "/api/operator/session") return signedIn;
      if (path === "/api/operator/moderation/provider") return { ...provider, mode: options?.method === "POST" ? "OPEN_CIRCUIT" : "PAUSED" };
      return { items: [], nextCursor: null };
    });
    render(<OperatorApp api={api} />);
    await screen.findByRole("heading", { name: "모의 검수" }, { timeout: 6000 });
    expect(screen.getByText(/실제 API 호출·과금 없음/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "검수 재개 확인" }));
    await screen.findByText(/실제 API 호출·과금 없음 · 복구 확인 중/);
    expect(api).toHaveBeenCalledWith("/api/operator/moderation/provider", { method: "POST", body: { paused: false }, csrfToken: signedIn.csrfToken });
    expect(screen.queryByText(/실제 API 호출·과금 없음 · 검수 가능/)).toBeNull();
  });

  it("logs in without persisting a password and recovers to the current session", async () => {
    let loggedIn = false;
    const api = vi.fn(async (path, options) => {
      if (path === "/api/operator/session") { if (loggedIn) return signedIn; throw rejected(401); }
      if (path === "/api/operator/login") {
        expect(options.body).toEqual({ username: "test-admin", password: "test-only-passphrase" });
        loggedIn = true; throw rejected(503); // Cookie set but login reply lost.
      }
      return { items: [], nextCursor: null };
    });
    render(<OperatorApp api={api} />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("아이디"), "test-admin");
    await user.type(screen.getByLabelText("비밀번호"), "test-only-passphrase");
    await user.click(screen.getByRole("button", { name: "로그인" }));
    expect(await screen.findByRole("heading", { name: "마음이 잘 도착하도록." })).toBeTruthy();
    expect(screen.queryByLabelText("비밀번호")).toBeNull();
  });

  it("sends real operator control paths with the current CSRF and no optimistic success", async () => {
    let resolveMode;
    const api = vi.fn(async (path) => {
      if (path === "/api/operator/session") return signedIn;
      if (path === "/api/operator/board/mode") return new Promise(resolve => { resolveMode = resolve; });
      return { items: [], nextCursor: null };
    });
    render(<OperatorApp api={api} />);
    await userEvent.click(await screen.findByRole("button", { name: "화면 중단" }));
    expect(api).toHaveBeenCalledWith("/api/operator/board/mode", {
      method: "POST", body: { mode: "PAUSED", reasonCode: "MAINTENANCE" }, csrfToken: signedIn.csrfToken,
    });
    expect(screen.queryByText("화면 중단됨")).toBeNull();
    resolveMode({ mode: "PAUSED" });
    expect(await screen.findByText("화면 중단됨")).toBeTruthy();
  });

  it("logout clears operator data without a subsequent unauthorized refresh", async () => {
    const api = vi.fn(async path => path === "/api/operator/session" ? signedIn : { items: [], nextCursor: null });
    render(<OperatorApp api={api} />);
    await screen.findByRole("button", { name: "로그아웃" });
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/operator/submissions?limit=30"));
    const initial = api.mock.calls.filter(([path]) => path.startsWith("/api/operator/submissions")).length;
    await userEvent.click(screen.getByRole("button", { name: "로그아웃" }));
    await screen.findByLabelText("비밀번호");
    expect(api.mock.calls.filter(([path]) => path.startsWith("/api/operator/submissions")).length).toBe(initial);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("verifies the existing cookie after a transient session read before showing login", async () => {
    let reads = 0;
    const api = vi.fn(async path => {
      if (path === "/api/operator/session") {
        if (++reads === 1) throw rejected(503);
        return signedIn;
      }
      return { items: [], nextCursor: null };
    });
    render(<OperatorApp api={api} />);
    await screen.findByRole("heading", { name: "접속을 확인하지 못했어요" });
    expect(screen.queryByLabelText("비밀번호")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "다시 확인" }));
    await screen.findByRole("heading", { name: "마음이 잘 도착하도록." });
    expect(api.mock.calls.some(([path]) => path === "/api/operator/login")).toBe(false);
  });

  it("retries the same login once after the backend clears an expired cookie", async () => {
    let logins = 0;
    const api = vi.fn(async path => {
      if (path === "/api/operator/session") throw rejected(401);
      if (path === "/api/operator/login") {
        if (++logins === 1) throw rejected(401);
        return signedIn;
      }
      return { items: [], nextCursor: null };
    });
    render(<OperatorApp api={api} />);
    fireEvent.change(await screen.findByLabelText("아이디"), { target: { value: "test-admin" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "test-only-passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: "로그인" }));
    await screen.findByRole("heading", { name: "마음이 잘 도착하도록." });
    const requests = api.mock.calls.filter(([path]) => path === "/api/operator/login");
    expect(requests).toHaveLength(2);
    expect(requests[0][1].body).toEqual(requests[1][1].body);
  });

  it("does not automatically retry invalid credentials", async () => {
    const api = vi.fn(async path => {
      if (path === "/api/operator/session") throw rejected(401);
      throw Object.assign(new Error("synthetic"), { status: 401, code: "INVALID_CREDENTIALS" });
    });
    render(<OperatorApp api={api} />);
    fireEvent.change(await screen.findByLabelText("아이디"), { target: { value: "test-admin" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "incorrect-test-passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: "로그인" }));
    await screen.findByRole("alert");
    expect(api.mock.calls.filter(([path]) => path === "/api/operator/login")).toHaveLength(1);
    expect(screen.getByLabelText("비밀번호").value).toBe("");
  });
});
