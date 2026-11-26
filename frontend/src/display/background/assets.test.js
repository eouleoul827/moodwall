import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let images, prepareMoodBackground, assetUrl;
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

beforeEach(async () => {
  vi.resetModules();
  images = [];
  vi.stubGlobal('Image', class {
    constructor() {
      this.decoding = deferred();
      this.decode = vi.fn(() => this.decoding.promise);
      images.push(this);
    }
    load() { this.onload?.(); }
    fail() { this.onerror?.(); }
  });
  ({ prepareMoodBackground, assetUrl } = await import('./assets.js'));
});
afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });

describe('mood background asset preparation', () => {
  it('waits for all selected images to load and decode before marking them ready', async () => {
    let done = false;
    const prepared = prepareMoodBackground('joy').then(() => { done = true; });
    expect(images).toHaveLength(3);
    expect(images.map(image => image.src)).toEqual([
      assetUrl('jj-3d-joy.webp'), assetUrl('byeoli-3d.webp'), assetUrl('jeit-3d.webp'),
    ]);
    for (const image of images) image.load();
    images[0].decoding.resolve(); images[1].decoding.resolve();
    await Promise.resolve(); await Promise.resolve();
    expect(done).toBe(false);
    images[2].decoding.resolve();
    await prepared;
    expect(done).toBe(true);
    await prepareMoodBackground('joy');
    expect(images).toHaveLength(3);
    const next = prepareMoodBackground('sadness');
    expect(images).toHaveLength(4);
    expect(images[3].src).toBe(assetUrl('jj-3d-sad.webp'));
    images[3].load(); images[3].decoding.resolve();
    await next;
  });

  it.each(['load', 'decode'])('does not cache a failed %s and retries it on the next preparation', async kind => {
    const prepared = prepareMoodBackground('support');
    const rejected = expect(prepared).rejects.toMatchObject({ code: 'ASSET_FAILURE' });
    for (const image of images.slice(1)) { image.load(); image.decoding.resolve(); }
    if (kind === 'load') images[0].fail();
    else { images[0].load(); images[0].decoding.reject(new Error('decode failed')); }
    await rejected;
    const retry = prepareMoodBackground('support');
    expect(images).toHaveLength(4);
    expect(images[3].src).toBe(assetUrl('jj-3d-cheer.webp'));
    images[3].load(); images[3].decoding.resolve();
    await retry;
    for (const image of images) { expect(image.onload).toBeNull(); expect(image.onerror).toBeNull(); }
  });

  it.each(['loading', 'decoding'])('cancels during %s, removes callbacks, and ignores late completion', async stage => {
    const controller = new AbortController();
    const prepared = prepareMoodBackground('celebration', { signal: controller.signal });
    const rejected = expect(prepared).rejects.toMatchObject({ name: 'AbortError' });
    const cancelled = [...images];
    if (stage === 'decoding') for (const image of cancelled) image.load();
    controller.abort();
    await rejected;
    for (const image of cancelled) {
      expect(image.src).toBe('');
      expect(image.onload).toBeNull(); expect(image.onerror).toBeNull();
      image.decoding.resolve();
    }
    await Promise.resolve();
    const retry = prepareMoodBackground('celebration');
    expect(images).toHaveLength(6);
    for (const image of images.slice(3)) { image.load(); image.decoding.resolve(); }
    await retry;
  });

  it('rejects a pre-aborted request without image work, including a cached scene', async () => {
    const ready = prepareMoodBackground('neutral');
    for (const image of images) { image.load(); image.decoding.resolve(); }
    await ready;
    const controller = new AbortController(); controller.abort(new Error('no longer visible'));
    await expect(prepareMoodBackground('neutral', { signal: controller.signal })).rejects.toThrow('no longer visible');
    expect(images).toHaveLength(3);
  });

  it('uses only local mapped assets and falls back safely for an unknown emotion', async () => {
    expect(assetUrl('https://evil.invalid/image.webp')).toBe('');
    expect(assetUrl('__proto__')).toBe('');
    const prepared = prepareMoodBackground('__proto__');
    expect(images[0].src).toBe(assetUrl('jj-3d-neutral.webp'));
    for (const image of images) { image.decode = undefined; image.load(); }
    await prepared;
  });
});
