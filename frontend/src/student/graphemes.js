const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter('ko', { granularity: 'grapheme' }) : null;

export function graphemes(value) {
  return segmenter ? [...segmenter.segment(value)].map(part => part.segment) : Array.from(value);
}

export function characterCount(value) { return graphemes(value.normalize('NFC')).length; }

export function validateMention(value) {
  const normalized = value.trim().normalize('NFC');
  if (!segmenter) return '이 브라우저에서는 작성할 수 없어요. 최신 브라우저로 QR을 열어 주세요.';
  if (!normalized) return '지금 기분을 한 줄로 남겨 주세요.';
  if (/[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(normalized) || /[\p{Cf}]/u.test(normalized.replace(/\u200d/g, ''))) {
    return '줄바꿈 없이 한 줄로 작성해 주세요.';
  }
  if (!/[\p{L}\p{N}\p{P}\p{S}]/u.test(normalized.replace(/\p{Default_Ignorable_Code_Point}/gu, ''))) {
    return '지금 기분을 한 줄로 남겨 주세요.';
  }
  if (characterCount(normalized) > 30 || new TextEncoder().encode(normalized).length > 2048) {
    return '30자 이내로 작성해 주세요.';
  }
  return '';
}
