// @vitest-environment node
import assert from 'node:assert/strict';
import { describe, expect, it, vi } from 'vitest';
import { cloudMetrics, layoutClouds, wrapCloudText } from './cloud-layout.mjs';
import { CLOUD_VARIANTS } from './cloud-variants.mjs';

const segmenter = new Intl.Segmenter('ko', { granularity: 'grapheme' });
const chars = value => [...segmenter.segment(value)].map(part => part.segment);
const normalize = value => value.normalize('NFC').replace(/\s+/gu, ' ').trim();
const contentOnly = value => normalize(value).replace(/ /g, '');
const units = value => chars(value).reduce((sum, char) => sum + (char === ' ' ? .34 : 1), 0);
const messages = ['잠깐 쉬어도 괜찮아', '오늘도 정말 수고했어', '우리 함께 천천히 가보자',
  '괜찮아 너는 잘하고 있어', '나도 네 곁에 있을게', '오늘 함께해 줘서 고마워'];

function wholeWordsFit(value, capacity) {
  let lines = 1, used = 0;
  for (const word of normalize(value).split(' ')) {
    const width = units(word);
    if (width > capacity + 1e-6) return false;
    if (used && used + .34 + width > capacity + 1e-6) { lines++; used = width; }
    else used += (used ? .34 : 0) + width;
  }
  return lines <= 3;
}

function checkMetrics(content, id) {
  const metrics = cloudMetrics({ content, id });
  const budget = (metrics.width * .70 - 8.4) / metrics.font;
  assert(metrics.lines.length <= 3, content);
  assert.equal(contentOnly(metrics.lines.join('')), contentOnly(content));
  assert(metrics.width >= 200 && metrics.width <= 440);
  assert(metrics.font >= 28 && metrics.font <= 29);
  for (const line of metrics.lines) assert(units(line) <= budget + 1e-6, line);
  assert(metrics.lines.length * metrics.font * 1.16 + 10 <= metrics.height * (140 / 260) + 1e-6);
  if (wholeWordsFit(content, budget)) assert.equal(metrics.lines.join(' '), normalize(content));
  return metrics;
}

// Sample the actual cubic outlines independently of their generating points.
function polygon(path) {
  const tokens = path.match(/[A-Za-z]|-?(?:\d*\.)?\d+/g), result = [];
  let offset = 0, current;
  while (offset < tokens.length) {
    const command = tokens[offset++];
    if (command === 'M') { current = [+tokens[offset++], +tokens[offset++]]; result.push(current); }
    else if (command === 'C') {
      const a = current, b = [+tokens[offset++], +tokens[offset++]], c = [+tokens[offset++], +tokens[offset++]], d = [+tokens[offset++], +tokens[offset++]];
      for (let n = 1; n <= 32; n++) {
        const t = n / 32, s = 1 - t;
        result.push([s**3*a[0] + 3*s*s*t*b[0] + 3*s*t*t*c[0] + t**3*d[0], s**3*a[1] + 3*s*s*t*b[1] + 3*s*t*t*c[1] + t**3*d[1]]);
      }
      current = d;
    } else assert.equal(command, 'Z');
  }
  return result;
}

function inside([x, y], points) {
  let result = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [ax, ay] = points[i], [bx, by] = points[j];
    if ((ay > y) !== (by > y) && x < (bx-ax)*(y-ay)/(by-ay)+ax) result = !result;
  }
  return result;
}

describe('rounded cloud text and placement', () => {
  it('keeps Korean words together and retains all visible text in at most three lines', () => {
    expect(wrapCloudText(messages[0])).toEqual(['잠깐 쉬어도', '괜찮아']);
    for (const message of messages) checkMetrics(message, 'word-example');
    let seed = 7831;
    const random = () => { seed = Math.imul(seed, 1664525) + 1013904223; return (seed >>> 0) / 4294967296; };
    const alphabet = ['가', '나', 'W', ' ', ' ', '👨‍👩‍👧‍👦', '🇰🇷', '!'];
    for (let i = 0; i < 180; i++) {
      const value = normalize(Array.from({ length: 1 + i % 30 }, () => alphabet[Math.floor(random() * alphabet.length)]).join(''));
      if (value) checkMetrics(value, `sample-${i}`);
    }
  });

  it('counts joined emoji, flags and combining characters as graphemes and truncates at thirty', () => {
    for (const unit of ['가', 'W', '👨‍👩‍👧‍👦', '🇰🇷', '👍🏽', '가', 'a\u0301']) {
      expect(wrapCloudText(unit.repeat(30)).join('')).toBe(unit.repeat(30).normalize('NFC'));
      expect(wrapCloudText(unit.repeat(35)).join('')).toBe(unit.repeat(29).normalize('NFC') + '…');
      checkMetrics(unit.repeat(30), `unicode-${unit}`);
    }
    expect(wrapCloudText(' \n\t ')).toEqual([]);
  });

  it('supports the LED browser without Intl.Segmenter', async () => {
    vi.resetModules();
    vi.stubGlobal('Intl', { Segmenter: undefined });
    try {
      const fallback = await import('./cloud-layout.mjs');
      for (const unit of ['👨‍👩‍👧‍👦', '🇰🇷', '👍🏽', 'a\u0301']) {
        expect(fallback.wrapCloudText(unit.repeat(30)).join('')).toBe(unit.repeat(30).normalize('NFC'));
        expect(fallback.wrapCloudText(unit.repeat(31)).join('')).toBe(unit.repeat(29).normalize('NFC') + '…');
      }
    } finally { vi.unstubAllGlobals(); vi.resetModules(); }
  });

  it('grows with appended text without reducing its font or changing its shape and wind speed', () => {
    const families = ['가'.repeat(30), 'W'.repeat(30), '👨‍👩‍👧‍👦'.repeat(30), '오늘도 너를 응원해 서로의 하루에 작은 기쁨을 더해'];
    for (const id of ['growth-a', 'growth-b', 'growth-c']) for (const family of families) {
      let previous;
      const source = chars(family);
      for (let n = 1; n <= Math.min(30, source.length); n++) {
        const metrics = checkMetrics(source.slice(0, n).join(''), id);
        if (previous) {
          assert(metrics.width >= previous.width && metrics.height >= previous.height, `${id}/${n}: cloud shrank`);
          assert.equal(metrics.font, previous.font);
          assert.equal(metrics.shape.id, previous.shape.id);
          assert.equal(metrics.speed, previous.speed);
        }
        previous = metrics;
      }
    }
  });

  it('keeps the complete shared text rectangle inside all eight cloud outlines', () => {
    expect(CLOUD_VARIANTS).toHaveLength(8);
    expect(new Set(CLOUD_VARIANTS.map(variant => variant.path)).size).toBe(8);
    for (const variant of CLOUD_VARIANTS) {
      const points = polygon(variant.path);
      for (let n = 0; n <= 40; n++) {
        const t = n / 40;
        for (const point of [[90+t*420,80], [90+t*420,220], [90,80+t*140], [510,80+t*140]]) {
          assert(inside(point, points), `${variant.id}: text rectangle escapes at ${point}`);
        }
      }
    }
  });

  it('gives thirty messages distinct speeds and preserves existing positions when another arrives', () => {
    const items = Array.from({ length: 30 }, (_, i) => ({ id: `cloud-${i+1}`, content: `${i+1}번째 한마디 오늘도 응원해`, sceneId: 'support' }));
    const layout = layoutClouds(items), previous = new Map(layout.map(cloud => [cloud.id, cloud]));
    expect(layout).toHaveLength(30);
    expect(new Set(layout.map(cloud => cloud.speed)).size).toBe(30);
    expect(new Set(layout.map(cloud => cloud.shape.id)).size).toBe(8);
    for (const cloud of layout) assert(cloud.speed >= 16 && cloud.speed <= 22);
    const added = { id: 'newest-cloud', content: '새로 찾아온 한마디', sceneId: 'joy' };
    const updated = layoutClouds([...items.slice(1).map(item => ({ ...item, sceneId: 'joy' })), added], previous);
    expect(updated.map(cloud => cloud.id)).toEqual([...items.slice(1).map(item => item.id), added.id]);
    for (const cloud of updated.slice(0, 29)) {
      const old = previous.get(cloud.id);
      for (const key of ['x', 'y', 'width', 'height', 'speed']) assert.equal(cloud[key], old[key]);
    }
  }, 10000);
});
