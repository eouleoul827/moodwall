import { describe, expect, it } from 'vitest';
import { createEasterEggs, EGG_COOLDOWN_MS, EGG_LABELS } from './easter-eggs.js';

const at = (iso) => () => Date.parse(iso);
let n = 0;
const msg = (sceneId, content = '좋은 하루', id = `m${++n}`) => ({ id, sceneId, content });

function memory() {
  const data = new Map();
  return { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)) };
}

describe('display easter eggs', () => {
  it('celebrates every hundredth message of the day once, waiting for a bright one', () => {
    let now = Date.parse('2026-11-26T03:00:00Z');
    const eggs = createEasterEggs({ storage: null, clock: () => now });
    for (let i = 0; i < 99; i++) expect(eggs.next(msg('tired'))).toBeNull();
    expect(eggs.next(msg('sadness'))).toBeNull();            // 100th, but sad: wait
    const egg = eggs.next(msg('joy'));                        // 101st and bright
    expect(egg).toEqual({ kind: 'hundred', count: 100 });
    expect(EGG_LABELS.hundred(egg)).toBe('오늘 100번째 멘션 돌파!');
    expect(eggs.next(msg('joy'))).toBeNull();
    now = Date.parse('2026-11-26T16:00:00Z');                 // next day in Seoul resets the count
    for (let i = 0; i < 99; i++) eggs.next(msg('tired'));
    expect(eggs.next(msg('celebration'))).toEqual({ kind: 'hundred', count: 100 });
  });

  it('shows the JJ egg for the keyword on bright messages with a ten minute cooldown', () => {
    let now = Date.parse('2026-11-26T03:00:00Z');
    const eggs = createEasterEggs({ storage: null, clock: () => now });
    expect(eggs.next(msg('sadness', '제이제이 보고싶다'))).toBeNull();
    expect(eggs.next(msg('joy', '제 이 제 이 최고'))).toEqual({ kind: 'jj' });
    expect(eggs.next(msg('joy', '무드월 재밌다'))).toBeNull();
    now += EGG_COOLDOWN_MS;
    expect(eggs.next(msg('support', '무드월 화이팅'))).toEqual({ kind: 'jj' });
  });

  it('starts a wave after five support messages in a row and resets on a break', () => {
    let now = Date.parse('2026-11-26T03:00:00Z');
    const eggs = createEasterEggs({ storage: null, clock: () => now });
    for (let i = 0; i < 4; i++) expect(eggs.next(msg('support'))).toBeNull();
    expect(eggs.next(msg('joy'))).toBeNull();
    for (let i = 0; i < 4; i++) expect(eggs.next(msg('support'))).toBeNull();
    expect(eggs.next(msg('support'))).toEqual({ kind: 'wave' });
    for (let i = 0; i < 5; i++) expect(eggs.next(msg('support'))).toBeNull();   // cooldown
    now += EGG_COOLDOWN_MS;
    for (let i = 0; i < 4; i++) eggs.next(msg('support'));
    expect(eggs.next(msg('support'))).toEqual({ kind: 'wave' });
  });

  it('keeps the first decision for a re-shown message without counting it twice', () => {
    const eggs = createEasterEggs({ storage: null, clock: at('2026-11-26T03:00:00Z') });
    const first = msg('joy', '제이제이');
    expect(eggs.next(first)).toEqual({ kind: 'jj' });
    expect(eggs.next(first)).toEqual({ kind: 'jj' });
    expect(eggs.next(msg('joy', '제이제이'))).toBeNull();
  });

  it('remembers today across a reload and survives broken storage', () => {
    const storage = memory();
    const clock = at('2026-11-26T03:00:00Z');
    const first = createEasterEggs({ storage, clock });
    expect(first.next(msg('joy', '제이제이'))).toEqual({ kind: 'jj' });
    const reloaded = createEasterEggs({ storage, clock });
    expect(reloaded.next(msg('joy', '제이제이'))).toBeNull();          // cooldown kept
    const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
    expect(createEasterEggs({ storage: broken, clock }).next(msg('joy', '무드월'))).toEqual({ kind: 'jj' });
    storage.setItem('moodwall.easterEggs.v1', '{not json');
    expect(createEasterEggs({ storage, clock }).next(msg('joy', '무드월'))).toEqual({ kind: 'jj' });
  });
});
