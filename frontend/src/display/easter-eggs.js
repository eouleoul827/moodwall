// Display-side easter eggs. Only messages that already passed moderation reach the
// display, so eggs inherit that check. State is kept per display in localStorage so a
// reload keeps today's count and cooldowns; if storage is unavailable it stays in memory.

export const EGG_COOLDOWN_MS = 10 * 60 * 1000;
export const HUNDRED_STEP = 100;
export const WAVE_STREAK = 5;
const STORAGE_KEY = 'moodwall.easterEggs.v1';
const REMEMBERED_IDS = 30;
const KEYWORDS = /제이제이|무드월/;
// Dancing or fireworks on a sad or angry message would feel wrong, so those wait.
const BRIGHT = new Set(['celebration', 'joy', 'support', 'excitement', 'neutral']);

export const EGG_LABELS = Object.freeze({
  jj: () => '제이제이 등장!',
  hundred: egg => `오늘 ${egg.count}번째 멘션 돌파!`,
  wave: () => '응원 파도타기!',
});

export function businessDate(now) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(now));
}

function browserStorage() {
  try { return globalThis.localStorage ?? null; } catch { return null; }
}

function freshState(date) {
  return { date, count: 0, celebrated: 0, streak: 0, last: {}, recent: [] };
}

export function createEasterEggs({ storage = browserStorage(), clock = () => Date.now() } = {}) {
  let state = null;

  const load = now => {
    const today = businessDate(now);
    if (!state) {
      try {
        const saved = JSON.parse(storage?.getItem(STORAGE_KEY) || 'null');
        if (saved && typeof saved === 'object' && Number.isInteger(saved.count)) state = { ...freshState(saved.date), ...saved };
      } catch { /* A corrupt or blocked entry simply starts over. */ }
    }
    if (!state || state.date !== today) state = { ...freshState(today), last: state?.last || {} };
    return state;
  };
  const save = () => {
    try { storage?.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* memory only */ }
  };
  const ready = (kind, now) => !(now - (state.last[kind] || 0) < EGG_COOLDOWN_MS);

  /** Call once when a message goes full screen. Returns null or { kind, count? }. */
  function next(item) {
    const now = clock();
    load(now);
    const id = String(item?.id ?? '');
    const seen = state.recent.find(entry => entry.id === id);
    if (id && seen) return seen.egg;            // a re-shown message keeps its first decision

    state.count += 1;
    state.streak = item?.sceneId === 'support' ? state.streak + 1 : 0;
    const bright = BRIGHT.has(item?.sceneId);
    const compact = String(item?.content ?? '').replace(/\s+/g, '');
    let egg = null;

    const milestone = state.celebrated + HUNDRED_STEP;
    if (bright && state.count >= milestone) {
      egg = { kind: 'hundred', count: milestone };
      state.celebrated = milestone;
    } else if (bright && KEYWORDS.test(compact) && ready('jj', now)) {
      egg = { kind: 'jj' };
    } else if (state.streak >= WAVE_STREAK && ready('wave', now)) {
      egg = { kind: 'wave' };
    }
    // Five in a row is used up either way, so the next wave needs a fresh five.
    if (state.streak >= WAVE_STREAK) state.streak = 0;
    if (egg) state.last[egg.kind] = now;
    if (id) state.recent = [...state.recent, { id, egg }].slice(-REMEMBERED_IDS);
    save();
    return egg;
  }

  return { next };
}
