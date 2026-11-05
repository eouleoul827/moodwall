// All scenes are local, reviewed assets. Model output never becomes a URL or CSS.
export const THEMES = Object.freeze({
  celebration: { label: '축하', color: '#f18749', tint: '#fff0df', effect: 'fireworks', weather: '축하할 일이 생겼나 봐요' },
  joy: { label: '기쁨', color: '#eb7993', tint: '#ffedf1', effect: 'hearts', weather: '웃음이 몽글몽글' },
  support: { label: '응원', color: '#7497d5', tint: '#eaf0fc', effect: 'sparkles', weather: '따뜻한 응원 바람' },
  excitement: { label: '설렘', color: '#a68acb', tint: '#f3ecfb', effect: 'petals', weather: '설렘이 살랑이는 날' },
  tired: { label: '피곤', color: '#98a09e', tint: '#edf0ec', effect: 'clouds', weather: '잠깐 쉬어가도 괜찮아요' },
  sadness: { label: '슬픔', color: '#7fa8bd', tint: '#e9f1f5', effect: 'rain', weather: '토닥토닥, 마음에 작은 우산' },
  anger: { label: '화남', color: '#cc8170', tint: '#f9ebe5', effect: 'embers', weather: '숨을 고르는 시간' },
  neutral: { label: '일상', color: '#b6aa86', tint: '#f3f0e7', effect: 'float', weather: '서로 다른 기분이 모이는 곳' },
});

export function themeFor(item) {
  return THEMES[item?.sceneId] || THEMES.neutral;
}

export function seedFor(value = '') {
  let seed = 2166136261;
  for (const char of value) seed = Math.imul(seed ^ char.codePointAt(0), 16777619);
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
