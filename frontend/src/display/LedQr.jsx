import { useLayoutEffect, useRef, useState } from 'react';

const SQUARE_BOX = typeof CSS !== 'undefined' && typeof CSS.supports === 'function' && CSS.supports('aspect-ratio', '1');

/**
 * LED panels have no sub-pixels to hide resampling: a QR module drawn on 3.49
 * physical pixels gets blurred edges and phones fail to read it from a distance.
 * The image is generated at one pixel per module and enlarged by a whole number
 * of physical pixels with nearest-neighbour scaling.
 */
export default function LedQr({ image, alt }) {
  const ref = useRef(null);
  const [modules, setModules] = useState(0), [size, setSize] = useState(null);
  useLayoutEffect(() => {
    const box = ref.current?.parentElement;
    if (!box || !modules || typeof ResizeObserver === 'undefined') return undefined;
    const fit = () => {
      const style = getComputedStyle(box);
      const innerWidth = box.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      // Without CSS aspect-ratio (Chrome < 88, TU15Pro LED player) the box
      // height follows this image, so measuring it creates a resize feedback loop.
      const inner = SQUARE_BOX ? Math.min(innerWidth,
        box.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)) : innerWidth;
      const ratio = window.devicePixelRatio || 1;
      const scale = Math.floor(inner * ratio / modules);
      setSize(scale >= 1 ? scale * modules / ratio : null);
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(box);
    return () => observer.disconnect();
  }, [modules]);
  return <img ref={ref} className="mw-led-qr" src={image} alt={alt}
    data-px-per-module={size && modules ? Math.round(size * (window.devicePixelRatio || 1) / modules) : undefined}
    onLoad={event => setModules(event.currentTarget.naturalWidth || 0)}
    style={size ? { width: `${size}px`, height: `${size}px` } : undefined} />;
}
