import jjCheer from '../display/background/mascot/jj-3d-cheer.webp';
import './student-skin.css';

function Cloud({ className = '' }) {
  return <svg className={`student-skin-cloud ${className}`} viewBox="0 0 240 128" fill="none" aria-hidden="true">
    <path d="M46 113C22 113 9 100 9 82c0-17 12-31 29-34C43 25 60 12 81 12c18 0 33 9 42 24 8-6 17-9 27-9 23 0 42 17 46 38 20 0 35 10 35 24 0 15-13 24-33 24H46Z" fill="currentColor" />
    <path d="M39 96c12 7 28 9 45 9h109" stroke="white" strokeWidth="3" strokeLinecap="round" opacity=".3" />
  </svg>;
}

function SkyArt() {
  return <div className="student-skin-art" aria-hidden="true">
    <div className="student-skin-halo" />
    <Cloud className="student-skin-cloud-back" />
    <Cloud className="student-skin-cloud-front" />
    <span className="student-skin-star student-skin-star-one">✦</span>
    <span className="student-skin-star student-skin-star-two">✧</span>
    <span className="student-skin-star student-skin-star-three">✦</span>
    <span className="student-skin-dot student-skin-dot-one" />
    <span className="student-skin-dot student-skin-dot-two" />
    <img className="student-skin-mascot" src={jjCheer} width="143" height="168" alt="" decoding="async" />
    <Cloud className="student-skin-cloud-seat" />
  </div>;
}

// Decoration surrounds the existing flow without participating in admission,
// submission, recovery, or receipt state.
export default function StudentSkyFrame({ children }) {
  return <div className="student-skin"><SkyArt />{children}</div>;
}
