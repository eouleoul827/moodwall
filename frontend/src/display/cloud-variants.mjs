// A continuous, soft outline: broad upper lobes and smaller uneven lower lobes.
// The same tangent is used on both sides of every join, so crests have no tips.
// ViewBox 600 × 260; common text-safe rectangle x90..510, y80..220.
const BASE = [
  [26,162], [41,120], [79,105], [91,65], [133,45], [169,55],
  [191,23], [235,12], [272,28], [293,60], [329,36], [374,38],
  [405,66], [443,49], [483,53], [512,82], [522,116],
  [558,132], [575,165], [558,196], [526,210], [505,235],
  [470,251], [434,249], [410,233], [374,247], [337,255],
  [303,250], [277,235], [246,247], [209,255], [173,249],
  [146,239], [108,236], [75,225], [62,208], [36,194],
];

function smoothOutline(points) {
  const f = value => Math.round(value * 100) / 100;
  let path = `M${points[0].join(' ')}`;
  for (let index = 0; index < points.length; index++) {
    const a = points[(index + points.length - 1) % points.length];
    const b = points[index];
    const c = points[(index + 1) % points.length];
    const d = points[(index + 2) % points.length];
    const first = [b[0] + (c[0] - a[0]) / 6, b[1] + (c[1] - a[1]) / 6];
    const second = [c[0] - (d[0] - b[0]) / 6, c[1] - (d[1] - b[1]) / 6];
    path += ` C${[...first, ...second, ...c].map(f).join(' ')}`;
  }
  return `${path} Z`;
}

const SHAPES = [
  ['high-left', {}],
  ['wide-drift', {3:[90,62],4:[131,39],6:[193,38],7:[240,29],8:[278,38],10:[331,45],11:[378,43],14:[482,47]}],
  ['tall-right', {4:[131,55],5:[171,64],6:[193,44],7:[237,35],8:[274,45],9:[293,64],10:[329,18],11:[376,14],12:[408,54],13:[448,48]}],
  ['low-bank', {3:[91,61],4:[134,43],6:[194,43],7:[240,34],8:[278,43],10:[332,47],11:[376,46],13:[445,42],14:[482,48]}],
  ['rising-center', {4:[132,51],5:[170,62],6:[196,31],7:[247,6],8:[291,24],9:[312,57],10:[343,40],11:[382,44],12:[408,68]}],
  ['soft-step', {3:[89,60],4:[132,37],6:[191,29],7:[233,20],8:[270,38],10:[328,42],11:[374,45],13:[444,30],14:[483,35],15:[514,70]}],
  ['round-crown', {4:[130,48],5:[167,61],6:[187,28],7:[228,10],8:[270,18],9:[300,49],10:[337,26],11:[380,31],12:[410,61],13:[448,46]}],
  ['left-shoulder', {3:[90,49],4:[130,32],5:[169,49],6:[189,24],7:[230,14],8:[267,32],9:[288,63],10:[326,44],11:[371,45],13:[446,55],14:[484,57]}],
];

export const CLOUD_VARIANTS = SHAPES.map(([id, overrides]) => ({
  id,
  path: smoothOutline(BASE.map((point, index) => overrides[index] || point)),
}));
