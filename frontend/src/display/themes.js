// Scene IDs and colours are reviewed local data; model output never becomes CSS or a URL.
export const THEMES = Object.freeze({
  celebration: { label: '축하', color: '#FF7A3D', day: '#FFBE9E', night: '#7A3F24', effect: 'fireworks', forecast: '축하가 모이고 있어요' },
  joy: { label: '기쁨', color: '#FF5C8A', day: '#FFB0C6', night: '#7A2C4C', effect: 'hearts', forecast: '웃음이 모이고 있어요' },
  support: { label: '응원', color: '#3D7BFF', day: '#AFC6FF', night: '#2C4A99', effect: 'sparkles', forecast: '응원이 모이고 있어요' },
  excitement: { label: '설렘', color: '#B66BFF', day: '#D8B6FF', night: '#583A9C', effect: 'petals', forecast: '설렘이 모이고 있어요' },
  tired: { label: '피곤', color: '#8A8F99', day: '#C9CDD6', night: '#424860', effect: 'clouds', forecast: '잠깐 쉬어가도 괜찮아요' },
  sadness: { label: '슬픔', color: '#4FA3C7', day: '#9FD3EC', night: '#1E5F80', effect: 'rain', forecast: '토닥토닥이 필요한 시간' },
  anger: { label: '화남', color: '#E0453A', day: '#F6A79F', night: '#7E2C26', effect: 'embers', forecast: '숨을 고르는 시간' },
  neutral: { label: '일상', color: '#A89B7D', day: '#DDD5C1', night: '#514A3C', effect: 'float', forecast: '여러 기분이 머무는 곳' },
});

export const DISPLAY_PALETTES = Object.freeze({
  day: { bg: '#F2EFE8', panel: '#FFFFFF', ink: '#14161B', sub: '#4B505B', line: '#C9C3B6', track: '#ECE8DF', skyTop: '#5FB4F0', skyBottom: '#CDEBFC', cloudInk: '#2E323B', shadow: 'rgba(20,22,27,.12)' },
  night: { bg: '#0D1120', panel: '#171C2E', ink: '#F5F3EE', sub: '#B5BBC8', line: '#3A4260', track: '#262C42', skyTop: '#0A1430', skyBottom: '#1D2E5C', cloudInk: '#F5F3EE', shadow: 'rgba(0,0,0,.35)' },
});

/** A visual clock theme only: it does not claim to report real weather. */
export function periodFor(now) {
  const hour = Number(new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Seoul', hour: '2-digit', hour12: false }).format(now));
  return hour >= 6 && hour < 18 ? 'day' : 'night';
}

export function themeFor(item, period = 'day') {
  const theme = Object.hasOwn(THEMES, item?.sceneId) ? THEMES[item.sceneId] : THEMES.neutral;
  return { ...theme, tint: theme[period === 'night' ? 'night' : 'day'] };
}

export function seedFor(value = '') {
  let seed = 2166136261;
  for (const char of String(value)) seed = Math.imul(seed ^ char.codePointAt(0), 16777619);
  return seed >>> 0;
}

export function particleStyles(seed, index) {
  let value = (seed + Math.imul(index + 1, 2654435761)) >>> 0;
  const random = () => {
    value ^= value << 13; value ^= value >>> 17; value ^= value << 5;
    return (value >>> 0) / 4294967296;
  };
  return {
    '--x': `${random() * 100}%`, '--y': `${random() * 95}%`,
    '--delay': `${-random() * 8}s`, '--duration': `${3.5 + random() * 4}s`,
    '--size': `${18 + random() * 36}px`, '--rotation': `${random() * 360}deg`,
  };
}
