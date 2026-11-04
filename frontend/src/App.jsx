import { Component, Suspense, lazy } from "react";

const StudentApp = lazy(() => import("./student/StudentApp.jsx"));
const DisplayApp = lazy(() => import("./display/DisplayApp.jsx"));
const OperatorApp = lazy(() => import("./operator/OperatorApp.jsx"));

class ScreenBoundary extends Component {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <main className="screen-message"><p className="brand-word">moodwall</p>
      <h1>화면을 다시 불러와 주세요</h1><p>연결을 확인한 뒤 새로고침해 주세요.</p>
      <button type="button" onClick={() => window.location.reload()}>다시 불러오기</button></main>;
    return this.props.children;
  }
}

export default function App() {
  const path = window.location.pathname.replace(/\/$/, "") || "/";
  const screen = ["/", "/join", "/student"].includes(path) ? <StudentApp />
    : path === "/display" ? <DisplayApp /> : path === "/operator" ? <OperatorApp />
    : <main className="screen-message"><h1>이 페이지는 찾을 수 없어요</h1><a href="/">무드월로 돌아가기</a></main>;
  return <ScreenBoundary><Suspense fallback={<main className="screen-message" aria-busy="true">
    <p className="brand-word">moodwall</p><p>마음을 모으는 중이에요…</p>
  </main>}>{screen}</Suspense></ScreenBoundary>;
}
