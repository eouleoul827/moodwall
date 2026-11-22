// Mood background generator for the display screen.
// Main character is always the 3D JJ render (one expression per mood); Byeoli and Jeit float small.
// Builds an SVG string; no DOM access.
import { hashString, mulberry32 } from "./seededRandom.js";

// 높이는 900으로 고정하고 너비는 화면 비율에 맞춘다 (전광판 2046×768 같은 와이드 화면 대응)
const H = 900, GROUND = 872;
let W = 1600;
let rnd = Math.random;
const R = (a, b) => a + rnd() * (b - a);
const RI = (a, b) => Math.floor(R(a, b + 1));
const pick = (a) => a[Math.floor(rnd() * a.length)];
const shuffle = (a) => a.map((v) => [rnd(), v]).sort((x, y) => x[0] - y[0]).map((v) => v[1]);
const f0 = (n) => n.toFixed(0);
let uid = 0;
let idBase = "mw";
let assetBase = "/mascot/";
const id = (p) => `${idBase}${p}${++uid}`;

// 가운데 말풍선 자리
let BUBBLE = { x0: 470, x1: 1130, y0: 250, y1: 640 };
const inBubble = (x, y, pad = 0) => x > BUBBLE.x0 - pad && x < BUBBLE.x1 + pad && y > BUBBLE.y0 - pad && y < BUBBLE.y1 + pad;

// ---------- 이미지 ----------
const EXPRESSION = {
  응원: "cheer", 축하: "celebration", 기쁨: "joy", 설렘: "excited",
  피곤: "tired", 슬픔: "sad", 화남: "angry", 일반: "neutral",
};
const ART = {
  jj: (mood) => [`jj-3d-${EXPRESSION[mood]}.webp`, 1020, 1200],
  byeoli: ["byeoli-3d.webp", 585, 600],
  jeit: ["jeit-3d.webp", 448, 600],
};
let assetUrl = null;
const img = ([file, w, h]) => `<image href="${assetUrl ? assetUrl(file) : assetBase + file}" width="${w}" height="${h}"/>`;

// ---------- 공통 도구 ----------
function linear(stops, vertical = true) {
  const g = id("lg");
  const dir = vertical ? 'x1="0" y1="0" x2="0" y2="1"' : 'x1="0" y1="0" x2="1" y2="0"';
  return { url: `url(#${g})`, def: `<linearGradient id="${g}" ${dir}>${stops.map(([o, c, op = 1]) => `<stop offset="${o}" stop-color="${c}" stop-opacity="${op}"/>`).join("")}</linearGradient>` };
}
function radial(stops) {
  const g = id("rg");
  return { url: `url(#${g})`, def: `<radialGradient id="${g}">${stops.map(([o, c, op = 1]) => `<stop offset="${o}" stop-color="${c}" stop-opacity="${op}"/>`).join("")}</radialGradient>` };
}
function blurFilter(sd) {
  const f = id("bl");
  return { url: `url(#${f})`, def: `<filter id="${f}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="${sd}"/></filter>` };
}
const sky = (top, bottom) => {
  const g = linear([[0, top], [1, bottom]]);
  return `<defs>${g.def}</defs><rect width="${W}" height="${H}" fill="${g.url}"/>`;
};

// 땅: 캐릭터가 서는 앞쪽 언덕
function ground(color, shade) {
  const d = `M0 ${GROUND - 26} C${W * 0.22} ${GROUND - 62} ${W * 0.58} ${GROUND + 6} ${W} ${GROUND - 34} L${W} ${H} L0 ${H}Z`;
  return `<path d="${d}" fill="${color}"/><path d="M0 ${GROUND + 10} C${W * 0.3} ${GROUND - 8} ${W * 0.7} ${GROUND + 26} ${W} ${GROUND + 4} L${W} ${H} L0 ${H}Z" fill="${shade}" opacity=".55"/>`;
}
function hills(layers) {
  // layers: [[색, 기준 y, 높이 진폭], ...] 뒤에서 앞으로
  return layers.map(([c, base, amp]) => {
    const ph = R(0, 6);
    let d = `M0 ${H} L0 ${base}`;
    for (let x = 0; x <= W; x += 40) d += ` L${x} ${(base - Math.sin(x / 260 + ph) * amp - Math.sin(x / 90 + ph) * amp * 0.12).toFixed(1)}`;
    return `<path d="${d} L${W} ${H}Z" fill="${c}"/>`;
  }).join("");
}
const cloudShape = (x, y, k, fill, op = 1, drift = true) =>
  `<g transform="translate(${f0(x)} ${f0(y)})"><g transform="scale(${k.toFixed(2)})" opacity="${op}">` +
  `<ellipse cx="0" cy="18" rx="92" ry="34" fill="${fill}"/><circle cx="-38" cy="4" r="36" fill="${fill}"/><circle cx="8" cy="-14" r="48" fill="${fill}"/><circle cx="52" cy="6" r="32" fill="${fill}"/>` +
  (drift ? `<animateTransform attributeName="transform" type="translate" additive="sum" values="0 0;${RI(30, 70)} 0;0 0" dur="${RI(26, 44)}s" repeatCount="indefinite"/>` : "") +
  `</g></g>`;
function clouds(n, fill, op, yRange = [50, 260]) {
  let s = "";
  for (let i = 0; i < n; i++) {
    const x = (i + 0.5) * (W / n) + R(-80, 80), y = R(...yRange);
    s += cloudShape(x, y, R(0.8, 1.35), fill, op);
  }
  return s;
}
function bokeh(n, colors, op = 0.35, rRange = [18, 60]) {
  const b = blurFilter(6);
  let s = `<defs>${b.def}</defs><g filter="${b.url}">`;
  for (let i = 0; i < n; i++) {
    const x = R(0, W), y = R(0, GROUND - 60), r = R(...rRange);
    s += `<circle cx="${f0(x)}" cy="${f0(y)}" r="${f0(r)}" fill="${pick(colors)}" opacity="${(op * (inBubble(x, y) ? 0.5 : 1)).toFixed(2)}">` +
      `<animate attributeName="opacity" values="${(op * 0.5).toFixed(2)};${op};${(op * 0.5).toFixed(2)}" dur="${R(3, 6).toFixed(1)}s" repeatCount="indefinite"/></circle>`;
  }
  return s + "</g>";
}
function twinkles(n, colors, avoid, size = [8, 16]) {
  let s = "";
  for (let i = 0, t = 0; i < n && t < 300; t++) {
    const x = R(20, W - 20), y = R(20, GROUND - 140);
    if (inBubble(x, y, 20) || (avoid && x > avoid.x0 && x < avoid.x1 && y > avoid.y0)) continue;
    i++;
    const k = R(...size);
    s += `<path d="M${f0(x)} ${f0(y - k)} Q${f0(x)} ${f0(y)} ${f0(x + k)} ${f0(y)} Q${f0(x)} ${f0(y)} ${f0(x)} ${f0(y + k)} Q${f0(x)} ${f0(y)} ${f0(x - k)} ${f0(y)} Q${f0(x)} ${f0(y)} ${f0(x)} ${f0(y - k)}Z" fill="${pick(colors)}">` +
      `<animate attributeName="opacity" values=".15;1;.15" dur="${R(1.6, 3.2).toFixed(1)}s" begin="${R(0, 2).toFixed(1)}s" repeatCount="indefinite"/></path>`;
  }
  return s;
}

// ---------- 장식 ----------
function bunting(y, sag, colors, x0 = -20, x1 = W + 20) {
  const n = 16, pts = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    pts.push([x0 + (x1 - x0) * t, y + Math.sin(t * Math.PI) * sag]);
  }
  let s = `<path d="M${pts.map(([x, yy]) => `${f0(x)} ${f0(yy)}`).join(" L")}" stroke="#ffffff" stroke-width="3" fill="none" opacity=".9"/>`;
  for (let i = 0; i < n; i++) {
    const [ax, ay] = pts[i], [bx, by] = pts[i + 1];
    const mx = (ax + bx) / 2, my = (ay + by) / 2;
    s += `<g transform="translate(${f0(mx)} ${f0(my)})"><g><path d="M-30 0 L30 0 L0 54Z" fill="${colors[i % colors.length]}"/>` +
      `<path d="M-30 0 L30 0 L0 54Z" fill="#fff" opacity=".18" transform="scale(.5) translate(-24 0)"/>` +
      `<animateTransform attributeName="transform" type="rotate" values="-4;4;-4" dur="${R(2.4, 3.4).toFixed(1)}s" repeatCount="indefinite"/></g></g>`;
  }
  return s;
}
function balloon(x, y, color, k = 1) {
  const g = radial([[0, "#ffffff", 0.55], [0.35, color], [1, color]]);
  return `<defs>${g.def}</defs><g transform="translate(${f0(x)} ${f0(y)}) scale(${k.toFixed(2)})"><g>` +
    `<path d="M0 78 q-8 60 6 120 q10 40 -4 90" stroke="#ffffff" stroke-width="2.5" fill="none" opacity=".8"/>` +
    `<ellipse cx="0" cy="0" rx="58" ry="72" fill="${g.url}"/><path d="M-8 70 L8 70 L0 82Z" fill="${color}"/>` +
    `<ellipse cx="-20" cy="-28" rx="12" ry="20" fill="#fff" opacity=".55" transform="rotate(-20 -20 -28)"/>` +
    `<animateTransform attributeName="transform" type="translate" values="0 0;0 -18;0 0" dur="${R(3, 4.5).toFixed(1)}s" repeatCount="indefinite"/></g></g>`;
}
function sun(x, y, r, core, glow) {
  const g = radial([[0, glow, 0.9], [0.45, glow, 0.35], [1, glow, 0]]);
  let rays = "";
  for (let i = 0; i < 12; i++) rays += `<rect x="-6" y="${f0(-r - 60)}" width="12" height="36" rx="6" fill="${core}" opacity=".7" transform="rotate(${i * 30})"/>`;
  return `<defs>${g.def}</defs><circle cx="${f0(x)}" cy="${f0(y)}" r="${f0(r * 3.2)}" fill="${g.url}"/>` +
    `<g transform="translate(${f0(x)} ${f0(y)})"><g>${rays}<animateTransform attributeName="transform" type="rotate" values="0;360" dur="60s" repeatCount="indefinite"/></g>` +
    `<circle r="${f0(r)}" fill="${core}"/><circle r="${f0(r * 0.8)}" fill="#fff" opacity=".25"/></g>`;
}
function rainbow(cx, cy, r) {
  const cols = ["#ff9aa2", "#ffc89a", "#fff1a8", "#b5ead7", "#a8d8ff", "#c7b8ff"];
  return `<g opacity=".7">${cols.map((c, i) => `<circle cx="${f0(cx)}" cy="${f0(cy)}" r="${f0(r - i * 34)}" fill="none" stroke="${c}" stroke-width="36"/>`).join("")}</g>`;
}
function lollipopTree(x, base, k, leaf, trunk) {
  return `<g transform="translate(${f0(x)} ${f0(base)}) scale(${k.toFixed(2)})"><rect x="-9" y="-120" width="18" height="120" rx="8" fill="${trunk}"/>` +
    `<circle cx="0" cy="-150" r="70" fill="${leaf}"/><circle cx="-30" cy="-170" r="26" fill="#fff" opacity=".18"/></g>`;
}
function flowers(n, colors, yBase) {
  let s = "";
  for (let i = 0; i < n; i++) {
    const x = R(0, W), y = yBase + R(-6, 20), c = pick(colors), k = R(0.6, 1.1);
    s += `<g transform="translate(${f0(x)} ${f0(y)}) scale(${k.toFixed(2)})">${[0, 72, 144, 216, 288].map((a) => `<ellipse cx="0" cy="-9" rx="6" ry="9" fill="${c}" transform="rotate(${a})"/>`).join("")}<circle r="5" fill="#ffd94d"/></g>`;
  }
  return s;
}
function blossomBranch(right) {
  const sx = right ? -1 : 1, x0 = right ? W + 20 : -20;
  let s = `<g transform="translate(${x0} -10) scale(${sx} 1)">`;
  s += `<path d="M0 40 C140 70 240 130 380 120 C460 115 520 150 560 200" stroke="#8a5a44" stroke-width="18" fill="none" stroke-linecap="round"/>`;
  s += `<path d="M240 110 C270 170 260 220 300 260" stroke="#8a5a44" stroke-width="10" fill="none" stroke-linecap="round"/>`;
  for (let i = 0; i < 26; i++) {
    const t = R(0.05, 1), x = 20 + t * 540 + R(-40, 40), y = 40 + t * 160 + R(-50, 60);
    s += `<g transform="translate(${f0(x)} ${f0(y)}) scale(${R(0.7, 1.3).toFixed(2)})">${[0, 72, 144, 216, 288].map((a) => `<ellipse cx="0" cy="-12" rx="9" ry="13" fill="${pick(["#ffc4d6", "#ffb3c9", "#ffd6e2"])}" transform="rotate(${a})"/>`).join("")}<circle r="5" fill="#ff7fa3"/></g>`;
  }
  return s + "</g>";
}
function petals(n) {
  let s = "";
  for (let i = 0; i < n; i++) {
    const x = R(0, W), d = R(6, 11);
    s += `<ellipse cx="${f0(x)}" cy="-20" rx="8" ry="5" fill="${pick(["#ffc4d6", "#ffb3c9", "#ffe0ea"])}" opacity=".9">` +
      `<animateTransform attributeName="transform" type="translate" values="0 0;${RI(-160, 160)} ${H + 40}" dur="${d.toFixed(1)}s" begin="-${R(0, d).toFixed(1)}s" repeatCount="indefinite"/></ellipse>`;
  }
  return s;
}
function moon(x, y, r) {
  const g = radial([[0, "#fff6c9", 0.7], [0.5, "#fff6c9", 0.15], [1, "#fff6c9", 0]]);
  // 초승달: 큰 원에서 오른쪽 위로 비켜난 원을 뺀 모양
  const r2 = r * 0.9, bx = x + r * 0.45, by = y - r * 0.25;
  const dx = bx - x, dy = by - y, d = Math.hypot(dx, dy);
  const a1 = (r * r - r2 * r2 + d * d) / (2 * d), hh = Math.sqrt(r * r - a1 * a1);
  const px = x + (a1 * dx) / d, py = y + (a1 * dy) / d;
  const p1 = [px + (hh * dy) / d, py - (hh * dx) / d], p2 = [px - (hh * dy) / d, py + (hh * dx) / d];
  const crescent = `M${p1[0].toFixed(1)} ${p1[1].toFixed(1)} A${r} ${r} 0 1 0 ${p2[0].toFixed(1)} ${p2[1].toFixed(1)} A${r2.toFixed(1)} ${r2.toFixed(1)} 0 0 1 ${p1[0].toFixed(1)} ${p1[1].toFixed(1)}Z`;
  return `<defs>${g.def}</defs><circle cx="${f0(x)}" cy="${f0(y)}" r="${f0(r * 3)}" fill="${g.url}"/><path d="${crescent}" fill="#fff4c2"/>`;
}
function stars(n, color) {
  let s = "";
  for (let i = 0; i < n; i++) {
    const x = R(0, W), y = R(0, GROUND - 220), r = R(1.5, 3.5);
    s += `<circle cx="${f0(x)}" cy="${f0(y)}" r="${r.toFixed(1)}" fill="${color}"><animate attributeName="opacity" values=".2;1;.2" dur="${R(2, 5).toFixed(1)}s" begin="${R(0, 3).toFixed(1)}s" repeatCount="indefinite"/></circle>`;
  }
  return s;
}
function rain(n, color, slant = -10) {
  let s = "";
  for (let i = 0; i < n; i++) {
    const x = R(-100, W + 100), len = R(30, 60), d = R(0.7, 1.3);
    s += `<line x1="${f0(x)}" y1="-60" x2="${f0(x + slant)}" y2="${f0(-60 + len)}" stroke="${color}" stroke-width="3" stroke-linecap="round" opacity="${R(0.35, 0.7).toFixed(2)}">` +
      `<animateTransform attributeName="transform" type="translate" values="0 0;${f0(slant * 18)} ${H + 80}" dur="${d.toFixed(2)}s" begin="-${R(0, d).toFixed(2)}s" repeatCount="indefinite"/></line>`;
  }
  return s;
}
function puddles(n, color) {
  let s = "";
  for (let i = 0; i < n; i++) {
    const x = R(60, W - 60), y = GROUND + R(-2, 18), rx = R(60, 120);
    s += `<ellipse cx="${f0(x)}" cy="${f0(y)}" rx="${f0(rx)}" ry="${f0(rx * 0.14)}" fill="${color}" opacity=".5"/>` +
      `<ellipse cx="${f0(x + R(-20, 20))}" cy="${f0(y)}" rx="10" ry="2" fill="none" stroke="#fff" stroke-width="2"><animate attributeName="rx" values="6;40" dur="1.6s" begin="${R(0, 1.6).toFixed(1)}s" repeatCount="indefinite"/>` +
      `<animate attributeName="ry" values="1;6" dur="1.6s" begin="0s" repeatCount="indefinite"/><animate attributeName="opacity" values=".8;0" dur="1.6s" repeatCount="indefinite"/></ellipse>`;
  }
  return s;
}
function windowDrops(n) {
  let s = "";
  for (let i = 0; i < n; i++) {
    const x = R(0, W), y = R(0, H - 120), r = R(4, 11);
    if (inBubble(x, y)) continue;
    s += `<g opacity=".7"><circle cx="${f0(x)}" cy="${f0(y)}" r="${r.toFixed(1)}" fill="#ffffff" opacity=".35"/><circle cx="${f0(x - r * 0.3)}" cy="${f0(y - r * 0.3)}" r="${(r * 0.3).toFixed(1)}" fill="#fff"/>` +
      `<path d="M${f0(x)} ${f0(y + r)} q${R(-6, 6).toFixed(0)} ${f0(R(20, 70))} 0 ${f0(R(40, 110))}" stroke="#fff" stroke-width="${(r * 0.5).toFixed(1)}" stroke-linecap="round" fill="none" opacity=".25"/></g>`;
  }
  return s;
}
function curtains(color, dark) {
  const one = (flip) => {
    const sx = flip ? -1 : 1, x0 = flip ? W : 0;
    let folds = "";
    for (let i = 0; i < 5; i++) folds += `<path d="M${i * 46} 0 C${i * 46 + 20} 300 ${i * 46 - 10} 600 ${i * 46 + 30 + (4 - i) * 22} ${H}" stroke="${dark}" stroke-width="10" fill="none" opacity=".45"/>`;
    return `<g transform="translate(${x0} 0) scale(${sx} 1)"><path d="M0 0 L250 0 C230 300 200 520 300 ${H} L0 ${H}Z" fill="${color}"/>${folds}` +
      `<ellipse cx="230" cy="560" rx="26" ry="14" fill="#f2c200"/></g>`;
  };
  let valance = `<rect x="0" y="0" width="${W}" height="70" fill="${color}"/>`;
  for (let x = 0; x < W; x += 100) valance += `<path d="M${x} 70 q50 50 100 0Z" fill="${color}"/>`;
  return one(false) + one(true) + valance + `<rect x="0" y="0" width="${W}" height="18" fill="${dark}" opacity=".5"/>`;
}
function spotlight(x0, y0, x1, w, color) {
  const g = linear([[0, color, 0.55], [1, color, 0]]);
  return `<defs>${g.def}</defs><path d="M${f0(x0 - 20)} ${f0(y0)} L${f0(x0 + 20)} ${f0(y0)} L${f0(x1 + w)} ${GROUND} L${f0(x1 - w)} ${GROUND}Z" fill="${g.url}">` +
    `<animate attributeName="opacity" values=".7;1;.7" dur="3s" repeatCount="indefinite"/></path>` +
    `<ellipse cx="${f0(x1)}" cy="${GROUND}" rx="${f0(w)}" ry="26" fill="${color}" opacity=".35"/>`;
}
function stageFloor(top, color, line) {
  let s = `<rect x="0" y="${top}" width="${W}" height="${H - top}" fill="${color}"/>`;
  for (let x = -200; x < W + 200; x += 120) s += `<line x1="${W / 2 + (x - W / 2) * 0.55}" y1="${top}" x2="${x}" y2="${H}" stroke="${line}" stroke-width="2" opacity=".35"/>`;
  return s + `<rect x="0" y="${top}" width="${W}" height="6" fill="#fff" opacity=".25"/>`;
}
function sunburst(cx, cy, c1, op = 0.5) {
  let s = `<g opacity="${op}">`;
  for (let i = 0; i < 20; i++) {
    const a0 = (i / 20) * Math.PI * 2, a1 = a0 + Math.PI / 20, L = 2400;
    s += `<path d="M${f0(cx)} ${f0(cy)} L${f0(cx + Math.cos(a0) * L)} ${f0(cy + Math.sin(a0) * L)} L${f0(cx + Math.cos(a1) * L)} ${f0(cy + Math.sin(a1) * L)}Z" fill="${c1}"/>`;
  }
  return s + `<animateTransform attributeName="transform" type="rotate" values="0 ${f0(cx)} ${f0(cy)};18 ${f0(cx)} ${f0(cy)}" dur="20s" repeatCount="indefinite"/></g>`;
}
function burst(cx, cy, r, fill, stroke) {
  let d = "";
  for (let i = 0; i < 28; i++) {
    const a = (i / 28) * Math.PI * 2, rr = i % 2 ? r * 0.72 : r * R(0.95, 1.15);
    d += `${i ? "L" : "M"}${f0(cx + Math.cos(a) * rr)} ${f0(cy + Math.sin(a) * rr)} `;
  }
  return `<path d="${d}Z" fill="${fill}" stroke="${stroke}" stroke-width="10" stroke-linejoin="round"><animateTransform attributeName="transform" type="scale" additive="sum" values="1;1.03;1" dur=".5s" repeatCount="indefinite"/></path>`;
}
function speedLines(cx, cy, color) {
  let s = "";
  for (let i = 0; i < 36; i++) {
    const a = (i / 36) * Math.PI * 2 + R(-0.04, 0.04), r0 = R(560, 700), r1 = 1600;
    s += `<line x1="${f0(cx + Math.cos(a) * r0)}" y1="${f0(cy + Math.sin(a) * r0)}" x2="${f0(cx + Math.cos(a) * r1)}" y2="${f0(cy + Math.sin(a) * r1)}" stroke="${color}" stroke-width="${R(3, 9).toFixed(0)}" opacity=".35"/>`;
  }
  return s;
}
function heatWaves(color) {
  let s = "";
  for (let i = 0; i < 9; i++) {
    const x = R(40, W - 40);
    if (x > BUBBLE.x0 && x < BUBBLE.x1) continue;
    s += `<path d="M${f0(x)} ${GROUND - 40} q16 -30 0 -60 q-16 -30 0 -60 q16 -30 0 -60" stroke="${color}" stroke-width="6" fill="none" stroke-linecap="round" opacity="0">` +
      `<animate attributeName="opacity" values="0;.6;0" dur="${R(2, 3).toFixed(1)}s" begin="${R(0, 2).toFixed(1)}s" repeatCount="indefinite"/>` +
      `<animateTransform attributeName="transform" type="translate" values="0 0;0 -80" dur="${R(2, 3).toFixed(1)}s" repeatCount="indefinite"/></path>`;
  }
  return s;
}
function birds(n, color) {
  let s = "";
  for (let i = 0; i < n; i++) {
    const x = R(80, W - 80), y = R(80, 220);
    if (inBubble(x, y)) continue;
    s += `<path d="M${f0(x - 18)} ${f0(y)} q9 -10 18 0 q9 -10 18 0" stroke="${color}" stroke-width="4" fill="none" stroke-linecap="round">` +
      `<animateTransform attributeName="transform" type="translate" values="0 0;40 -10;0 0" dur="${R(6, 10).toFixed(1)}s" repeatCount="indefinite"/></path>`;
  }
  return s;
}
function bleachers(top, colors) {
  let s = "";
  for (let r = 0; r < 4; r++) {
    const y = top + r * 34;
    s += `<rect x="0" y="${y}" width="${W}" height="30" fill="${colors[r % colors.length]}" opacity=".55"/>`;
    for (let x = 20 + (r % 2) * 20; x < W; x += 40) s += `<circle cx="${x}" cy="${y + 8}" r="9" fill="${pick(["#ffffff", "#2f7fd1", "#f2c200", "#ff8fb3"])}" opacity=".7"/>`;
  }
  return s;
}

// ---------- 감정별 장면 (hero: 제이제이 반대편 위쪽 x) ----------
// 각 장면은 { back, front } 를 돌려준다. front는 캐릭터 위에 그려지는 것
const SCENES = {
  축하: [
    ["파티", function party(c) {
      const back = sky("#ffe3ef", "#fff6fa") + bokeh(18, ["#ffb3cc", "#ffd27a", "#b9a6ff", "#8fd3ff"], 0.4) +
        balloon(c.hero - 60, 300, "#ff6f9e", 1) + balloon(c.hero + 40, 250, "#ffcf3f", 0.9) + balloon(c.hero + 120, 330, "#6fb6ff", 0.95) +
        ground("#ffc9dc", "#ffb3cc");
      return { back, front: bunting(-10, 90, ["#ff6f9e", "#ffcf3f", "#6fb6ff", "#8ee0b5", "#b9a6ff"]) };
    }],
    ["무대", function stage(c) {
      const back = sky("#2a2150", "#4a2f6e") + stars(40, "#ffe9a8") + curtains("#c2304a", "#7d1428") +
        stageFloor(GROUND - 40, "#6b3f2a", "#3f2416") +
        spotlight(c.jjX - 200, 0, c.jjX, 260, "#fff6d0") + spotlight(c.hero, 0, c.hero + (c.right ? 80 : -80), 200, "#ffd1e8");
      return { back, front: "" };
    }],
  ],
  기쁨: [
    ["맑은 언덕", function sunnyHills(c) {
      const back = sky("#9fd8ff", "#e8f7ff") + sun(c.hero, 150, 70, "#ffd84d", "#fff3b0") + clouds(3, "#ffffff", 0.95) +
        hills([["#bfe9c8", 720, 50], ["#9edcae", 780, 40]]) + flowers(26, ["#ffffff", "#ff9ec0", "#ffd94d"], 800) + ground("#8fd49f", "#79c58c");
      return { back, front: "" };
    }],
    ["무지개", function rainbowDay(c) {
      const back = sky("#cdeaff", "#fffaf0") + rainbow(c.hero, 760, 640) + cloudShape(c.hero - 560, 690, 1.4, "#ffffff", 1, false) +
        cloudShape(c.hero + 560, 690, 1.4, "#ffffff", 1, false) + clouds(2, "#ffffff", 0.9, [60, 160]) +
        hills([["#c9efd2", 790, 30]]) + ground("#a6e2b6", "#8fd49f");
      return { back, front: "" };
    }],
  ],
  응원: [
    ["햇살 응원", function rays(c) {
      const back = sky("#fff3c4", "#fffaf0") + sunburst(c.jjX, GROUND - 320, "#ffe28a", 0.6) + twinkles(14, ["#ffb800", "#2f7fd1", "#ffffff"], c.avoid, [10, 20]) +
        ground("#ffd36b", "#ffc23d");
      return { back, front: bunting(-20, 70, ["#2f7fd1", "#ffcf3f", "#ffffff", "#2f7fd1", "#ff8a5c"]) };
    }],
    ["운동장", function stadium(c) {
      const back = sky("#7cc4ff", "#d9f0ff") + clouds(3, "#ffffff", 0.9, [40, 150]) + bleachers(GROUND - 230, ["#5b8fd6", "#7aa6e0"]) +
        `<rect x="0" y="${GROUND - 92}" width="${W}" height="20" fill="#ffffff" opacity=".8"/>` + ground("#6ccf7f", "#56b96a") +
        twinkles(8, ["#ffffff", "#ffd94d"], c.avoid, [10, 18]);
      return { back, front: bunting(-20, 60, ["#2f7fd1", "#ffcf3f", "#ffffff"]) };
    }],
  ],
  설렘: [
    ["벚꽃", function blossom(c) {
      const back = sky("#ffd9e6", "#fff4f7") + bokeh(14, ["#ffc4d6", "#ffffff", "#ffe0ea"], 0.5) + clouds(2, "#ffffff", 0.8, [120, 220]) +
        hills([["#ffd1df", 790, 30]]) + ground("#ffc0d3", "#ffadc5");
      return { back, front: blossomBranch(!c.right) + petals(24) };
    }],
    ["노을", function sunset(c) {
      const back = sky("#ffb8c9", "#ffe6c7") + sun(c.hero, GROUND - 210, 110, "#ffd1a1", "#ffe8d6") +
        hills([["#ffc6c9", 760, 40], ["#ffb3bd", 810, 30]]) + bokeh(16, ["#ff8fb3", "#ffffff"], 0.35, [12, 34]) + ground("#ff9fb2", "#ff8aa2");
      return { back, front: "" };
    }],
  ],
  피곤: [
    ["밤하늘", function night(c) {
      const bg = "#26305e";
      const back = sky("#1d2550", "#3c4683") + stars(90, "#ffffff") + moon(c.hero, 170, 70) +
        clouds(3, "#4b5596", 0.6, [220, 380]) + hills([["#2c3770", 760, 40]]) + ground("#3a4685", "#2f3a72");
      return { back, front: "" };
    }],
    ["구름 침대", function cloudBed(c) {
      const back = sky("#b9a8e6", "#f1d9f0") + stars(30, "#ffffff") + moon(c.hero, 150, 56);
      let bed = "";
      for (let i = 0; i < 9; i++) bed += cloudShape(i * 200 + R(-30, 30), GROUND - R(10, 40), R(1.4, 1.9), "#ffffff", 0.95, false);
      return { back: back + bed, front: "" };
    }],
  ],
  슬픔: [
    ["비 오는 날", function rainyDay() {
      const back = sky("#8fa4ba", "#d5dee8") + clouds(5, "#6f8299", 0.9, [10, 90]) + clouds(4, "#8597ad", 0.8, [80, 160]) +
        hills([["#a7b8c9", 790, 24]]) + ground("#93a6b9", "#8094a8") + puddles(4, "#c3d2e0");
      return { back, front: rain(110, "#e8f1fb") };
    }],
    ["창가 빗방울", function windowRain() {
      const b = blurFilter(14);
      let city = `<defs>${b.def}</defs><g filter="${b.url}">`;
      for (let i = 0; i < 26; i++) city += `<circle cx="${f0(R(0, W))}" cy="${f0(R(420, 820))}" r="${f0(R(20, 50))}" fill="${pick(["#ffd27a", "#9ecbff", "#ffffff"])}" opacity=".35"/>`;
      city += "</g>";
      const back = sky("#4e6383", "#8ea3bf") + city + ground("#6f84a3", "#5f7493");
      return { back, front: windowDrops(70) + rain(40, "#dce8f6", -4) };
    }],
  ],
  화남: [
    ["빠직", function explosion(c) {
      const back = sky("#ff8a5c", "#ffd0a8") + speedLines(c.jjX, GROUND - 330, "#ffffff") +
        burst(c.jjX, GROUND - 330, 360, "#ffd84d", "#ff6a2a") + ground("#ff9a6b", "#ff8250");
      return { back, front: "" };
    }],
    ["열기", function heat(c) {
      const back = sky("#ff6b6b", "#ffc2a6") + sun(c.hero, 160, 80, "#ffe066", "#ffb36b") + heatWaves("#fff2d6") +
        hills([["#ff9478", 780, 40]]) + ground("#ff8a6e", "#ff7458");
      return { back, front: "" };
    }],
  ],
  일반: [
    ["공원 산책", function park(c) {
      const back = sky("#bfe8f2", "#f1fbf6") + clouds(3, "#ffffff", 0.95) + birds(5, "#6c8ba3") +
        hills([["#c8ecd5", 740, 40], ["#acdfbf", 790, 30]]) +
        lollipopTree(c.hero - 80, 800, 0.9, "#7ccf94", "#a77b5a") + lollipopTree(c.hero + 70, 810, 0.7, "#95dca8", "#a77b5a") +
        ground("#9fd9b0", "#86cc9b");
      return { back, front: "" };
    }],
    ["파스텔 구름", function pastelClouds() {
      const back = sky("#d7d1ff", "#fdf3ff") + clouds(4, "#ffffff", 0.95, [60, 300]) + bokeh(10, ["#ffffff", "#ffd6f0"], 0.4) +
        hills([["#e6dcff", 790, 26]]) + ground("#d4c6ff", "#c3b2fb");
      return { back, front: "" };
    }],
  ],
};

// ---------- 캐릭터 ----------
const MOTION = {
  응원: (h) => `<animateTransform attributeName="transform" type="rotate" values="-3;3;-3" dur="1.2s" repeatCount="indefinite"/>`,
  축하: (h) => `<animateTransform attributeName="transform" type="translate" values="0 0;0 -${f0(h * 0.06)};0 0;0 0" keyTimes="0;.3;.6;1" dur="1.2s" repeatCount="indefinite"/>`,
  기쁨: (h) => `<animateTransform attributeName="transform" type="rotate" values="-4;4;-4" dur="1.6s" repeatCount="indefinite"/>`,
  설렘: (h) => `<animateTransform attributeName="transform" type="rotate" values="-2;2;-2" dur="2.8s" repeatCount="indefinite"/>`,
  피곤: (h) => `<animateTransform attributeName="transform" type="rotate" values="0;3;3;0" keyTimes="0;.45;.7;1" dur="3.6s" repeatCount="indefinite"/>`,
  슬픔: (h) => `<animateTransform attributeName="transform" type="translate" values="0 0;0 8;0 0" dur="4s" repeatCount="indefinite"/>`,
  화남: (h) => `<animateTransform attributeName="transform" type="translate" values="0 0;-6 0;6 0;-6 0;6 0;0 0;0 0" keyTimes="0;.08;.16;.24;.32;.4;1" dur="1.8s" repeatCount="indefinite"/>`,
  일반: (h) => `<animateTransform attributeName="transform" type="translate" values="0 0;0 -${f0(h * 0.015)};0 0" dur="3s" repeatCount="indefinite"/>`,
};

function stand(art, cx, h, mood) {
  const [, w, ih] = art;
  const k = h / ih, sw = w * k;
  const shadow = `<ellipse cx="${f0(cx)}" cy="${GROUND + 4}" rx="${f0(sw * 0.36)}" ry="${f0(h * 0.03 + 6)}" fill="#000" opacity=".12"/>`;
  const body = `<g transform="translate(${cx.toFixed(1)} ${GROUND})"><g>${MOTION[mood](h)}` +
    `<g transform="scale(${k.toFixed(4)}) translate(${(-w / 2).toFixed(1)} ${-ih})">${img(art)}</g></g></g>`;
  return { s: shadow + body, top: GROUND - h, left: cx - sw / 2, right: cx + sw / 2, cx };
}

function floater(art, cx, top, h, mood, i) {
  const [, w, ih] = art;
  const k = h / ih;
  const jump = mood === "축하" || mood === "기쁨" || mood === "응원";
  const anim = jump
    ? `<animateTransform attributeName="transform" type="translate" values="0 0;0 -24;0 0;0 0" keyTimes="0;.3;.6;1" dur="1.2s" begin="${(i * 0.3).toFixed(1)}s" repeatCount="indefinite"/>`
    : `<animateTransform attributeName="transform" type="translate" values="0 0;0 -14;0 0" dur="${R(2.6, 3.6).toFixed(1)}s" repeatCount="indefinite"/>`;
  return `<g transform="translate(${f0(cx)} ${f0(top)}) rotate(${R(-8, 8).toFixed(1)})"><g>${anim}` +
    `<g transform="scale(${k.toFixed(4)}) translate(${(-w / 2).toFixed(1)} 0)">${img(art)}</g></g></g>`;
}

const FRIEND_KEYWORDS = [
  [/시험|공부|과제|중간|기말|레포트|발표|팀플|책|도서관|수업|강의/, "jeit"],
  [/별|화이팅|파이팅|힘내|합격|축하|생일|생축|졸업|축제|설레|기대|꿈/, "byeoli"],
];
const TOGETHER_WORDS = /함께|같이|다같이|모두|우리|다들/;
const BRIGHT = ["축하", "기쁨", "응원", "설렘", "일반"];
const NAME = { byeoli: "별이", jeit: "제이트" };

function castFor(mood, text) {
  const key = FRIEND_KEYWORDS.find(([re]) => re.test(text))?.[1];
  if (!BRIGHT.includes(mood)) return key ? [key] : rnd() < 0.35 ? [pick(["byeoli", "jeit"])] : [];
  if (TOGETHER_WORDS.test(text) || rnd() < 0.4) return key === "jeit" ? ["jeit", "byeoli"] : ["byeoli", "jeit"];
  return [key || pick(["byeoli", "jeit"])];
}

// ---------- 감정 효과 (제이제이 머리 기준) ----------
const FX = {
  응원: (a) => "",
  축하: () => [...Array(30)].map(() => {
    const x = R(20, W - 20), c = pick(["#ff6f9e", "#ffcf3f", "#6fb6ff", "#8ee0b5", "#b9a6ff"]), d = R(4, 7);
    return `<rect x="${f0(x)}" y="-30" width="12" height="20" rx="3" fill="${c}" transform="rotate(${RI(0, 90)} ${f0(x)} -20)">` +
      `<animateTransform attributeName="transform" type="translate" additive="sum" values="0 0;${RI(-60, 60)} ${H + 60}" dur="${d.toFixed(1)}s" begin="-${R(0, d).toFixed(1)}s" repeatCount="indefinite"/></rect>`;
  }).join(""),
  기쁨: (a) => twinkles(5, ["#ffd94d", "#ffffff"], null, [12, 20]),
  설렘: (a) => [0, 1, 2].map((i) =>
    `<g transform="translate(${f0(a.cx - 70 + i * 70)} ${f0(a.top + 30)})"><path d="M0 22 C-30 3 -21 -22 -7 -21 C-3 -21 0 -16 0 -13 C0 -16 3 -21 7 -21 C21 -22 30 3 0 22Z" fill="#ff6f9e">` +
    `<animateTransform attributeName="transform" type="translate" values="0 0;${RI(-30, 30)} -160" dur="2.6s" begin="${(i * 0.85).toFixed(2)}s" repeatCount="indefinite"/>` +
    `<animate attributeName="opacity" values="0;1;0" dur="2.6s" begin="${(i * 0.85).toFixed(2)}s" repeatCount="indefinite"/></path></g>`).join(""),
  피곤: (a) => [[0, 0, 64], [46, -56, 46], [80, -100, 32]].map(([dx, dy, fs], i) =>
    `<text x="${f0(a.right - 40 + dx)}" y="${f0(a.top + 60 + dy)}" font-size="${fs}" font-weight="800" fill="#c9d0ff" font-family="sans-serif" opacity="0">Z` +
    `<animate attributeName="opacity" values="0;.9;0" dur="2.4s" begin="${(i * 0.5).toFixed(1)}s" repeatCount="indefinite"/></text>`).join(""),
  슬픔: () => "",
  화남: (a) => `<g transform="translate(${f0(a.right - 60)} ${f0(a.top + 50)})"><g stroke="#e8352a" stroke-width="11" stroke-linecap="round" fill="none">` +
    `<path d="M-22 -4 q14 4 18 -18 M4 -22 q4 14 18 18 M22 4 q-14 -4 -18 18 M-4 22 q-4 -14 -18 -18"/>` +
    `<animateTransform attributeName="transform" type="scale" values="1;1.2;1" dur=".6s" repeatCount="indefinite"/></g></g>`,
  일반: () => "",
};


// Backend emotion labels -> generator moods
const EMOTION_TO_MOOD = {
  CELEBRATION: "축하", JOY: "기쁨", SUPPORT: "응원", EXCITEMENT: "설렘",
  TIRED: "피곤", SADNESS: "슬픔", ANGER: "화남", NEUTRAL: "일반",
};

export const MOODS = Object.keys(SCENES);
export const SAMPLE_MESSAGES = {
  응원: "다들 시험 화이팅!", 축하: "민수야 생일 축하해!!", 기쁨: "시험 끝나서 너무 좋다", 설렘: "내일 축제 너무 기대돼",
  피곤: "밤새 과제해서 죽겠다", 슬픔: "오늘 좀 우울하다", 화남: "와이파이 또 끊김;;", 일반: "점심 뭐 먹지",
};

export function moodOf(emotion) {
  if (!emotion) return "일반";
  if (SCENES[emotion]) return emotion;
  return EMOTION_TO_MOOD[String(emotion).toUpperCase()] || "일반";
}

/**
 * Full-screen SVG background for one approved mention.
 * Same seed -> same picture, so re-renders and reloads do not reshuffle.
 * `content` is only used for keyword matching and is never written into the SVG.
 * `side` ("left" | "right") pins JJ to one side; omit it to pick randomly.
 * `assetUrl(file)` returns the URL for an image file (bundler-hashed paths); otherwise `mascotBase + file`.
 * `aspect` is width / height of the box the SVG fills, so wide panels get a wide scene instead of a cropped one.
 */
export function generateBackground({ emotion, content = "", seed = "", mascotBase = "/mascot/", assetUrl: resolveAsset = null, side, aspect = 16 / 9 } = {}) {
  const mood = moodOf(emotion);
  W = Math.round(H * Math.min(Math.max(Number(aspect) || 16 / 9, 1.2), 3.2));
  BUBBLE = { x0: W * 0.29, x1: W * 0.71, y0: 250, y1: 640 };
  const h = hashString(`${seed}|${mood}|${W}`);
  rnd = mulberry32(h);
  uid = 0;
  idBase = `mw${h.toString(36)}`;
  assetBase = mascotBase;
  assetUrl = typeof resolveAsset === "function" ? resolveAsset : null;
  try {
    const text = String(content);
    const coin = rnd() < 0.6;
    // side로 제이제이 위치를 고정할 수 있다 (전광판은 QR이 오른쪽 아래라 왼쪽)
    const right = side === "right" ? true : side === "left" ? false : coin;

    // 주인공 3D 제이제이
    const art = ART.jj(mood);
    const jh = R(600, 640), jw = art[1] * jh / art[2];
    const jjX = right ? W - jw / 2 - R(14, 34) : jw / 2 + R(14, 34);
    const main = stand(art, jjX, jh, mood);

    // 장면 (hero 장식은 제이제이 반대편 위쪽)
    const hero = right ? R(260, 360) : W - R(260, 360);
    const avoid = { x0: main.left - 30, x1: main.right + 30, y0: main.top - 60 };
    const [sceneName, sceneFn] = pick(SCENES[mood]);
    const scene = sceneFn({ right, jjX, hero, avoid });

    // 친구 자리: 제이제이 머리 위 / 반대편 땅 / 반대편 중간
    const spots = shuffle([
      { cx: jjX + (right ? -1 : 1) * R(150, 210), top: R(20, 70), h: R(150, 180) },
      // 위치를 고정한 화면(전광판)은 반대편 아래에 QR이 있어서 땅 자리를 쓰지 않는다
      side ? { cx: right ? R(110, 200) : W - R(110, 200), top: R(60, 140), h: R(150, 180) }
        : { cx: right ? R(170, 300) : W - R(170, 300), h: 220, ground: true },
      { cx: right ? R(110, 200) : W - R(110, 200), top: R(330, 400), h: R(150, 180) },
    ]);
    const cast = castFor(mood, text);
    let friends = "";
    cast.forEach((who, i) => {
      const sp = { ...spots[i] };
      if (who === "jeit") sp.h *= 1.15;
      friends += sp.ground ? stand(ART[who], sp.cx, sp.h, mood).s : floater(ART[who], sp.cx, sp.top, sp.h, mood, i);
    });

    const body = scene.back + friends + main.s + scene.front + FX[mood](main);
    return {
      mood,
      svg: `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMax slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${body}</svg>`,
      label: `${sceneName} · 제이제이${cast.map((c) => ", " + NAME[c]).join("")}`,
    };
  } finally {
    rnd = Math.random;
  }
}
