import { cloudGeometry } from './cloud-shapes.js';
import { themeFor } from './themes.js';

/** Content stays escaped React text. Only the checked-in shape can set the mask URL. */
export default function CloudMessage({ item, period = 'day' }) {
  const cloud = cloudGeometry(item), theme = themeFor(item, period);
  return <div className="mw-bubble" data-cloud-shape={cloud.shape.file} style={{
    '--cloud-width': cloud.width / 24, '--cloud-height': cloud.height / 24,
    '--cloud-font': cloud.fontSize / 24, '--cloud-tint': theme.tint,
    '--cloud-mask': `url("${cloud.shape.url}")`, '--cloud-flip': cloud.flipped ? -1 : 1,
    '--cloud-text-left': `${cloud.left * 100}%`, '--cloud-text-width': `${cloud.textWidth * 100}%`,
    '--cloud-text-top': `${cloud.centerY * 100}%`,
    '--cloud-glow-x': `${(cloud.flipped ? 1 - cloud.left - cloud.textWidth / 2 : cloud.left + cloud.textWidth / 2) * 100}%`,
    '--bob-duration': `${4 + cloud.seed % 4}s`,
  }}>
    <i className="mw-cloud-shape" aria-hidden="true" />
    <span className="mw-cloud-message">{item.content}</span>
  </div>;
}
