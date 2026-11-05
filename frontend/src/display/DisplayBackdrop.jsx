import './display-backdrop.css';

/** Eager, dependency-free public fallback; no message, QR, network resource or technical status. */
export default function DisplayBackdrop() {
  return <main className="mw-backdrop" aria-label="무드월">
    <div className="mw-backdrop-orb mw-backdrop-orb-one" aria-hidden="true" />
    <div className="mw-backdrop-orb mw-backdrop-orb-two" aria-hidden="true" />
    <div className="mw-backdrop-brand">MOOD WALL<span>마음이 머무는 작은 벽</span></div>
    <svg className="mw-backdrop-mark" viewBox="0 0 100 100" aria-hidden="true"><path d="M50 83 17 53C-4 31 27 9 50 32 73 9 104 31 83 53Z" fill="currentColor" /></svg>
  </main>;
}
