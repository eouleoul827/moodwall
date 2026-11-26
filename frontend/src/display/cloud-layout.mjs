import { CLOUD_VARIANTS } from './cloud-variants.mjs';

export const WALL_WIDTH = 2046;
export const WALL_HEIGHT = 768;
export const CLOUD_LOOP_WIDTH = 3400;
const MOTION_SAMPLES = [[0, 1], [20, .8], [45, .65], [75, .5], [110, .4], [150, .3]];
const wrapPosition = value => ((value % CLOUD_LOOP_WIDTH) + CLOUD_LOOP_WIDTH) % CLOUD_LOOP_WIDTH;
const SEGMENTER = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter('ko', { granularity: 'grapheme' }) : null;


function characters(value) {
  if (SEGMENTER) return [...SEGMENTER.segment(value)].map(part => part.segment);
  const result = [];
  let previous = '', regionalCount = 0;
  for (const char of Array.from(value)) {
    const cp = char.codePointAt(0);
    const regional = cp >= 0x1f1e6 && cp <= 0x1f1ff;
    const joining = char === '\u200d' || previous === '\u200d' || /\p{Mark}/u.test(char)
      || cp >= 0xfe00 && cp <= 0xfe0f || cp >= 0x1f3fb && cp <= 0x1f3ff
      || cp >= 0xe0020 && cp <= 0xe007f || regional && regionalCount % 2 === 1;
    if (result.length && joining) result[result.length - 1] += char;
    else result.push(char);
    regionalCount = regional ? regionalCount + 1 : 0;
    previous = char;
  }
  return result;
}

export function wrapCloudText(value, maxUnits = 10) {
  const all = characters(String(value ?? '').normalize('NFC').replace(/\s+/gu, ' ').trim());
  const visible = all.slice(0, 30);
  if (all.length > 30) visible[29] = '…';
  if (!visible.length) return [];
  const capacity = Number.isFinite(maxUnits) && maxUnits > 0 ? maxUnits : 10;
  const targetLines = visible.length <= 6 ? 1 : visible.length <= 16 ? 2 : 3;
  const totalUnits = units(visible.join(''));
  let best = null;
  for (let lineCount = 1; lineCount <= 3; lineCount++) {
    const memo = new Map();
    const ideal = totalUnits / lineCount;
    const solve = (offset, availableLines) => {
      while (visible[offset] === ' ') offset++;
      if (offset === visible.length) return availableLines === 0 ? { lines: [], splits: 0, balance: 0 } : null;
      if (!availableLines) return null;
      const key = `${offset}:${availableLines}`;
      if (memo.has(key)) return memo.get(key);
      let choice = null, lineUnits = 0;
      for (let end = offset + 1; end <= visible.length; end++) {
        lineUnits += visible[end - 1] === ' ' ? .34 : 1;
        if (lineUnits > capacity + .000001) break;
        if (visible[end - 1] === ' ') continue;
        const tail = solve(end, availableLines - 1);
        if (!tail) continue;
        const splits = tail.splits + (end < visible.length && visible[end] !== ' ' ? 1 : 0);
        const balance = tail.balance + (lineUnits - ideal) ** 2;
        if (!choice || splits < choice.splits || splits === choice.splits && balance < choice.balance) {
          choice = { lines: [visible.slice(offset, end).join(''), ...tail.lines], splits, balance };
        }
      }
      memo.set(key, choice);
      return choice;
    };
    const candidate = solve(0, lineCount);
    if (!candidate) continue;
    const distance = Math.abs(lineCount - targetLines);
    // Preserve whole words before balancing the lines. Only split a word when
    // no complete, three-line arrangement fits the available safe width.
    if (!best || candidate.splits < best.splits
      || candidate.splits === best.splits && (distance < best.distance
        || distance === best.distance && candidate.balance < best.balance)) {
      best = { ...candidate, distance };
    }
  }
  // The metrics below always supply enough room. A narrower external budget
  // must still preserve all 30 graphemes, rather than silently discard a tail.
  return best ? best.lines : wrapCloudText(visible.join(''), Math.max(10, capacity));
}

function seedOf(value) {
  let seed = 2166136261;
  for (const char of String(value ?? '')) seed = Math.imul(seed ^ char.codePointAt(0), 16777619);
  return seed >>> 0;
}
function randomFrom(seed) {
  let state = seed || 1;
  return () => { state ^= state << 13; state ^= state >>> 17; state ^= state << 5; return (state >>> 0) / 4294967296; };
}
const units = line => characters(line).reduce((sum, char) => sum + (/^\s+$/u.test(char) ? .34 : 1), 0);

export function cloudMetrics(item) {
  const seed = seedOf(item.id ?? item.sceneSeed);
  const font = 28 + (seed % 3) * .5;
  const content = characters(String(item.content ?? '').normalize('NFC').replace(/\s+/gu, ' ').trim()).slice(0, 30).join('');
  const count = Math.max(1, characters(content).length);
  // Both length and the longest existing word stay the same or grow when text
  // is appended. Rebalancing lines therefore cannot make the cloud narrower.
  const widthFor = text => {
    const length = Math.max(1, characters(text).length);
    const lengthWidth = length <= 6 ? 200 + (length - 1) * 16
      : length <= 16 ? 280 + (length - 6) * 8 : 360 + (length - 16) * (80 / 14);
    const longestWord = Math.min(10, Math.max(1, ...text.split(' ').map(units)));
    return Math.min(440, Math.max(lengthWidth, longestWord * font / .70 + 12));
  };
  const width = widthFor(content);
  const lines = wrapCloudText(item.content, (width * .70 - 8.4) / font);
  let reservedLines = Math.max(lines.length, count <= 6 ? 1 : count <= 16 ? 2 : 3);
  // A short middle word can require its own line before a wider, longer draft
  // permits two lines again. Retain that earlier height instead of shrinking.
  if (reservedLines < 3) for (let end = 7; end < count; end++) {
    const prefix = characters(content).slice(0, end).join('').trim();
    if (wrapCloudText(prefix, (widthFor(prefix) * .70 - 8.4) / font).length === 3) {
      reservedLines = 3;
      break;
    }
  }
  // Leave room for the rounded lower edge as well as the upper cloud lobes.
  // Common text-safe rectangle: x90..510,y80..220 on a 600x260 canvas.
  const height = Math.max(width * (.425 + (seed % 5) * .012), (reservedLines * font * 1.16 + 10) / (140 / 260));
  // Keep an individual pace for this message, independent of text length or theme.
  const speed = 16 + randomFrom(seed ^ 0x9e3779b9)() * 6;
  return { seed, lines, font, width, height, speed, shape: CLOUD_VARIANTS[seed % CLOUD_VARIANTS.length], flipped: Boolean(seed & 16) };
}

function textRect(cloud, padding = 11) {
  return { x: cloud.x + cloud.width * .15 - padding, y: cloud.y + cloud.height * (150 / 260) - cloud.lines.length * cloud.font * .58 - padding,
    w: cloud.width * .70 + padding * 2, h: cloud.lines.length * cloud.font * 1.16 + padding * 2 };
}
function overlap(a, b, shift = 0) {
  return Math.max(0, Math.min(a.x + a.w, b.x + shift + b.w) - Math.max(a.x, b.x + shift))
    * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
}

const cloudBody = cloud => ({ x:cloud.x + cloud.width * .06, y:cloud.y + cloud.height * .22, w:cloud.width * .88, h:cloud.height * .74 });

// Unaligned positions, with a slightly different steady speed for each cloud.
// Consider near-future crossings as well as the starting arrangement. This reduces
// obscured text without forcing equal speeds, lanes, or moving existing clouds.
export function layoutClouds(items, previous = new Map(), elapsed = 0) {
  const placed = [], result = new Map();
  const footprints = new Map();
  const remember = cloud => {
    placed.push(cloud); result.set(cloud.id, cloud);
    footprints.set(cloud.id, { text:textRect(cloud), body:cloudBody(cloud) });
  };
  for (const item of items) {
    const existing = previous.get(item.id);
    if (existing?.content === item.content) remember({ ...existing, ...cloudMetrics(item) });
  }
  const additions = items.filter(item => !result.has(item.id)).map(item => ({ ...cloudMetrics(item), id: item.id, content: item.content }))
    .sort((a, b) => b.width * b.height - a.width * a.height || a.seed - b.seed);
  for (const metrics of additions) {
    const random = randomFrom(metrics.seed);
    let best = null, bestScore = Infinity;
    for (let attempt = 0; attempt < 400; attempt++) {
      const candidate = { ...metrics,
        x: items.length <= 3 ? wrapPosition(1030 + random() * 620 - metrics.speed * elapsed) : random() * CLOUD_LOOP_WIDTH,
        y: 185 + random() * Math.max(0, WALL_HEIGHT - 211 - metrics.height),
      };
      const rect = textRect(candidate);
      const body = cloudBody(candidate);
      let score = 0;
      for (const other of placed) {
        // Nothing can be hidden when the full vertical ranges do not intersect.
        if (candidate.y + candidate.height + 12 < other.y || other.y + other.height + 12 < candidate.y) continue;
        const { text:otherRect, body:otherBody } = footprints.get(other.id);
        for (const [future, weight] of MOTION_SAMPLES) {
          const distance = wrapPosition(other.x - candidate.x + (other.speed - candidate.speed) * (elapsed + future));
          const relativeShift = candidate.x + distance - other.x;
          for (const offset of [0, -CLOUD_LOOP_WIDTH]) {
            const shift = relativeShift + offset;
            score += overlap(rect, otherRect, shift) * 40 * weight;
            // A lower cloud is drawn in front; its body should avoid the text above.
            score += (candidate.y >= other.y ? overlap(body, otherRect, shift) : overlap(rect, otherBody, shift)) * 6 * weight;
            if (future === 0) score += overlap({x:candidate.x,y:candidate.y,w:candidate.width,h:candidate.height},
              {x:other.x,y:other.y,w:other.width,h:other.height},shift) * .025;
          }
        }
      }
      score += random() * 12;
      if (score < bestScore) { best = candidate; bestScore = score; }
    }
    remember(best);
  }
  return items.map(item => ({ ...result.get(item.id), item }));
}
