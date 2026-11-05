import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CLOUD_SHAPES, cloudGeometry } from './cloud-shapes.js';
import CloudMessage from './CloudMessage.jsx';
import { DISPLAY_PALETTES, periodFor, themeFor, THEMES } from './themes.js';

describe('reviewed display design', () => {
  it('uses Korea time for both day/night boundaries regardless of the device time zone', () => {
    expect(periodFor(Date.parse('2026-11-04T20:59:59Z'))).toBe('night');
    expect(periodFor(Date.parse('2026-11-04T21:00:00Z'))).toBe('day');
    expect(periodFor(Date.parse('2026-11-05T08:59:59Z'))).toBe('day');
    expect(periodFor(Date.parse('2026-11-05T09:00:00Z'))).toBe('night');
  });
  it('retains the seven design colours and supplies a distinct neutral palette in both periods', () => {
    expect(Object.keys(THEMES)).toHaveLength(8);
    expect(THEMES.joy).toMatchObject({ color: '#FF5C8A', day: '#FFB0C6', night: '#7A2C4C' });
    expect(themeFor({ sceneId: 'neutral' }, 'night').tint).toBe('#514A3C');
    expect(themeFor({ sceneId: 'untrusted-css' }).color).toBe(THEMES.neutral.color);
    expect(themeFor({ sceneId: '__proto__' }).color).toBe(THEMES.neutral.color);
    expect(themeFor({ sceneId: 'constructor' }).color).toBe(THEMES.neutral.color);
    expect(DISPLAY_PALETTES.day.panel).toBe('#FFFFFF');
    expect(DISPLAY_PALETTES.night.panel).toBe('#171C2E');
    for (const theme of Object.values(THEMES)) {
      expect(theme.day).toMatch(/^#[\da-f]{6}$/i);
      expect(theme.night).toMatch(/^#[\da-f]{6}$/i);
      expect(theme.day).not.toBe(theme.night);
    }
  });
  it('selects all sixteen local shapes deterministically and keeps padded, mirrored text inside each cloud', () => {
    const seen = new Set();
    for (let index = 0; index < 256; index++) {
      const item = { id: String(index), sceneSeed: `scene-${index}`, content: '기분을 나누는 서른 글자의 구름도 잘 읽혀야 해요' };
      const geometry = cloudGeometry(item);
      expect(cloudGeometry(item)).toEqual(geometry);
      seen.add(geometry.shape.file);
      expect(CLOUD_SHAPES).toContain(geometry.shape);
      expect(geometry.left).toBeGreaterThan(0);
      expect(geometry.left + geometry.textWidth).toBeLessThan(1);
      expect(geometry.centerY).toBeGreaterThan(.5);
      expect(geometry.centerY).toBeLessThan(.7);
      expect(geometry.fontSize).toBeGreaterThan(14);
      expect(geometry.width).toBeLessThanOrEqual(900);
      const expectedLeft = (10 + (geometry.flipped ? 1 - geometry.shape.textBox.right : geometry.shape.textBox.left) * 1000) / 1020;
      expect(geometry.left).toBeCloseTo(expectedLeft);
    }
    expect(seen.size).toBe(16);
  });
  it('renders content as text and chooses mask URLs only from checked-in assets', () => {
    const content = '<img src=x onerror=alert(1)>';
    const { container } = render(<CloudMessage item={{ content, id: 'https://evil.invalid/mask.svg', sceneId: 'joy' }} period="night" />);
    expect(screen.getByText(content)).toBeTruthy();
    expect(container.querySelector('img')).toBeNull();
    const bubble = container.querySelector('.mw-bubble');
    expect(bubble.style.getPropertyValue('--cloud-tint')).toBe('#7A2C4C');
    expect(bubble.style.getPropertyValue('--cloud-mask')).not.toContain('evil.invalid');
    expect(bubble.dataset.cloudShape).toMatch(/^cloud-\d{2}\.svg$/);
  });
  it('sizes thirty joined emoji or combining graphemes like thirty visible characters', () => {
    const base = { id: 'grapheme-boundary', sceneSeed: 'same-shape' };
    const expected = cloudGeometry({ ...base, content: '가'.repeat(30) });
    for (const content of ['👨‍👩‍👧‍👦'.repeat(30), 'a\u0301'.repeat(30)]) {
      const actual = cloudGeometry({ ...base, content });
      expect(actual.width).toBe(expected.width);
      expect(actual.fontSize).toBe(expected.fontSize);
      expect(actual.fontSize).toBeGreaterThan(18);
    }
  });
  it('reserves enough text-box width for thirty wide ASCII letters', () => {
    for (const content of ['W'.repeat(30), 'M'.repeat(30)]) {
      const cloud = cloudGeometry({ id: 'wide-ascii-boundary', content });
      expect(cloud.fontSize * 30).toBeLessThan(cloud.width * cloud.textWidth);
    }
  });
});
