import metadata from './assets/clouds.json';
import cloud01 from './assets/cloud-01.svg';
import cloud02 from './assets/cloud-02.svg';
import cloud03 from './assets/cloud-03.svg';
import cloud04 from './assets/cloud-04.svg';
import cloud05 from './assets/cloud-05.svg';
import cloud06 from './assets/cloud-06.svg';
import cloud07 from './assets/cloud-07.svg';
import cloud08 from './assets/cloud-08.svg';
import cloud09 from './assets/cloud-09.svg';
import cloud10 from './assets/cloud-10.svg';
import cloud11 from './assets/cloud-11.svg';
import cloud12 from './assets/cloud-12.svg';
import cloud13 from './assets/cloud-13.svg';
import cloud14 from './assets/cloud-14.svg';
import cloud15 from './assets/cloud-15.svg';
import cloud16 from './assets/cloud-16.svg';
import { seedFor } from './themes.js';

const assets = [cloud01, cloud02, cloud03, cloud04, cloud05, cloud06, cloud07, cloud08,
  cloud09, cloud10, cloud11, cloud12, cloud13, cloud14, cloud15, cloud16];
export const CLOUD_SHAPES = Object.freeze(metadata.map((shape, index) => Object.freeze({ ...shape, url: assets[index] })));
// The TU15Pro LED player browser predates Intl.Segmenter (Chrome 87).
// Fall back to code points, as student/graphemes.js already does.
const graphemes = typeof Intl.Segmenter === 'function'
  ? new Intl.Segmenter('ko', { granularity: 'grapheme' })
  : { segment: value => Array.from(value, segment => ({ segment })) };

/** The design text boxes use an unpadded 1000×height canvas; SVG files add 10px on each edge. */
export function cloudGeometry(item) {
  const seed = seedFor(item.sceneSeed ?? item.id);
  const shape = CLOUD_SHAPES[seed % CLOUD_SHAPES.length];
  const flipped = Boolean(seed & 16);
  const left = (10 + (flipped ? 1 - shape.textBox.right : shape.textBox.left) * 1000) / 1020;
  const textWidth = (shape.textBox.right - shape.textBox.left) * 1000 / 1020;
  const centerY = (10 + shape.textBox.centerY * shape.viewBoxHeight) / (shape.viewBoxHeight + 20);
  // One user-visible emoji may contain several joined code points. Keep it one
  // width unit, and reserve a full em for ASCII too so wide W/M never spill out.
  const units = [...graphemes.segment(item.content)].reduce((sum, { segment }) => sum + (/^\s+$/u.test(segment) ? .3 : 1), 0);
  const width = Math.max(280, Math.min(900, (units + 1.5) * 24 / textWidth));
  const fontSize = Math.min(24, width * textWidth / Math.max(1, units + .8));
  return { shape, seed, flipped, left, textWidth, centerY, width, fontSize,
    height: Math.min(160, Math.max(105, width * (shape.viewBoxHeight + 20) / 1020)) };
}
