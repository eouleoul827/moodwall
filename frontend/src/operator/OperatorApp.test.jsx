import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OperatorApp from "./OperatorApp.jsx";

const signedIn = { username: "test-admin", role: "ADMIN", csrfToken: "test-only-csrf" };
const rejected = status => Object.assign(new Error("synthetic"), { status, code: status === 401 ? "SESSION_EXPIRED" : "REQUEST_FAILED" });
afterEach(() => window.history.replaceState(null, "", "/"));

describe("operator API connection", () => {
  it("selects a retained bubble from the display without hiding until an authenticated click", async () => {
    const id = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001";
    window.history.replaceState(null, "", `/operator#hide=${id}`);
    const api = vi.fn(async path => path === "/api/operator/session" ? { ...signedIn, role: "MODERATOR" } : { items: [], nextCursor: null });
    render(<OperatorApp api={api} />);
    expect((await screen.findByLabelText("말풍선 ID")).value).toBe(id);
    expect(window.location.hash).toBe("");
    expect(api.mock.calls.some(([path]) => path.endsWith("/hide"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "이 말풍선 숨기기" }));
    expect(api).toHaveBeenCalledWith(`/api/operator/submissions/${id}/hide`, {
      method: "POST", body: { reasonCode: "INAPPROPRIATE_CONTENT" }, csrfToken: signedIn.csrfToken,
    });
    expect(await screen.findByText("해당 말풍선을 전광판에서 숨겼어요.")).toBeTruthy();
    expect(screen.getByLabelText("말풍선 ID").value).toBe("");
  });

  it("does not accept arbitrary paths as a retained bubble ID", async () => {
    window.history.replaceState(null, "", "/operator#hide=..%2F..%2Flogout");
    const api = vi.fn(async path => path === "/api/operator/session" ? signedIn : { items: [], nextCursor: null });
    render(<OperatorApp api={api} />);
    const target = await screen.findByLabelText("말풍선 ID");
    expect(target.value).toBe("");
    fireEvent.change(target, { target: { value: "../../logout" } });
    expect(screen.getByRole("button", { name: "이 말풍선 숨기기" }).disabled).toBe(true);
    fireEvent.submit(target.closest("form"));
    expect(api.mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
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

  it("moderators can hide an approved message without receiving admin controls", async () => {
    const api = vi.fn(async path => path === "/api/operator/session" ? { ...signedIn, role: "MODERATOR" } : {
      items: [{ submission: { id: "synthetic-id", content: "오늘 행복해", moderationStatus: "APPROVED", displayStatus: "QUEUED", acceptedAt: "2026-11-04T03:00:00Z" } }], nextCursor: null,
    });
    render(<OperatorApp api={api} />);
    await userEvent.click(await screen.findByRole("button", { name: "오늘 행복해 숨기기" }));
    expect(screen.queryByRole("button", { name: "접수 재개" })).toBeNull();
    expect(api).toHaveBeenCalledWith("/api/operator/submissions/synthetic-id/hide", {
      method: "POST", body: { reasonCode: "INAPPROPRIATE_CONTENT" }, csrfToken: signedIn.csrfToken,
    });
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
