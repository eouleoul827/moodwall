import { moodOf } from './generator.js';

// Only checked-in files become URLs. Vite emits hashed /assets/ paths served by
// the backend, never a student-controlled URL or an external image endpoint.
const MASCOT_URLS = Object.fromEntries(
  Object.entries(import.meta.glob('./mascot/*.webp', { eager: true, query: '?url', import: 'default' }))
    .map(([path, url]) => [path.slice(path.lastIndexOf('/') + 1), url]),
);
const EXPRESSION = Object.freeze({
  응원: 'cheer', 축하: 'celebration', 기쁨: 'joy', 설렘: 'excited',
  피곤: 'tired', 슬픔: 'sad', 화남: 'angry', 일반: 'neutral',
});
const readyURLs = new Set();

export const assetUrl = file => Object.prototype.hasOwnProperty.call(MASCOT_URLS, file) ? MASCOT_URLS[file] : '';
const failure = () => Object.assign(new Error('BACKGROUND_ASSET_UNAVAILABLE'), { code: 'ASSET_FAILURE' });
const aborted = signal => signal?.reason || new DOMException('Background preparation cancelled', 'AbortError');

function prepareImage(url, signal) {
  if (signal?.aborted) return Promise.reject(aborted(signal));
  if (readyURLs.has(url)) return Promise.resolve();
  if (!url) return Promise.reject(failure());
  return new Promise((resolve, reject) => {
    const image = new Image();
    let settled = false;
    const finish = error => {
      if (settled) return;
      settled = true;
      image.onload = null;
      image.onerror = null;
      signal?.removeEventListener('abort', cancel);
      if (error) reject(error);
      else { readyURLs.add(url); resolve(); }
    };
    const cancel = () => {
      finish(aborted(signal));
      image.src = '';
    };
    image.onerror = () => finish(failure());
    image.onload = async () => {
      try {
        if (typeof image.decode === 'function') await image.decode();
        if (signal?.aborted) finish(aborted(signal));
        else finish();
      } catch { finish(failure()); }
    };
    signal?.addEventListener('abort', cancel, { once: true });
    image.src = url;
  });
}

export async function prepareMoodBackground(sceneId, { signal } = {}) {
  if (signal?.aborted) throw aborted(signal);
  const mood = moodOf(sceneId);
  const expression = Object.prototype.hasOwnProperty.call(EXPRESSION, mood) ? EXPRESSION[mood] : 'neutral';
  // Aspect/seed can choose either friend when the scene is finally rendered.
  await Promise.all([`jj-3d-${expression}.webp`, 'byeoli-3d.webp', 'jeit-3d.webp']
    .map(file => prepareImage(assetUrl(file), signal)));
}
