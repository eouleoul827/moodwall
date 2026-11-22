// Writes background-preview.html in the repo root to eyeball every mood.
// Usage: node scripts/preview_background.mjs [seed]
import { writeFileSync } from "node:fs";
import { generateBackground, MOODS, SAMPLE_MESSAGES } from "../frontend/src/display/background/generator.js";

const seed = process.argv[2] ?? "preview";
const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const samples = [...MOODS.map((mood) => [mood, SAMPLE_MESSAGES[mood]]),
  ["CELEBRATION", "다같이 졸업 축하해!!"], ["SUPPORT", "기말 공부 화이팅"], ["ANGER", "과제 왜 이렇게 많아"]];

const cards = samples.flatMap(([emotion, text]) =>
  [1, 2].map((n) => {
    const { mood, svg, label } = generateBackground({ emotion, content: text, seed: `${seed}-${n}-${text}`, mascotBase: "frontend/public/mascot/" });
    return `<figure><div class="card">${svg}<div class="bubble">${esc(text)}</div></div><figcaption>${mood} · ${esc(label)}</figcaption></figure>`;
  }),
);

writeFileSync(
  "background-preview.html",
  `<!doctype html><meta charset="utf-8"><title>mood backgrounds</title>
<style>
body{margin:0;padding:16px;font-family:system-ui,"Malgun Gothic",sans-serif;background:#f4f4f4}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px}
figure{margin:0}.card{position:relative;aspect-ratio:16/9;border-radius:14px;overflow:hidden}
.card svg{position:absolute;inset:0;width:100%;height:100%}.mw-grain{mix-blend-mode:overlay}
.bubble{position:absolute;left:50%;top:42%;transform:translate(-50%,-50%);background:rgba(255,255,255,.75);padding:10px 18px;border-radius:22px;font-weight:700}
figcaption{font-size:12px;color:#666;margin-top:6px}
</style><main>${cards.join("")}</main>`,
);
console.log("background-preview.html");
