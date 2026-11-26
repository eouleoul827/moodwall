// @vitest-environment node
import assert from 'node:assert/strict';
import { describe, expect, it } from 'vitest';
import { advanceCloudMotion, reconcileCloudMotion, MOTION } from './cloud-motion.mjs';

const DT = 1 / 30;
const wrap = value => ((value % MOTION.loop) + MOTION.loop) % MOTION.loop;
const cloud = (id, seed, overrides = {}) => ({ id, seed, content: '같은 속도로 겹치는 구름',
  x: 1000, y: 350, width: 300, height: 160, font: 28, lines: ['계속 가려지지', '않도록 움직여'], speed: 19, ...overrides });
const intersection = (a, b) => ({ x: Math.max(a.x, b.x), y: Math.max(a.y, b.y),
  w: Math.max(0, Math.min(a.x+a.w, b.x+b.w)-Math.max(a.x,b.x)),
  h: Math.max(0, Math.min(a.y+a.h, b.y+b.h)-Math.max(a.y,b.y)) });

// Independent text/body rectangle estimate; overlapping foreground covers are
// unioned so the same hidden area is not counted twice for a three-cloud group.
function worstCoverage(states) {
  const entries = [...states.values()].map((item, index) => {
    const height = item.lines.length * item.font * 1.16;
    return { item, index, text: { x: item.x+item.width*.15, y: item.y+item.height*(150/260)-height/2, w: item.width*.7, h: height },
      body: { x: item.x+item.width*.08, y: item.y+item.height*.28, w: item.width*.84, h: item.height*.65 } };
  });
  return Math.max(...entries.map(({ item, index, text }) => {
    const covers = entries.filter(other => other.item.layer > item.layer || other.item.layer === item.layer && other.index > index)
      .map(other => intersection(text, other.body));
    const sum = covers.reduce((area, rect) => area + rect.w*rect.h, 0);
    const duplicate = covers.length === 2 ? intersection(covers[0], covers[1]) : { w: 0, h: 0 };
    return (sum-duplicate.w*duplicate.h)/(text.w*text.h);
  }));
}

describe('continuous cloud motion', () => {
  it.each([2, 3])('gradually uncovers %i coincident clouds travelling at the same speed', count => {
    const input = Array.from({ length: count }, (_, i) => cloud(`exact-${i}`, i+10));
    const states = reconcileCloudMotion(input), layers = [...states.values()].map(item => item.layer);
    expect(worstCoverage(states)).toBeGreaterThan(.99);
    let uncoveredAt = null;
    for (let step = 1; step <= 1800; step++) {
      advanceCloudMotion(states, DT);
      if (uncoveredAt === null && worstCoverage(states) < .9) uncoveredAt = step / 30;
    }
    expect(uncoveredAt).not.toBeNull();
    expect(uncoveredAt).toBeLessThan(60);
    expect(worstCoverage(states)).toBeLessThan(.9);
    expect([...states.values()].map(item => item.layer)).toEqual(layers);
    for (const item of states.values()) {
      expect(item.opacity).toBeUndefined();
      expect(item.content).toBe(input[0].content);
    }
  });

  it('bounds speed, acceleration and screen edges while clouds wrap and pass one another', () => {
    const input = Array.from({ length: 30 }, (_, i) => cloud(`busy-${i}`, i+70, {
      x: i < 8 ? 3399 : i*113, y: i < 4 ? 185 : i < 8 ? 582 : 185+(i%7)*56,
      speed: 16+i*6/30,
    }));
    const states = reconcileCloudMotion(input);
    let maxAcceleration = 0, maxEdgeCorrection = 0, maxSideSpeed = 0, maxSpeedDeviation = 0, maxPositionError = 0;
    for (let step = 0; step < 2700; step++) {
      const old = new Map([...states].map(([id, item]) => [id, { ...item }]));
      advanceCloudMotion(states, DT);
      for (const [id, item] of states) {
        const prior = old.get(id);
        assert(Number.isFinite(item.x+item.y+item.vx+item.vy));
        assert(item.x >= 0 && item.x < MOTION.loop);
        assert(item.y >= MOTION.minY-1e-8 && item.y+item.height <= MOTION.bottom+1e-8);
        assert.equal(item.layer, prior.layer);
        // The final boundary guard can zero a tiny residual outward velocity.
        // Check that correction separately from normal steering acceleration.
        const edgeStop = item.vy === 0 && (item.y === MOTION.minY && prior.vy < 0 || item.y+item.height === MOTION.bottom && prior.vy > 0);
        if (edgeStop) maxEdgeCorrection = Math.max(maxEdgeCorrection, Math.abs(prior.vy));
        else maxAcceleration = Math.max(maxAcceleration, Math.hypot(item.vx-prior.vx,item.vy-prior.vy)/DT);
        maxSideSpeed = Math.max(maxSideSpeed, Math.abs(item.vy));
        maxSpeedDeviation = Math.max(maxSpeedDeviation, Math.abs(item.vx-item.speed));
        maxPositionError = Math.max(maxPositionError, Math.abs(wrap(item.x-prior.x)-item.vx*DT));
      }
    }
    expect(maxAcceleration).toBeLessThanOrEqual(MOTION.maxAcceleration+1e-7);
    expect(maxEdgeCorrection).toBeLessThan(.05); // < .002px of travel in one 30Hz frame.
    expect(maxSideSpeed).toBeLessThanOrEqual(MOTION.maxSideSpeed+1e-8);
    expect(maxSpeedDeviation).toBeLessThanOrEqual(MOTION.speedAllowance+1e-8);
    expect(maxPositionError).toBeLessThan(1e-8);
  }, 10000);

  it('keeps motion and fixed layer order across additions, removals and emotion updates', () => {
    const original = [cloud('keep-a', 1), cloud('keep-b', 2, { x: 1450, y: 410 })];
    const copy = JSON.stringify(original), initial = reconcileCloudMotion(original);
    advanceCloudMotion(initial, 7);
    expect(JSON.stringify(original)).toBe(copy);
    const old = JSON.stringify([...initial]);
    const added = cloud('new-c', 3, { x: 2100, y: 200 });
    const next = reconcileCloudMotion([...original.map(item => ({ ...item, sceneId: 'joy' })), added], initial);
    for (const id of ['keep-a', 'keep-b']) {
      for (const key of ['x', 'y', 'vx', 'vy', 'homeY', 'age', 'layer']) expect(next.get(id)[key]).toBe(initial.get(id)[key]);
      expect(next.get(id).sceneId).toBe('joy');
    }
    expect(JSON.stringify([...initial])).toBe(old);
    expect(next.get('new-c')).toMatchObject({ age: 0, vx: added.speed, vy: 0 });
    expect([...reconcileCloudMotion([original[1]], next).keys()]).toEqual(['keep-b']);
    expect(advanceCloudMotion(initial, 0)).toBe(initial);
    expect(advanceCloudMotion(initial, -1)).toBe(initial);
    expect(JSON.stringify([...initial])).toBe(old);
  });

  it('chooses the same separation directions after input reordering', () => {
    const input = [cloud('order-a', 25), cloud('order-b', 27), cloud('order-c', 29)];
    const forward = reconcileCloudMotion(input), reverse = reconcileCloudMotion([...input].reverse());
    advanceCloudMotion(forward, 10); advanceCloudMotion(reverse, 10);
    for (const [id, item] of forward) for (const key of ['x', 'y', 'vx', 'vy']) {
      expect(item[key]).toBeCloseTo(reverse.get(id)[key], 7);
    }
  });
});
